"""WiFiSense Mapper — Binary Sensor Entities.

Provides binary (on/off) signals for:
  - PresenceBinarySensor    : fused WiFi presence per area (RSSI + CSI)
  - ObjectAnomalyBinarySensor: fires when anomaly score exceeds threshold
  - CSIMotionBinarySensor   : multi-node CSI motion detection for a floor
"""

from __future__ import annotations

import logging
from typing import Any

from homeassistant.components.binary_sensor import (
    BinarySensorDeviceClass,
    BinarySensorEntity,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN, MANUFACTURER, MODEL
from .coordinator import WiFiSenseCoordinator
from .sensor import _floor_device_info, _get_floor_name

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up binary sensor entities."""
    coordinator: WiFiSenseCoordinator = hass.data[DOMAIN][entry.entry_id]["coordinator"]
    threshold = coordinator.anomaly_threshold

    entities: list[BinarySensorEntity] = []

    for floor_id in coordinator.grids:
        floor_name = _get_floor_name(hass, floor_id)
        device_info = _floor_device_info(entry, floor_id, floor_name)

        entities.append(
            ObjectAnomalyBinarySensor(
                coordinator, entry, floor_id, floor_name, threshold, device_info
            )
        )
        entities.append(
            CSIMotionBinarySensor(coordinator, entry, floor_id, floor_name, device_info)
        )

    # Per-area presence sensors (one per HA area)
    from homeassistant.helpers import area_registry as ar

    area_reg = ar.async_get(hass)
    for area in area_reg.areas.values():
        device_info = DeviceInfo(
            identifiers={(DOMAIN, f"{entry.entry_id}_area_{area.id}")},
            name=f"WiFiSense — {area.name}",
            manufacturer=MANUFACTURER,
            model=MODEL,
        )
        entities.append(
            PresenceBinarySensor(coordinator, entry, area.id, area.name, device_info)
        )

    async_add_entities(entities)


class WiFiSenseBaseBinary(CoordinatorEntity[WiFiSenseCoordinator], BinarySensorEntity):
    """Base class for WiFiSense binary sensor entities."""

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


class PresenceBinarySensor(WiFiSenseBaseBinary):
    """Fused WiFi presence indicator for an HA area.

    Presence is considered active if ANY of the following are true:
      a) At least one router client is associated to an AP in this area.
      b) At least one CSI node in this area reports motion detected = True.

    This fused approach reduces false negatives from single-source failures.
    Note: WiFi presence tracks devices, not people directly. A person
    carrying a phone is tracked; a person without a WiFi device is not.
    """

    _attr_name = "Presence"
    _attr_device_class = BinarySensorDeviceClass.PRESENCE
    _attr_icon = "mdi:account-check"

    def __init__(
        self,
        coordinator: WiFiSenseCoordinator,
        entry: ConfigEntry,
        area_id: str,
        area_name: str,
        device_info: DeviceInfo,
    ) -> None:
        super().__init__(coordinator, entry, f"presence_{area_id}", device_info)
        self._area_id = area_id
        self._area_name = area_name
        self._attr_name = f"{area_name} Presence"

    @property
    def is_on(self) -> bool:
        """Return True if presence is detected in this area."""
        data = self.coordinator.data or {}

        # Source A: router client in this area
        clients = data.get("router_clients", {})
        ap_stats = data.get("ap_stats", {})
        area_ap_macs = {
            mac for mac, ap in ap_stats.items() if ap.area_id == self._area_id
        }
        if any(c.ap_mac in area_ap_macs for c in clients.values()):
            return True

        # Source B: CSI motion detected in this area
        csi_nodes = data.get("csi_nodes", [])
        for node in csi_nodes:
            if node.area_id == self._area_id and getattr(
                node, "motion_detected_value", False
            ):
                return True

        return False

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        data = self.coordinator.data or {}
        clients = data.get("router_clients", {})
        ap_stats = data.get("ap_stats", {})
        area_ap_macs = {
            mac for mac, ap in ap_stats.items() if ap.area_id == self._area_id
        }
        area_clients = [c for c in clients.values() if c.ap_mac in area_ap_macs]
        return {
            "area_id": self._area_id,
            "device_count": len(area_clients),
            "devices": [c.hostname or c.mac for c in area_clients[:10]],  # limit to 10
        }


class ObjectAnomalyBinarySensor(WiFiSenseBaseBinary):
    """Fires when spatial anomaly score exceeds the configured threshold.

    This sensor indicates that the WiFi signal pattern on a floor has
    changed significantly from the learned baseline, which may indicate:
      - New furniture or objects placed in a room.
      - Existing furniture moved.
      - Structural changes (doors/windows left open, etc.).
      - Unusual occupancy patterns.

    Important: This is a statistical detector, not computer vision.
    Expect occasional false positives, especially:
      - During baseline learning phase (first 100+ samples).
      - After network equipment changes (new AP, channel switch).
      - During high mesh roaming activity.
    """

    _attr_device_class = BinarySensorDeviceClass.PROBLEM
    _attr_icon = "mdi:alert-outline"

    def __init__(
        self,
        coordinator: WiFiSenseCoordinator,
        entry: ConfigEntry,
        floor_id: str,
        floor_name: str,
        threshold: float,
        device_info: DeviceInfo,
    ) -> None:
        super().__init__(coordinator, entry, f"anomaly_binary_{floor_id}", device_info)
        self._floor_id = floor_id
        self._threshold = threshold
        self._attr_name = f"{floor_name} Object Anomaly"

    @property
    def is_on(self) -> bool:
        """Return True if anomaly threshold is exceeded and baseline is warmed up."""
        data = self.coordinator.data or {}
        scores = data.get("anomaly_scores", {}).get(self._floor_id, {})
        bl = self.coordinator.baselines.get(self._floor_id)
        if bl is None or not bl.is_warmed_up:
            return False
        return bl.is_anomaly(scores, self._threshold)

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        data = self.coordinator.data or {}
        scores = data.get("anomaly_scores", {}).get(self._floor_id, {})
        bl = self.coordinator.baselines.get(self._floor_id)
        anomalous = bl.anomalous_cells(scores, self._threshold) if bl else []
        max_score = bl.max_anomaly_score(scores) if bl else 0.0
        return {
            "floor_id": self._floor_id,
            "threshold": self._threshold,
            "max_anomaly_score": round(max_score, 2),
            "anomalous_cell_count": len(anomalous),
            "baseline_warmed_up": bl.is_warmed_up if bl else False,
        }


class CSIMotionBinarySensor(WiFiSenseBaseBinary):
    """Aggregated CSI motion detection across all nodes on a floor.

    Returns True if ANY CSI node on the floor reports motion detected.
    Multi-node AND logic (requiring all nodes to agree) is too conservative
    for typical home sensor densities; OR logic is preferred.
    """

    _attr_device_class = BinarySensorDeviceClass.MOTION
    _attr_icon = "mdi:motion-sensor"

    def __init__(
        self,
        coordinator: WiFiSenseCoordinator,
        entry: ConfigEntry,
        floor_id: str,
        floor_name: str,
        device_info: DeviceInfo,
    ) -> None:
        super().__init__(coordinator, entry, f"csi_motion_{floor_id}", device_info)
        self._floor_id = floor_id
        self._attr_name = f"{floor_name} CSI Motion"

    @property
    def is_on(self) -> bool:
        """Return True if any CSI node on this floor detects motion."""
        data = self.coordinator.data or {}
        csi_nodes = data.get("csi_nodes", [])
        return any(
            getattr(node, "motion_detected_value", False)
            for node in csi_nodes
            if (node.floor_id or "default") == self._floor_id
        )

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        data = self.coordinator.data or {}
        csi_nodes = data.get("csi_nodes", [])
        floor_nodes = [
            n for n in csi_nodes if (n.floor_id or "default") == self._floor_id
        ]
        return {
            "floor_id": self._floor_id,
            "node_count": len(floor_nodes),
            "active_nodes": [
                n.name
                for n in floor_nodes
                if getattr(n, "motion_detected_value", False)
            ],
        }
