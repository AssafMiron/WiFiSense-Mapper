"""WiFiSense Mapper — TP-Link Deco Router Client.

Uses the ``tplinkrouterc6u`` PyPI package (TPLinkDecoClient) for
authentication and client data, which handles the AES/RSA session
auth that the Deco local web API requires.

References:
  - https://pypi.org/project/tplinkrouterc6u/
  - https://github.com/amosyuen/ha-tplink-deco (pattern reference)

Limitations:
  - Not all Deco models expose per-client RSSI via the web API;
    in that case rssi=None is returned and the coordinator falls back
    to CSI-only positioning.
  - Session tokens expire; reconnection is handled automatically.
  - Mesh roaming: a client can hop between Deco nodes between polls,
    causing apparent AP assignment changes. The coordinator smooths
    this with a short rolling window.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from .base import APStats, ClientInfo, RouterClient

_LOGGER = logging.getLogger(__name__)


class DecoClient(RouterClient):
    """TP-Link Deco client using tplinkrouterc6u."""

    def __init__(self, host: str, username: str, password: str) -> None:
        super().__init__(host, username, password)
        self._client: Any = None  # tplinkrouterc6u.TPLinkDecoClient

    async def async_connect(self) -> bool:
        """Authenticate with the Deco router.

        The tplinkrouterc6u library is synchronous, so we run it in
        an executor to avoid blocking the HA event loop.
        """
        try:
            await asyncio.get_event_loop().run_in_executor(None, self._connect_sync)
            self._connected = True
            _LOGGER.debug("Connected to Deco at %s", self.host)
            return True
        except ImportError:
            _LOGGER.error(
                "tplinkrouterc6u package not installed. "
                "Add 'tplinkrouterc6u>=4.3.0' to requirements."
            )
            return False
        except Exception as exc:  # noqa: BLE001
            _LOGGER.warning("Failed to connect to Deco at %s: %s", self.host, exc)
            self._connected = False
            return False

    def _connect_sync(self) -> None:
        """Blocking connect — runs in executor."""
        # Import is guarded to give a helpful error if package is missing.
        from tplinkrouterc6u import TPLinkDecoClient  # type: ignore[import]

        self._client = TPLinkDecoClient(self.host, self.password)
        self._client.authorize()

    async def async_get_clients(self) -> list[ClientInfo]:
        """Return connected WiFi clients from the Deco."""
        if (
            not self._connected or self._client is None
        ) and not await self.async_connect():
            return []
        try:
            raw = await asyncio.get_event_loop().run_in_executor(
                None, self._get_clients_sync
            )
            return raw
        except Exception as exc:  # noqa: BLE001
            _LOGGER.warning("Error fetching Deco clients: %s", exc)
            self._connected = False  # Force re-auth on next poll
            return []

    def _get_clients_sync(self) -> list[ClientInfo]:
        """Blocking client fetch — runs in executor."""
        result: list[ClientInfo] = []
        try:
            status = self._client.get_status()
        except Exception as exc:  # noqa: BLE001
            _LOGGER.debug("Deco get_status failed, attempting reauth: %s", exc)
            self._client.authorize()
            status = self._client.get_status()

        # tplinkrouterc6u exposes clients on status.clients
        for client in getattr(status, "clients", []) or []:
            mac_raw = getattr(client, "mac", None) or ""
            if not mac_raw:
                continue
            result.append(
                ClientInfo(
                    mac=self.normalize_mac(mac_raw),
                    ip=getattr(client, "ip", None),
                    hostname=getattr(client, "hostname", None)
                    or getattr(client, "name", None),
                    # RSSI exposure varies by Deco model — not always available
                    rssi=getattr(client, "rssi", None),
                    ap_mac=self.normalize_mac(getattr(client, "device_mac", None) or "")
                    or None,
                    ssid=getattr(client, "ssid", None),
                    band=getattr(client, "band", None),
                    extra={
                        "via_deco": True,
                        "client_type": getattr(client, "client_type", None),
                    },
                )
            )
        return result

    async def async_get_ap_stats(self) -> list[APStats]:
        """Return per-Deco-node statistics."""
        if (
            not self._connected or self._client is None
        ) and not await self.async_connect():
            return []
        try:
            return await asyncio.get_event_loop().run_in_executor(
                None, self._get_ap_stats_sync
            )

        except Exception as exc:  # noqa: BLE001
            _LOGGER.warning("Error fetching Deco AP stats: %s", exc)
            return []

    def _get_ap_stats_sync(self) -> list[APStats]:
        """Blocking AP stats fetch — runs in executor."""
        result: list[APStats] = []
        try:
            status = self._client.get_status()
        except Exception:  # noqa: BLE001
            self._client.authorize()
            status = self._client.get_status()

        for device in getattr(status, "devices", []) or []:
            mac_raw = getattr(device, "mac", None) or ""
            if not mac_raw:
                continue
            result.append(
                APStats(
                    mac=self.normalize_mac(mac_raw),
                    name=getattr(device, "name", None)
                    or getattr(device, "device_model", None),
                    channel=getattr(device, "channel", None),
                    band=getattr(device, "band", None),
                    tx_rate=getattr(device, "tx_rate", None),
                    rx_rate=getattr(device, "rx_rate", None),
                    noise_floor=getattr(device, "noise_floor", None),
                    client_count=len(
                        [
                            c
                            for c in getattr(status, "clients", [])
                            if self.normalize_mac(getattr(c, "device_mac", "") or "")
                            == self.normalize_mac(mac_raw)
                        ]
                    ),
                    extra={"via_deco": True},
                )
            )
        return result

    async def async_disconnect(self) -> None:
        """No persistent session to close for Deco."""
        self._connected = False
        self._client = None
