"""WiFiSense Mapper — Floor & Area Registry Helpers.

Provides thin wrappers around Home Assistant's floor and area registries,
enabling the rest of the integration to work with the user's existing
organizational structure without duplicating it.

Design intent:
  - Never create floors or areas — only read and reference existing ones.
  - Let the user's existing HA configuration drive spatial organization.
  - Provide fuzzy matching to auto-suggest node → area assignments.
"""

from __future__ import annotations

import logging
from difflib import SequenceMatcher
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant
    from homeassistant.helpers.area_registry import AreaEntry
    from homeassistant.helpers.floor_registry import FloorEntry

_LOGGER = logging.getLogger(__name__)


def get_all_floors(hass: HomeAssistant) -> list[FloorEntry]:
    """Return all floors defined in Home Assistant's floor registry."""
    from homeassistant.helpers import floor_registry as fr

    reg = fr.async_get(hass)
    return list(reg.floors.values())


def get_all_areas(hass: HomeAssistant) -> list[AreaEntry]:
    """Return all areas defined in Home Assistant's area registry."""
    from homeassistant.helpers import area_registry as ar

    reg = ar.async_get(hass)
    return list(reg.areas.values())


def get_areas_for_floor(hass: HomeAssistant, floor_id: str) -> list[AreaEntry]:
    """Return all areas assigned to a specific floor."""
    return [a for a in get_all_areas(hass) if a.floor_id == floor_id]


def get_floor_for_area(hass: HomeAssistant, area_id: str) -> FloorEntry | None:
    """Return the floor that contains a given area, or None."""
    from homeassistant.helpers import area_registry as ar
    from homeassistant.helpers import floor_registry as fr

    area_reg = ar.async_get(hass)
    area = area_reg.async_get_area(area_id)
    if area is None or not area.floor_id:
        return None

    floor_reg = fr.async_get(hass)
    return floor_reg.async_get_floor(area.floor_id)


def suggest_area_for_node(
    node_name: str,
    areas: list[AreaEntry],
) -> AreaEntry | None:
    """Fuzzy-match a CSI/router node name to the most likely area.

    Uses Python's SequenceMatcher for approximate string matching.
    Returns None if no area has a similarity score above 0.4.

    This is a heuristic only — the user should always be able to
    override area assignments via the Options Flow.
    """
    if not areas or not node_name:
        return None

    name_lower = node_name.lower()
    best_match: AreaEntry | None = None
    best_score = 0.4  # Minimum similarity threshold

    for area in areas:
        area_name_lower = (area.name or "").lower()
        score = SequenceMatcher(None, name_lower, area_name_lower).ratio()
        # Also check if area name appears as a substring of node name
        if area_name_lower and area_name_lower in name_lower:
            score = max(score, 0.7)
        if score > best_score:
            best_score = score
            best_match = area

    if best_match:
        _LOGGER.debug(
            "Auto-suggested area '%s' for node '%s' (score=%.2f)",
            best_match.name,
            node_name,
            best_score,
        )
    return best_match


def build_floor_area_map(
    hass: HomeAssistant,
) -> dict[str, list[AreaEntry]]:
    """Return a dict mapping floor_id → list of AreaEntry on that floor.

    Areas with no floor assignment are grouped under key ''.
    """
    result: dict[str, list] = {}
    for area in get_all_areas(hass):
        floor_id = area.floor_id or ""
        result.setdefault(floor_id, []).append(area)
    return result


def auto_link_ap_to_ha_device(
    hass: HomeAssistant,
    ap_mac: str,
    ap_name: str | None = None,
) -> tuple[str | None, str | None]:
    """Auto-link an AP/Deco node MAC or name to Home Assistant Device and Area Registries.

    Returns:
        (area_id, floor_id) tuple or (None, None) if no link could be established.
    """
    from homeassistant.helpers import device_registry as dr

    dev_reg = dr.async_get(hass)
    norm_mac = str(ap_mac).lower().replace("-", ":").replace(".", ":")

    # 1. Search HA Device Registry by MAC address in connections or identifiers
    matched_device = None
    for device in dev_reg.devices.values():
        # Check connections
        for conn in device.connections:
            if len(conn) >= 2:
                conn_val = str(conn[1])
                if conn_val.lower().replace("-", ":").replace(".", ":") == norm_mac:
                    matched_device = device
                    break
        if matched_device:
            break

        # Check identifiers
        for ident in device.identifiers:
            if len(ident) >= 2:
                ident_val = str(ident[1])
                if ident_val.lower().replace("-", ":").replace(".", ":") == norm_mac:
                    matched_device = device
                    break
        if matched_device:
            break

    # If device is found and assigned to an Area in HA
    if matched_device and matched_device.area_id:
        area_id = matched_device.area_id
        floor = get_floor_for_area(hass, area_id)
        floor_id = floor.floor_id if floor else None
        _LOGGER.debug(
            "Auto-matched AP %s (%s) to HA Device %s in area %s",
            ap_mac,
            ap_name,
            matched_device.name,
            area_id,
        )
        return (area_id, floor_id)

    # 2. Fallback: Fuzzy match AP name to HA Area names
    all_areas = get_all_areas(hass)
    name_to_check = ap_name or (matched_device.name if matched_device else None)
    if name_to_check:
        suggested_area = suggest_area_for_node(name_to_check, all_areas)
        if suggested_area:
            floor = get_floor_for_area(hass, suggested_area.id)
            floor_id = floor.floor_id if floor else None
            _LOGGER.debug(
                "Fuzzy-matched AP %s (%s) to area %s",
                ap_mac,
                name_to_check,
                suggested_area.id,
            )
            return (suggested_area.id, floor_id)

    return (None, None)

