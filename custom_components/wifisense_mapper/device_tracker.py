"""WiFiSense Mapper — Device Tracker Entities.

Provides rough room-level device tracking for WiFi clients.

Position estimate strategy (in priority order):
  1. AP area assignment: if the client's associated AP has an area
     assigned, use that area as the client's location.
  2. CSI triangulation: if multiple CSI nodes see motion correlated
     with client RSSI, use the strongest CSI node's area.
  3. Floor-level fallback: if only floor is known, report the floor name.
  4. "not_home": if the client is not currently associated.

Accuracy notes:
  - Room-level WiFi tracking has ~2–8 meter accuracy in typical homes.
  - It tracks DEVICES (phones, laptops), not people directly.
  - A device in standby (no active WiFi traffic) may show stale data
    or roam between APs without triggering a position update.
  - Do not rely on this for security-critical presence detection.
"""

from __future__ import annotations

import logging
from typing import Any

from homeassistant.components.device_tracker import (
    TrackerEntity,
)
from homeassistant.components.device_tracker.const import SourceType
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN, MANUFACTURER
from .coordinator import WiFiSenseCoordinator

_LOGGER = logging.getLogger(__name__)

# Maximum number of device tracker entities to create
# (prevents entity explosion on large networks)
MAX_TRACKED_DEVICES = 50


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up device tracker entities for known WiFi clients."""
    coordinator: WiFiSenseCoordinator = hass.data[DOMAIN][entry.entry_id]["coordinator"]

    # Create trackers for clients already known at setup time
    entities: list[TrackerEntity] = []
    seen_macs = set()

    for mac, client in list(coordinator.router_clients.items())[:MAX_TRACKED_DEVICES]:
        if mac in seen_macs:
            continue
        seen_macs.add(mac)
        device_info = DeviceInfo(
            identifiers={(DOMAIN, f"{entry.entry_id}_tracker_{mac}")},
            name=client.hostname or f"Device {mac}",
            manufacturer=MANUFACTURER,
            model="WiFi Client",
        )
        entities.append(WifiSenseDeviceTracker(coordinator, entry, mac, device_info))

    async_add_entities(entities)

    # TODO: In a future version, register a coordinator listener to
    # dynamically add trackers for newly-seen clients via
    # async_add_entities(new_entities, update_before_add=True)


class WifiSenseDeviceTracker(CoordinatorEntity[WiFiSenseCoordinator], TrackerEntity):
    """Room-level WiFi device tracker.

    State: area name (room) or "not_home" if not associated.
    Source type: router (accurate to AP level, not GPS).
    """

    _attr_has_entity_name = True
    _attr_source_type = SourceType.ROUTER

    def __init__(
        self,
        coordinator: WiFiSenseCoordinator,
        entry: ConfigEntry,
        mac: str,
        device_info: DeviceInfo,
    ) -> None:
        super().__init__(coordinator)
        self._mac = mac
        self._entry = entry
        self._attr_unique_id = f"{entry.entry_id}_tracker_{mac}"
        self._device_info = device_info

    @property
    def device_info(self) -> DeviceInfo:
        return self._device_info

    @property
    def name(self) -> str:
        client = self.coordinator.router_clients.get(self._mac)
        return (
            client.hostname or f"Device {self._mac}"
            if client
            else f"Device {self._mac}"
        )

    @property
    def is_connected(self) -> bool:
        """Return True if the device is currently associated."""
        return self._mac in self.coordinator.router_clients

    @property
    def latitude(self) -> float | None:
        return None  # WiFi tracking doesn't provide GPS coordinates

    @property
    def longitude(self) -> float | None:
        return None

    @property
    def location_name(self) -> str | None:
        """Return the area/room name for this device."""
        client = self.coordinator.router_clients.get(self._mac)
        if client is None:
            return "not_home"

        # Priority 1: client's direct area assignment
        if client.area_id:
            return self._area_name(client.area_id)

        # Priority 2: AP's area assignment
        if client.ap_mac:
            ap = self.coordinator.ap_stats.get(client.ap_mac)
            if ap and ap.area_id:
                return self._area_name(ap.area_id)
            if ap and ap.name:
                return ap.name  # Fall back to AP name if no area

        # Priority 3: floor name
        if client.floor_id:
            return self._floor_name(client.floor_id)

        return "home"  # Connected but location unknown

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        client = self.coordinator.router_clients.get(self._mac)
        if not client:
            return {"mac": self._mac}
        return {
            "mac": self._mac,
            "ip": client.ip,
            "ssid": client.ssid,
            "band": client.band,
            "rssi": client.rssi,
            "ap_mac": client.ap_mac,
            "ap_name": self.coordinator.ap_stats.get(client.ap_mac or "", None)
            and getattr(
                self.coordinator.ap_stats.get(client.ap_mac or ""), "name", None
            ),
        }

    def _area_name(self, area_id: str) -> str:
        try:
            from homeassistant.helpers import area_registry as ar

            reg = ar.async_get(self.hass)
            area = reg.async_get_area(area_id)
            return area.name if area else area_id
        except Exception:  # noqa: BLE001
            return area_id

    def _floor_name(self, floor_id: str) -> str:
        try:
            from homeassistant.helpers import floor_registry as fr

            reg = fr.async_get(self.hass)
            floor = reg.async_get_floor(floor_id)
            return floor.name if floor else floor_id
        except Exception:  # noqa: BLE001
            return floor_id
