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
from .sensor import _get_floor_name

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

    # Main integration hub device info (Service Hub)
    # Binary sensors attach directly here to prevent device proliferation.
    from homeassistant.helpers.device_registry import DeviceEntryType

    from .registry_helpers import get_all_areas

    hub_device_info = DeviceInfo(
        identifiers={(DOMAIN, entry.entry_id)},
        name="WiFiSense Mapper",
        manufacturer=MANUFACTURER,
        model=MODEL,
        entry_type=DeviceEntryType.SERVICE,
    )

    for floor_id in coordinator.grids:
        floor_name = _get_floor_name(hass, floor_id)
        entities.append(
            ObjectAnomalyBinarySensor(
                coordinator, entry, floor_id, floor_name, threshold, hub_device_info
            )
        )
        entities.append(
            CSIMotionBinarySensor(
                coordinator, entry, floor_id, floor_name, hub_device_info
            )
        )

    # Per-area presence sensors (one per HA area)
    for area in get_all_areas(hass):
        entities.append(
            PresenceBinarySensor(
                coordinator, entry, area.id, area.name, hub_device_info
            )
        )
        entities.append(
            WiFiSenseRFMotionBinarySensor(
                coordinator, entry, area.id, area.name, hub_device_info
            )
        )
        entities.append(
            WiFiSenseUnidentifiedPresenceBinarySensor(
                coordinator, entry, area.id, area.name, hub_device_info
            )
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


def estimate_distance_from_rssi(
    rssi: float | None, tx_power: float = -45.0, n: float = 2.5
) -> float | None:
    """Estimate distance in meters from RSSI using indoor log-distance path loss model.

    d = 10 ^ ((tx_power - rssi) / (10 * n))
    """
    if rssi is None or rssi >= 0:
        return None
    try:
        dist = 10.0 ** ((tx_power - float(rssi)) / (10.0 * n))
        return round(max(0.1, min(dist, 50.0)), 1)
    except (ValueError, TypeError, OverflowError):
        return None


class PresenceBinarySensor(WiFiSenseBaseBinary):
    """Fused WiFi presence indicator for an HA area.

    Presence is considered active if ANY of the following are true:
      a) A localized person is currently located in this area and marked home.
      b) At least one router client is associated to an AP in this area or assigned to this area.
      c) At least one CSI node in this area reports motion detected = True.

    This fused approach reduces false negatives from single-source failures.
    """

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
        self._attr_name = f"Presence {area_name}"
        self._attr_suggested_area = area_name

    @property
    def is_on(self) -> bool:
        """Return True if presence is detected in this area."""
        data = self.coordinator.data or {}

        # Source A: localized person in this area
        engine = getattr(self.coordinator, "localization_engine", None)
        trackers = engine.trackers if engine else {}
        for tracker in trackers.values():
            st = getattr(tracker, "latest_state", None) or getattr(
                tracker, "state", None
            )
            t_area = getattr(tracker, "current_area_id", None) or (
                st.area_id if st else None
            )
            is_home = (
                getattr(tracker, "is_home", True)
                if st is None
                else getattr(st, "is_home", True)
            )
            if t_area == self._area_id and is_home:
                return True

        # Source B: router client in this area
        clients = data.get("router_clients", {})
        ap_stats = data.get("ap_stats", {})
        area_ap_macs = {
            mac for mac, ap in ap_stats.items() if ap.area_id == self._area_id
        }
        if any(
            (c.ap_mac in area_ap_macs or c.area_id == self._area_id)
            for c in clients.values()
        ):
            return True

        # Source C: CSI motion detected in this area
        csi_nodes = data.get("csi_nodes", [])
        for node in csi_nodes:
            if node.area_id == self._area_id and getattr(
                node, "motion_detected_value", False
            ):
                return True

        # Source D: RF motion / unidentified presence in this area
        crosscheck = (data.get("rf_crosscheck") or {}).get(self._area_id)
        return bool(crosscheck and crosscheck.unidentified_motion)

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        data = self.coordinator.data or {}
        clients = data.get("router_clients", {})
        ap_stats = data.get("ap_stats", {})
        area_ap_macs = {
            mac for mac, ap in ap_stats.items() if ap.area_id == self._area_id
        }
        area_aps = [
            ap.name or mac
            for mac, ap in ap_stats.items()
            if ap.area_id == self._area_id
        ]
        area_clients = [
            c
            for c in clients.values()
            if (c.ap_mac in area_ap_macs or c.area_id == self._area_id)
        ]

        engine = getattr(self.coordinator, "localization_engine", None)
        trackers = engine.trackers if engine else {}
        occupants: list[str] = []
        distances: list[float] = []
        devices_detail: list[dict[str, Any]] = []

        for tracker in trackers.values():
            st = getattr(tracker, "latest_state", None) or getattr(
                tracker, "state", None
            )
            t_area = getattr(tracker, "current_area_id", None) or (
                st.area_id if st else None
            )
            is_home = (
                getattr(tracker, "is_home", True)
                if st is None
                else getattr(st, "is_home", True)
            )
            if t_area == self._area_id and is_home:
                occupants.append(tracker.person_name)
                dist = (st.distance_m if st else None) or getattr(
                    tracker, "smoothed_distance", None
                )
                if dist is not None:
                    distances.append(dist)

        for c in area_clients[:10]:
            dist = estimate_distance_from_rssi(c.rssi)
            if dist is not None:
                distances.append(dist)
            devices_detail.append(
                {
                    "mac": c.mac,
                    "name": c.hostname or c.mac,
                    "ip": c.ip,
                    "rssi": c.rssi,
                    "estimated_distance_m": dist,
                    "ap_mac": c.ap_mac,
                }
            )

        crosscheck = (data.get("rf_crosscheck") or {}).get(self._area_id)
        occupant_type = "verified" if occupants else "none"
        unidentified_motion = False
        unidentified_count = 0
        rf_corroborated_occupant = None
        if crosscheck:
            occupant_type = crosscheck.occupant_type
            unidentified_motion = crosscheck.unidentified_motion
            unidentified_count = crosscheck.unidentified_count
            rf_corroborated_occupant = crosscheck.rf_corroborated_occupant

        return {
            "area_id": self._area_id,
            "occupants": occupants,
            "occupant_count": len(occupants),
            "occupant_type": occupant_type,
            "unidentified_motion": unidentified_motion,
            "verified_occupants": occupants,
            "unidentified_count": unidentified_count,
            "rf_corroborated_occupant": rf_corroborated_occupant,
            "device_count": len(area_clients),
            "devices": [c.hostname or c.mac for c in area_clients[:10]],
            "devices_detail": devices_detail,
            "nearest_distance_m": min(distances) if distances else None,
            "active_aps": area_aps,
        }


class ObjectAnomalyBinarySensor(WiFiSenseBaseBinary):
    """Fires when spatial anomaly score exceeds the configured threshold."""

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
        self._attr_name = f"Object Anomaly {floor_name}"

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
    """Aggregated CSI motion detection across all nodes on a floor."""

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
        self._attr_name = f"CSI Motion {floor_name}"

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


class WiFiSenseRFMotionBinarySensor(WiFiSenseBaseBinary):
    """Device-free RF motion detection for an area using mesh backhaul and stationary Wi-Fi links."""

    _attr_device_class = BinarySensorDeviceClass.MOTION
    _attr_icon = "mdi:motion-sensor-wireless"
    _attr_translation_key = "rf_motion"

    def __init__(
        self,
        coordinator: WiFiSenseCoordinator,
        entry: ConfigEntry,
        area_id: str,
        area_name: str,
        device_info: DeviceInfo,
    ) -> None:
        super().__init__(coordinator, entry, f"rf_motion_{area_id}", device_info)
        self._area_id = area_id
        self._area_name = area_name
        self._attr_name = f"{area_name} RF Motion"
        self._attr_suggested_area = area_name

    @property
    def is_on(self) -> bool:
        """Return True if RF perturbation detected in this area."""
        data = self.coordinator.data or {}
        rf_snapshot = data.get("rf_sensing")
        if rf_snapshot and hasattr(rf_snapshot, "area_motion"):
            return bool(rf_snapshot.area_motion.get(self._area_id, False))
        return False

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        data = self.coordinator.data or {}
        rf_snapshot = data.get("rf_sensing")
        score = 0.0
        active_links = 0
        link_details: list[dict[str, Any]] = []

        if rf_snapshot:
            score = rf_snapshot.area_scores.get(self._area_id, 0.0)
            active_links = rf_snapshot.area_active_links.get(self._area_id, 0)
            for link in getattr(rf_snapshot, "link_states", {}).values():
                if link.get("area_id") == self._area_id:
                    link_details.append(
                        {
                            "link_id": link.get("link_id"),
                            "type": link.get("link_type"),
                            "peer_name": link.get("peer_name"),
                            "rssi": link.get("last_rssi"),
                            "baseline": link.get("baseline_mean"),
                            "variance": link.get("last_variance"),
                            "score": link.get("disturbance_score"),
                            "perturbed": link.get("is_perturbed"),
                        }
                    )

        return {
            "area_id": self._area_id,
            "area_name": self._area_name,
            "disturbance_score": score,
            "active_links_count": active_links,
            "sensitivity": self.coordinator.rf_sensitivity,
            "off_delay_sec": self.coordinator.rf_off_delay,
            "monitored_links": link_details,
        }


class WiFiSenseUnidentifiedPresenceBinarySensor(WiFiSenseBaseBinary):
    """Device-free unidentified presence detection for an area.

    Fires only when physical RF motion is detected in an area with NO verified
    family member/connected device matching the disturbance.
    """

    _attr_device_class = BinarySensorDeviceClass.MOTION
    _attr_icon = "mdi:account-question"
    _attr_translation_key = "unidentified_presence"

    def __init__(
        self,
        coordinator: WiFiSenseCoordinator,
        entry: ConfigEntry,
        area_id: str,
        area_name: str,
        device_info: DeviceInfo,
    ) -> None:
        super().__init__(
            coordinator, entry, f"unidentified_presence_{area_id}", device_info
        )
        self._area_id = area_id
        self._area_name = area_name
        self._attr_name = f"{area_name} Unidentified Presence"
        self._attr_suggested_area = area_name

    @property
    def is_on(self) -> bool:
        """Return True if an unidentified physical motion perturbation is detected."""
        data = self.coordinator.data or {}
        crosscheck = (data.get("rf_crosscheck") or {}).get(self._area_id)
        if crosscheck:
            return bool(crosscheck.unidentified_motion)
        return False

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        data = self.coordinator.data or {}
        crosscheck = (data.get("rf_crosscheck") or {}).get(self._area_id)
        score = 0.0
        active_links = 0
        occupant_type = "none"
        verified: list[str] = []
        last_unidentified_ts = None

        if crosscheck:
            score = crosscheck.disturbance_score
            active_links = crosscheck.active_links_count
            occupant_type = crosscheck.occupant_type
            verified = list(crosscheck.verified_occupants)
            last_unidentified_ts = crosscheck.last_unidentified_ts

        return {
            "area_id": self._area_id,
            "area_name": self._area_name,
            "disturbance_score": score,
            "active_links_count": active_links,
            "occupant_type": occupant_type,
            "verified_occupants": verified,
            "last_unidentified_ts": last_unidentified_ts,
            "proximity_threshold_m": getattr(
                self.coordinator, "rf_proximity_threshold_m", 3.5
            ),
            "coincidence_window_sec": getattr(
                self.coordinator, "rf_coincidence_window_s", 15.0
            ),
        }
