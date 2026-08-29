"""WiFiSense Mapper — Sensor Entities.

Exposes per-floor and per-area measurements as HA sensor entities:
  - WifiClientCountSensor  : count of associated WiFi clients in area
  - RSSISignalSensor       : average RSSI for an AP / area
  - CSIMotionScoreSensor   : aggregated CSI motion score for an area
  - AnomalyScoreSensor     : max spatial anomaly z-score for a floor
"""

from __future__ import annotations

import logging
from typing import Any

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorStateClass,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import SIGNAL_STRENGTH_DECIBELS_MILLIWATT
from homeassistant.core import HomeAssistant
from homeassistant.helpers.device_registry import DeviceEntryType, DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN, MANUFACTURER, MODEL
from .coordinator import WiFiSenseCoordinator

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up sensor entities from config entry."""
    coordinator: WiFiSenseCoordinator = hass.data[DOMAIN][entry.entry_id]["coordinator"]

    entities: list[SensorEntity] = []

    # Per-floor sensors
    for floor_id in coordinator.grids:
        floor_name = _get_floor_name(hass, floor_id)
        device_info = _floor_device_info(entry, floor_id, floor_name)

        entities.append(
            AnomalyScoreSensor(coordinator, entry, floor_id, floor_name, device_info)
        )
        entities.append(
            WifiClientCountSensor(coordinator, entry, floor_id, floor_name, device_info)
        )

    # Per-CSI-node sensors
    for node in coordinator.csi_nodes:
        if node.motion_score_entity_id:
            device_info = _node_device_info(entry, node.device_id, node.name)
            entities.append(CSIMotionScoreSensor(coordinator, entry, node, device_info))

    # Per-AP RSSI sensors
    for ap_mac, ap in coordinator.ap_stats.items():
        device_info = _ap_device_info(entry, ap_mac, ap.name or ap_mac)
        entities.append(
            RSSISignalSensor(coordinator, entry, ap_mac, ap.name or ap_mac, device_info)
        )

    async_add_entities(entities)


class WiFiSenseBaseSensor(CoordinatorEntity[WiFiSenseCoordinator], SensorEntity):
    """Base class for all WiFiSense sensor entities."""

    _attr_has_entity_name = True

    def __init__(
        self,
        coordinator: WiFiSenseCoordinator,
        entry: ConfigEntry,
        unique_suffix: str,
        device_info: DeviceInfo,
    ) -> None:
        super().__init__(coordinator)
        self._entry = entry
        self._attr_unique_id = f"{entry.entry_id}_{unique_suffix}"
        self._attr_device_info = device_info


class WifiClientCountSensor(WiFiSenseBaseSensor):
    """Number of WiFi clients associated to APs on a given floor."""

    _attr_name = "WiFi Client Count"
    _attr_icon = "mdi:wifi"
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_native_unit_of_measurement = "clients"

    def __init__(
        self,
        coordinator: WiFiSenseCoordinator,
        entry: ConfigEntry,
        floor_id: str,
        floor_name: str,
        device_info: DeviceInfo,
    ) -> None:
        super().__init__(coordinator, entry, f"client_count_{floor_id}", device_info)
        self._floor_id = floor_id
        self._floor_name = floor_name
        self._attr_name = f"{floor_name} WiFi Client Count"

    @property
    def native_value(self) -> int:
        """Return count of clients on this floor."""
        data = self.coordinator.data or {}
        clients = data.get("router_clients", {})
        ap_stats = data.get("ap_stats", {})

        # Collect AP MACs that are on this floor
        floor_ap_macs = {
            mac for mac, ap in ap_stats.items() if (ap.floor_id or "") == self._floor_id
        }

        if not floor_ap_macs:
            # If no floor assignment, return total
            return len(clients)

        return sum(1 for c in clients.values() if c.ap_mac in floor_ap_macs)


class RSSISignalSensor(WiFiSenseBaseSensor):
    """Average RSSI across all clients on a given AP."""

    _attr_device_class = SensorDeviceClass.SIGNAL_STRENGTH
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_native_unit_of_measurement = SIGNAL_STRENGTH_DECIBELS_MILLIWATT

    def __init__(
        self,
        coordinator: WiFiSenseCoordinator,
        entry: ConfigEntry,
        ap_mac: str,
        ap_name: str,
        device_info: DeviceInfo,
    ) -> None:
        super().__init__(
            coordinator, entry, f"rssi_{ap_mac.replace(':', '_')}", device_info
        )
        self._ap_mac = ap_mac
        self._attr_name = f"{ap_name} Average RSSI"

    @property
    def native_value(self) -> float | None:
        """Return average RSSI of clients on this AP."""
        data = self.coordinator.data or {}
        clients = data.get("router_clients", {})

        values = [
            c.rssi
            for c in clients.values()
            if c.ap_mac == self._ap_mac and c.rssi is not None
        ]
        if not values:
            return None
        return round(sum(values) / len(values), 1)

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        ap = self.coordinator.ap_stats.get(self._ap_mac)
        if not ap:
            return {}
        return {
            "ap_name": ap.name,
            "channel": ap.channel,
            "band": ap.band,
            "client_count": ap.client_count,
            "noise_floor": ap.noise_floor,
        }


class CSIMotionScoreSensor(WiFiSenseBaseSensor):
    """CSI motion score from an ESPectre/TOMMY node."""

    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_icon = "mdi:motion-sensor"
    _attr_native_unit_of_measurement = "score"

    def __init__(
        self,
        coordinator: WiFiSenseCoordinator,
        entry: ConfigEntry,
        node: Any,  # CSINodeInfo
        device_info: DeviceInfo,
    ) -> None:
        super().__init__(coordinator, entry, f"csi_score_{node.device_id}", device_info)
        self._node = node
        self._attr_name = f"{node.name} Motion Score"

    @property
    def native_value(self) -> float | None:
        """Return the latest CSI motion score."""
        return getattr(self._node, "motion_score_value", None)

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        return {
            "device_id": self._node.device_id,
            "platform": self._node.platform,
            "area_id": self._node.area_id,
            "floor_id": self._node.floor_id,
            "source_entity": self._node.motion_score_entity_id,
        }


class AnomalyScoreSensor(WiFiSenseBaseSensor):
    """Maximum spatial anomaly z-score for a floor."""

    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_icon = "mdi:alert-circle-outline"
    _attr_native_unit_of_measurement = "σ"

    def __init__(
        self,
        coordinator: WiFiSenseCoordinator,
        entry: ConfigEntry,
        floor_id: str,
        floor_name: str,
        device_info: DeviceInfo,
    ) -> None:
        super().__init__(coordinator, entry, f"anomaly_{floor_id}", device_info)
        self._floor_id = floor_id
        self._attr_name = f"{floor_name} Anomaly Score"

    @property
    def native_value(self) -> float | None:
        """Return the current max anomaly z-score for this floor."""
        data = self.coordinator.data or {}
        scores = data.get("anomaly_scores", {}).get(self._floor_id, {})
        bl = self.coordinator.baselines.get(self._floor_id)
        if not scores or bl is None:
            return None
        return round(bl.max_anomaly_score(scores), 2)

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        bl = self.coordinator.baselines.get(self._floor_id)
        return {
            "floor_id": self._floor_id,
            "baseline_warmed_up": bl.is_warmed_up if bl else False,
            "sample_count": bl._sample_count if bl else 0,
        }


# ─── Device info helpers ───────────────────────────────────────────────────────


def _get_floor_name(hass: HomeAssistant, floor_id: str) -> str:
    try:
        from homeassistant.helpers import floor_registry as fr

        reg = fr.async_get(hass)
        floor = reg.async_get_floor(floor_id)
        return floor.name if floor else floor_id
    except Exception:  # noqa: BLE001
        return floor_id


def _floor_device_info(
    entry: ConfigEntry, floor_id: str, floor_name: str
) -> DeviceInfo:
    return DeviceInfo(
        identifiers={(DOMAIN, f"{entry.entry_id}_{floor_id}")},
        name=f"WiFiSense — {floor_name}",
        manufacturer=MANUFACTURER,
        model=MODEL,
        entry_type=DeviceEntryType.SERVICE,
    )


def _node_device_info(entry: ConfigEntry, device_id: str, name: str) -> DeviceInfo:
    return DeviceInfo(
        identifiers={(DOMAIN, f"{entry.entry_id}_csi_{device_id}")},
        name=f"WiFiSense CSI — {name}",
        manufacturer=MANUFACTURER,
        model="ESP32 CSI Node",
    )


def _ap_device_info(entry: ConfigEntry, ap_mac: str, ap_name: str) -> DeviceInfo:
    return DeviceInfo(
        identifiers={(DOMAIN, f"{entry.entry_id}_ap_{ap_mac}")},
        name=f"WiFiSense AP — {ap_name}",
        manufacturer=MANUFACTURER,
        model="WiFi Access Point",
    )
