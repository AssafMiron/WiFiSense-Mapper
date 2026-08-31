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

        self._client = TPLinkDecoClient(
            self.host,
            self.password,
            username=self.username or "admin",
            verify_ssl=False,
        )
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

        # Build lookup table from AP name/model to AP MAC if mesh node list is cached
        node_name_to_mac: dict[str, str] = {}
        cached_nodes = getattr(self._client, "devices", []) or []
        for node in cached_nodes:
            if isinstance(node, dict):
                n_mac = node.get("mac")
                if not n_mac:
                    continue
                norm_m = self.normalize_mac(str(n_mac))
                for key in ("custom_name", "name", "device_model", "model"):
                    val = node.get(key)
                    if val:
                        node_name_to_mac[str(val).strip().lower()] = norm_m
            else:
                n_mac = getattr(node, "macaddr", None) or getattr(node, "mac", None)
                if not n_mac:
                    continue
                norm_m = self.normalize_mac(str(n_mac))
                for attr in ("custom_name", "name", "device_model"):
                    val = getattr(node, attr, None)
                    if val:
                        node_name_to_mac[str(val).strip().lower()] = norm_m

        # In tplinkrouterc6u, status.devices holds connected clients (Device objects)
        raw_devices = getattr(status, "devices", None)
        if raw_devices is None:
            raw_devices = getattr(status, "clients", []) or []

        for device in raw_devices:
            mac_raw = (
                getattr(device, "macaddr", None)
                or getattr(device, "mac", None)
                or (device.get("mac") if isinstance(device, dict) else None)
            )
            if not mac_raw:
                continue

            ip = (
                getattr(device, "ipaddr", None)
                or getattr(device, "ip", None)
                or (device.get("ip") if isinstance(device, dict) else None)
            )

            hostname = (
                getattr(device, "hostname", None)
                or getattr(device, "name", None)
                or (device.get("name") if isinstance(device, dict) else None)
            )

            rssi = (
                getattr(device, "signal", None)
                if getattr(device, "signal", None) is not None
                else getattr(device, "rssi", None)
                if getattr(device, "rssi", None) is not None
                else (
                    device.get("signal")
                    if isinstance(device, dict) and device.get("signal") is not None
                    else device.get("rssi")
                    if isinstance(device, dict)
                    else None
                )
            )

            ap_mac_raw = (
                getattr(device, "ap_name", None)
                or getattr(device, "device_mac", None)
                or getattr(device, "ap_mac", None)
                or (
                    device.get("ap_name")
                    or device.get("device_mac")
                    or device.get("ap_mac")
                    if isinstance(device, dict)
                    else None
                )
            )

            ap_mac: str | None = None
            if ap_mac_raw:
                ap_raw_str = str(ap_mac_raw).strip()
                if ap_raw_str.lower() in node_name_to_mac:
                    ap_mac = node_name_to_mac[ap_raw_str.lower()]
                else:
                    ap_mac = self.normalize_mac(ap_raw_str) or None

            ssid = (
                getattr(device, "ssid", None)
                or (device.get("ssid") if isinstance(device, dict) else None)
            )

            band = (
                getattr(device, "frequency", None)
                or getattr(device, "band", None)
                or (
                    device.get("frequency") or device.get("band")
                    if isinstance(device, dict)
                    else None
                )
            )

            conn_type = getattr(device, "type", None) or (
                device.get("type") if isinstance(device, dict) else None
            )
            if band is None and conn_type is not None:
                if hasattr(conn_type, "get_band"):
                    band_str = conn_type.get_band()
                    if band_str:
                        band = "2.4GHz" if band_str == "2G" else f"{band_str}Hz"
                elif hasattr(conn_type, "name"):
                    band = str(conn_type.name)

            extra: dict[str, Any] = {"via_deco": True}
            if hasattr(device, "down_speed") and device.down_speed is not None:
                extra["down_speed"] = device.down_speed
            if hasattr(device, "up_speed") and device.up_speed is not None:
                extra["up_speed"] = device.up_speed
            if conn_type is not None:
                extra["client_type"] = (
                    conn_type.value if hasattr(conn_type, "value") else str(conn_type)
                )
            elif (client_type := getattr(device, "client_type", None)) is not None:
                extra["client_type"] = client_type

            result.append(
                ClientInfo(
                    mac=self.normalize_mac(str(mac_raw)),
                    ip=str(ip) if ip is not None else None,
                    hostname=hostname,
                    rssi=rssi,
                    ap_mac=ap_mac,
                    ssid=ssid,
                    band=band,
                    extra=extra,
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
        deco_nodes: list[Any] = []

        try:
            if not getattr(self._client, "devices", None):
                self._client.get_firmware()
            deco_nodes = getattr(self._client, "devices", []) or []
        except Exception as exc:  # noqa: BLE001
            _LOGGER.debug(
                "Deco get_firmware/device_list failed, attempting reauth: %s", exc
            )
            try:
                self._client.authorize()
                self._client.get_firmware()
                deco_nodes = getattr(self._client, "devices", []) or []
            except Exception as auth_exc:  # noqa: BLE001
                _LOGGER.warning("Failed to fetch Deco node list: %s", auth_exc)

        # Also get status to associate client counts
        status = None
        try:
            status = self._client.get_status()
        except Exception as exc:  # noqa: BLE001
            _LOGGER.debug("Deco get_status in AP stats failed: %s", exc)

        clients: list[Any] = []
        if status:
            clients = (
                getattr(status, "devices", None)
                or getattr(status, "clients", [])
                or []
            )

        for node in deco_nodes:
            mac_raw = (
                node.get("mac")
                if isinstance(node, dict)
                else getattr(node, "macaddr", None) or getattr(node, "mac", None)
            ) or ""
            if not mac_raw:
                continue

            name = (
                node.get("custom_name")
                or node.get("name")
                or node.get("device_model")
                or node.get("model")
                if isinstance(node, dict)
                else getattr(node, "custom_name", None)
                or getattr(node, "name", None)
                or getattr(node, "device_model", None)
            )

            norm_node_mac = self.normalize_mac(str(mac_raw))

            # Count clients associated with this node
            matching_clients = 0
            for c in clients:
                c_ap_mac = (
                    getattr(c, "ap_name", None)
                    or getattr(c, "device_mac", None)
                    or getattr(c, "ap_mac", None)
                    or (
                        c.get("ap_name")
                        or c.get("device_mac")
                        or c.get("ap_mac")
                        if isinstance(c, dict)
                        else None
                    )
                )
                if c_ap_mac and (
                    self.normalize_mac(str(c_ap_mac)) == norm_node_mac
                    or bool(name and str(c_ap_mac).lower() == str(name).lower())
                ):
                    matching_clients += 1

            # If only 1 node exists in the mesh and clients have no specific ap_name,
            # attribute all connected clients to this node
            if len(deco_nodes) == 1 and matching_clients == 0 and len(clients) > 0:
                matching_clients = len(clients)

            role = (
                node.get("role")
                if isinstance(node, dict)
                else getattr(node, "role", None)
            )
            hw_ver = (
                node.get("hardware_ver")
                if isinstance(node, dict)
                else getattr(node, "hardware_ver", None)
            )
            sw_ver = (
                node.get("software_ver")
                if isinstance(node, dict)
                else getattr(node, "software_ver", None)
            )
            ip = (
                node.get("ip")
                if isinstance(node, dict)
                else getattr(node, "ipaddr", None) or getattr(node, "ip", None)
            )

            result.append(
                APStats(
                    mac=norm_node_mac,
                    name=name,
                    client_count=matching_clients,
                    extra={
                        "via_deco": True,
                        "role": role,
                        "hardware_ver": hw_ver,
                        "software_ver": sw_ver,
                        "ip": ip,
                    },
                )
            )

        # Fallback if no mesh nodes found in device_list but status has LAN MAC
        if not result and status:
            lan_mac = getattr(status, "lan_macaddr", None) or getattr(
                status, "wan_macaddr", None
            )
            if lan_mac:
                result.append(
                    APStats(
                        mac=self.normalize_mac(str(lan_mac)),
                        name="Deco Master",
                        client_count=len(clients),
                        extra={"via_deco": True, "role": "master"},
                    )
                )

        return result

    async def async_disconnect(self) -> None:
        """No persistent session to close for Deco."""
        self._connected = False
        self._client = None
