"""WiFiSense Mapper — CSI Node Discovery.

Scans the Home Assistant entity and device registries to discover
ESPectre and TOMMY-based ESP32 CSI sensing nodes without requiring
the user to manually specify entity IDs.

Discovery strategy (in priority order):
  1. ESPHome platform: look for entities whose names match known
     CSI score/motion patterns (motion_score, movement_score, etc.)
     on the ``esphome`` platform.
  2. MQTT platform: look for entities whose MQTT topics match
     known CSI project identifiers (espectre, tommy, espwifi).

Multi-node support:
  Entities are grouped by device_id so that multiple entities
  from the same physical node are returned as a single CSINodeInfo.
  This grouping enables multi-node triangulation in the spatial engine.

Limitations:
  - CSI-based presence detection is highly dependent on node placement,
    antenna orientation, and the 802.11 activity level in the area.
  - Multi-path reflections can cause false positives. The baseline
    learner helps filter persistent noise from genuine changes.
  - Mesh roaming and channel switches on nearby APs can temporarily
    affect CSI readings on ESP32 nodes.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from .const import (
    CSI_MOTION_DETECTED_PATTERNS,
    CSI_MOTION_SCORE_PATTERNS,
    CSI_MQTT_TOPIC_PATTERNS,
)

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant

_LOGGER = logging.getLogger(__name__)


@dataclass
class CSINodeInfo:
    """Information about a discovered CSI sensing node."""

    device_id: str
    """HA device registry ID grouping all entities for this node."""

    platform: str
    """Platform the node was discovered on ('esphome' or 'mqtt')."""

    name: str = ""
    """Human-readable name of the device."""

    area_id: str | None = None
    """HA area the node is physically in (if assigned in device registry)."""

    floor_id: str | None = None
    """HA floor the node is on (derived from area's floor)."""

    motion_score_entity_id: str | None = None
    """Entity ID providing a continuous motion/movement score (float)."""

    motion_detected_entity_id: str | None = None
    """Entity ID providing a binary motion/presence detection signal."""

    presence_entity_id: str | None = None
    """Entity ID for a presence binary sensor (may differ from motion_detected)."""

    all_entity_ids: list[str] = field(default_factory=list)
    """All entity IDs associated with this CSI device."""


def discover_csi_nodes(hass: HomeAssistant) -> list[CSINodeInfo]:
    """Scan registries and return discovered CSI nodes.

    This is a synchronous helper that reads from in-memory registries;
    it is safe to call from within the HA event loop.
    """
    from homeassistant.helpers import device_registry as dr
    from homeassistant.helpers import entity_registry as er

    ent_reg = er.async_get(hass)
    dev_reg = dr.async_get(hass)

    # Compile regex patterns once
    score_re = [re.compile(p, re.IGNORECASE) for p in CSI_MOTION_SCORE_PATTERNS]
    motion_re = [re.compile(p, re.IGNORECASE) for p in CSI_MOTION_DETECTED_PATTERNS]
    mqtt_topic_re = [re.compile(p, re.IGNORECASE) for p in CSI_MQTT_TOPIC_PATTERNS]

    # Group entities by device_id → build CSINodeInfo per device
    device_nodes: dict[str, CSINodeInfo] = {}

    for entry in ent_reg.entities.values():
        if entry.platform not in ("esphome", "mqtt"):
            continue

        node_name = entry.name or entry.original_name or entry.entity_id
        device_id = entry.device_id

        # For MQTT entities, check topic patterns if device_id missing
        if (
            entry.platform == "mqtt"
            and not device_id
            and not any(p.search(entry.unique_id or "") for p in mqtt_topic_re)
            and not any(p.search(entry.entity_id) for p in mqtt_topic_re)
        ):
            continue
        if entry.platform == "mqtt" and not device_id:
            # Use unique_id as a surrogate device key
            device_id = f"mqtt__{entry.unique_id or entry.entity_id}"

        if not device_id:
            continue

        # Initialise node record
        if device_id not in device_nodes:
            device = dev_reg.async_get(device_id) if entry.device_id else None
            node = CSINodeInfo(
                device_id=device_id,
                platform=entry.platform,
                name=(device.name if device else node_name) or node_name,
                area_id=entry.area_id or (device.area_id if device else None),
            )
            device_nodes[device_id] = node
        else:
            node = device_nodes[device_id]

        node.all_entity_ids.append(entry.entity_id)

        # Classify entity role
        entity_name_lower = (entry.name or entry.original_name or "").lower()
        entity_id_lower = entry.entity_id.lower()

        if any(
            p.search(entity_name_lower) or p.search(entity_id_lower) for p in score_re
        ):
            node.motion_score_entity_id = entry.entity_id
            _LOGGER.debug(
                "CSI score entity found: %s (device: %s)", entry.entity_id, node.name
            )
        elif any(
            p.search(entity_name_lower) or p.search(entity_id_lower) for p in motion_re
        ):
            if "presence" in entity_name_lower or "presence" in entity_id_lower:
                node.presence_entity_id = entry.entity_id
            else:
                node.motion_detected_entity_id = entry.entity_id
            _LOGGER.debug(
                "CSI motion/presence entity found: %s (device: %s)",
                entry.entity_id,
                node.name,
            )

    # Filter: keep only nodes that have at least one useful CSI entity
    useful_nodes = [
        n
        for n in device_nodes.values()
        if n.motion_score_entity_id
        or n.motion_detected_entity_id
        or n.presence_entity_id
    ]

    _LOGGER.info("CSI discovery complete: found %d node(s)", len(useful_nodes))
    return useful_nodes
