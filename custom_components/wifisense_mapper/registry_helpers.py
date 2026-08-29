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


def get_all_floors(hass: "HomeAssistant") -> list["FloorEntry"]:
    """Return all floors defined in Home Assistant's floor registry."""
    from homeassistant.helpers import floor_registry as fr  # noqa: PLC0415

    reg = fr.async_get(hass)
    return list(reg.floors.values())


def get_all_areas(hass: "HomeAssistant") -> list["AreaEntry"]:
    """Return all areas defined in Home Assistant's area registry."""
    from homeassistant.helpers import area_registry as ar  # noqa: PLC0415

    reg = ar.async_get(hass)
    return list(reg.areas.values())


def get_areas_for_floor(hass: "HomeAssistant", floor_id: str) -> list["AreaEntry"]:
    """Return all areas assigned to a specific floor."""
    return [a for a in get_all_areas(hass) if a.floor_id == floor_id]


def get_floor_for_area(hass: "HomeAssistant", area_id: str) -> "FloorEntry | None":
    """Return the floor that contains a given area, or None."""
    from homeassistant.helpers import area_registry as ar  # noqa: PLC0415
    from homeassistant.helpers import floor_registry as fr  # noqa: PLC0415

    area_reg = ar.async_get(hass)
    area = area_reg.async_get_area(area_id)
    if area is None or not area.floor_id:
        return None

    floor_reg = fr.async_get(hass)
    return floor_reg.async_get_floor(area.floor_id)


def suggest_area_for_node(
    node_name: str,
    areas: list["AreaEntry"],
) -> "AreaEntry | None":
    """Fuzzy-match a CSI/router node name to the most likely area.

    Uses Python's SequenceMatcher for approximate string matching.
    Returns None if no area has a similarity score above 0.4.

    This is a heuristic only — the user should always be able to
    override area assignments via the Options Flow.
    """
    if not areas or not node_name:
        return None

    name_lower = node_name.lower()
    best_match: "AreaEntry | None" = None
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
            best_match.name, node_name, best_score,
        )
    return best_match


def build_floor_area_map(
    hass: "HomeAssistant",
) -> dict[str, list["AreaEntry"]]:
    """Return a dict mapping floor_id → list of AreaEntry on that floor.

    Areas with no floor assignment are grouped under key ''.
    """
    result: dict[str, list] = {}
    for area in get_all_areas(hass):
        floor_id = area.floor_id or ""
        result.setdefault(floor_id, []).append(area)
    return result
