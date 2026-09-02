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


_GENERIC_NODE_NAMES = {
    "deco",
    "deco hub",
    "unknown",
    "null",
    "none",
    "router",
    "ap",
    "access point",
    "mesh node",
    "tplink",
    "unifi",
}


def suggest_area_for_node(
    node_name: str,
    areas: list[AreaEntry],
) -> AreaEntry | None:
    """Fuzzy-match a CSI/router node name to the most likely area.

    Uses Python's SequenceMatcher and word-token matching for approximate string matching.
    Returns None if no area has a similarity score above 0.6 or if node_name is generic.

    This is a heuristic only — the user should always be able to
    override area assignments via the Options Flow.
    """
    if not areas or not node_name:
        return None

    cleaned_name = node_name.strip().lower()
    if not cleaned_name or cleaned_name in _GENERIC_NODE_NAMES:
        return None

    # Remove common prefix/suffix words for comparison (e.g. "Bedroom Deco" -> "bedroom")
    words = [
        w
        for w in cleaned_name.split()
        if w not in ("deco", "ap", "hub", "node", "wifi", "wifisense", "router")
    ]
    core_name = " ".join(words) if words else cleaned_name

    best_match: AreaEntry | None = None
    best_score = 0.6  # Minimum similarity threshold

    for area in areas:
        area_name_raw = area.name or ""
        area_name_lower = area_name_raw.strip().lower()
        if not area_name_lower:
            continue

        area_words = [
            w for w in area_name_lower.split() if w not in ("room", "the", "area")
        ]
        core_area = " ".join(area_words) if area_words else area_name_lower

        # Exact match of core names
        if (
            core_name == core_area
            or core_name == area_name_lower
            or cleaned_name == area_name_lower
        ):
            score = 1.0
        # Substring / token inclusion
        elif (
            len(core_name) >= 3
            and (core_name in area_name_lower or core_name in core_area)
        ) or (
            len(core_area) >= 3
            and (core_area in cleaned_name or core_area in core_name)
        ):
            score = 0.85
        elif any(len(w) >= 3 and w in area_words for w in words):
            score = 0.75
        else:
            score = SequenceMatcher(None, core_name, core_area).ratio()

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
    matched_device: dr.DeviceEntry | None = None

    devices = dev_reg.devices.values() if hasattr(dev_reg.devices, "values") else dev_reg.devices  # type: ignore[union-attr]
    for device in devices:
        if isinstance(device, str):
            continue
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

    all_areas = get_all_areas(hass)
    effective_name = ap_name or (
        (matched_device.name_by_user or matched_device.name) if matched_device else None
    )

    # 1. Prioritize room name matching from device name (e.g. "Bedroom Deco" -> "bedroom" area)
    # This prevents blindly inheriting a default integration-level area like "office".
    if effective_name:
        suggested_area = suggest_area_for_node(effective_name, all_areas)
        if suggested_area:
            floor = get_floor_for_area(hass, suggested_area.id)
            floor_id = floor.floor_id if floor else None
            _LOGGER.debug(
                "Name-matched AP %s (%s) to area %s",
                ap_mac,
                effective_name,
                suggested_area.id,
            )
            return (suggested_area.id, floor_id)

    # 2. If device is found and assigned to an Area in HA
    # Only inherit matched_device.area_id if ap_name was provided or matched_device name is non-generic
    if matched_device and matched_device.area_id:
        # Check that matched_device has a valid non-empty area_id
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

    return (None, None)


def async_sync_device_area(
    hass: HomeAssistant,
    ap_mac: str,
    area_id: str,
    overwrite: bool = False,
) -> bool:
    """Synchronize an AP/node's area assignment back to Home Assistant's Device Registry.

    If the device's area_id in HA is blank, it is automatically updated.
    If already assigned, it is only updated if overwrite=True.
    """
    from homeassistant.helpers import device_registry as dr

    dev_reg = dr.async_get(hass)
    norm_mac = str(ap_mac).lower().replace("-", ":").replace(".", ":")

    devices = (
        dev_reg.devices.values()
        if hasattr(dev_reg.devices, "values")
        else dev_reg.devices
    )
    for device in devices:
        if isinstance(device, str):
            continue
        matched = False
        for conn in device.connections:
            if (
                len(conn) >= 2
                and str(conn[1]).lower().replace("-", ":").replace(".", ":") == norm_mac
            ):
                matched = True
                break
        if not matched:
            for ident in device.identifiers:
                if (
                    len(ident) >= 2
                    and str(ident[1]).lower().replace("-", ":").replace(".", ":") == norm_mac
                ):
                    matched = True
                    break

        if matched:
            if not device.area_id or overwrite or device.area_id == area_id:
                if device.area_id != area_id:
                    dev_reg.async_update_device(device.id, area_id=area_id)
                    _LOGGER.info(
                        "Updated HA Device Registry: Device '%s' (%s) area set to '%s'",
                        device.name,
                        ap_mac,
                        area_id,
                    )
                return True
            _LOGGER.debug(
                "Device '%s' already has area '%s'; not overwriting without explicit consent",
                device.name,
                device.area_id,
            )
            return False

    return False


def get_area_name_from_id(hass: HomeAssistant, area_id: str | None) -> str:
    """Return the friendly name for an area ID or fallback."""
    if not area_id:
        return "Home"
    try:
        from homeassistant.helpers import area_registry as ar

        reg = ar.async_get(hass)
        area = reg.async_get_area(area_id)
        return area.name if area and area.name else area_id
    except Exception:  # noqa: BLE001
        return area_id


def get_floor_name_from_id(hass: HomeAssistant, floor_id: str | None) -> str:
    """Return the friendly name for a floor ID or fallback."""
    if not floor_id or floor_id == "default":
        return "Ground Floor"
    try:
        from homeassistant.helpers import floor_registry as fr

        reg = fr.async_get(hass)
        floor = reg.async_get_floor(floor_id)
        return floor.name if floor and floor.name else floor_id
    except Exception:  # noqa: BLE001
        return floor_id

