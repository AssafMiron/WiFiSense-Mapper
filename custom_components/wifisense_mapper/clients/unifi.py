"""WiFiSense Mapper — UniFi Router Client.

Design principle: bridge through the existing HA ``unifi`` integration's
entity states rather than duplicating credentials or implementing a
separate UniFi controller API client.

This avoids:
  - Re-authentication / credential duplication.
  - Breaking when the UniFi controller updates its API.
  - Conflicting with HA's official UniFi integration.

How it works:
  1. We scan the entity registry for ``device_tracker.*`` entities
     belonging to the ``unifi`` platform.
  2. Each tracker entity represents a known WiFi client; we read its
     state (home/not_home/room) and attributes (ip, mac, ssid, etc.)
     from ``hass.states``.
  3. AP-level stats (if available) are read from ``sensor.*`` entities
     on the same UniFi platform.

If the unifi integration is not loaded, this client returns empty lists
and logs a warning — it does NOT fall back to direct HTTP to avoid
credential duplication.
"""
from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from .base import APStats, ClientInfo, RouterClient

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant

_LOGGER = logging.getLogger(__name__)


class UniFiClient(RouterClient):
    """Reads UniFi client data by bridging the existing unifi HA integration."""

    def __init__(self, hass: "HomeAssistant") -> None:
        # No credentials needed — we read from HA state machine
        super().__init__(host="", username="", password="")
        self._hass = hass

    async def async_connect(self) -> bool:
        """Check that the unifi integration is loaded."""
        from homeassistant.loader import async_get_loaded_integrations  # noqa: PLC0415

        loaded = async_get_loaded_integrations(self._hass)
        if "unifi" not in loaded:
            _LOGGER.warning(
                "UniFi integration not loaded. WiFiSense Mapper will not receive "
                "UniFi client data. Install and configure the UniFi integration first."
            )
            self._connected = False
            return False
        self._connected = True
        _LOGGER.debug("UniFi bridge active — reading from HA entity states.")
        return True

    async def async_get_clients(self) -> list[ClientInfo]:
        """Return WiFi clients by reading unifi device_tracker entities."""
        if not self._connected:
            return []

        result: list[ClientInfo] = []
        entity_registry = self._hass.helpers.entity_registry.async_get(self._hass)  # type: ignore[attr-defined]

        for entry in entity_registry.entities.values():
            if entry.platform != "unifi" or entry.domain != "device_tracker":
                continue
            state = self._hass.states.get(entry.entity_id)
            if state is None:
                continue

            attrs = state.attributes
            mac_raw = attrs.get("mac") or entry.unique_id or ""
            if not mac_raw:
                continue

            result.append(
                ClientInfo(
                    mac=self.normalize_mac(mac_raw),
                    ip=attrs.get("ip"),
                    hostname=attrs.get("hostname") or attrs.get("friendly_name"),
                    # UniFi tracker entities may include rssi in attributes
                    rssi=attrs.get("rssi") or attrs.get("signal"),
                    ap_mac=self.normalize_mac(attrs.get("ap_mac", "") or "")
                    or None,
                    ssid=attrs.get("ssid") or attrs.get("network"),
                    band=attrs.get("band"),
                    extra={
                        "via_unifi": True,
                        "is_home": state.state == "home",
                        "entity_id": entry.entity_id,
                        "vlan": attrs.get("vlan"),
                    },
                )
            )

        _LOGGER.debug("UniFi bridge returned %d clients", len(result))
        return result

    async def async_get_ap_stats(self) -> list[APStats]:
        """Return AP stats by reading unifi sensor entities (if available).

        UniFi exposes per-AP sensor entities for some controller versions;
        we read them opportunistically and return empty list if absent.
        """
        if not self._connected:
            return []

        result: list[APStats] = []
        entity_registry = self._hass.helpers.entity_registry.async_get(self._hass)  # type: ignore[attr-defined]
        device_registry = self._hass.helpers.device_registry.async_get(self._hass)  # type: ignore[attr-defined]

        # Collect sensor entities from the unifi platform that look AP-like
        ap_entities: dict[str, list] = {}
        for entry in entity_registry.entities.values():
            if entry.platform != "unifi" or entry.domain != "sensor":
                continue
            if entry.device_id:
                ap_entities.setdefault(entry.device_id, []).append(entry)

        for device_id, entries in ap_entities.items():
            device = device_registry.async_get(device_id)
            if device is None:
                continue

            # Extract MAC from device identifiers (unifi uses MAC as identifier)
            mac_raw = ""
            for domain, identifier in device.identifiers:
                if domain == "unifi":
                    mac_raw = identifier
                    break
            if not mac_raw:
                continue

            ap = APStats(
                mac=self.normalize_mac(mac_raw),
                name=device.name,
                extra={"via_unifi": True, "device_id": device_id},
            )
            # Pull channel/client count from sensor states
            for entry in entries:
                state = self._hass.states.get(entry.entity_id)
                if state is None:
                    continue
                name_lower = (entry.name or entry.entity_id).lower()
                try:
                    val = float(state.state)
                except ValueError:
                    continue
                if "channel" in name_lower:
                    ap.channel = int(val)
                elif "client" in name_lower:
                    ap.client_count = int(val)
                elif "noise" in name_lower:
                    ap.noise_floor = int(val)

            result.append(ap)

        return result

    async def async_disconnect(self) -> None:
        """No resources to clean up — we only read HA state machine."""
        self._connected = False
