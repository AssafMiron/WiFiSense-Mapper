"""WiFiSense Mapper — Vacuum Map Integration Helpers.

Discovers and consumes robot vacuum map entities to provide an
optional spatial validation and alignment layer for WiFi heatmaps.

Design principles:
  - Vacuum maps are a SECONDARY source for layout geometry, not a
    primary sensing mechanism. They are used to:
      • Provide room boundaries and obstacle geometry.
      • Validate that WiFi anomaly detections align with real room edges.
      • Cross-check object detection against cleaned vs. uncleaned zones.
  - Never require a vacuum to be present — all vacuum helpers degrade
    gracefully to no-op when no vacuum entities are found.
  - Support: Roborock (core integration), Valetudo/mqtt_vacuum_camera,
    Dreame, and any integration exposing image.* or camera.* map entities.

Multi-floor notes:
  - Some vacuums support multiple saved maps (one per floor).
  - When available, we read the current active map and attempt to
    match it to the corresponding HA Floor by name similarity.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant

from .const import VACUUM_PLATFORMS

_LOGGER = logging.getLogger(__name__)


@dataclass
class VacuumRoomSegment:
    """A room/segment as reported by the vacuum."""

    segment_id: str | int
    """Vacuum-internal segment identifier."""

    name: str | None = None
    """Human-readable room name (e.g., 'Living Room')."""

    area_id: str | None = None
    """Matched HA area_id (if auto-matched or user-configured)."""


@dataclass
class VacuumMapSource:
    """A discovered vacuum map entity and associated metadata."""

    entity_id: str
    """HA entity ID of the image/camera entity."""

    platform: str
    """HA integration platform that owns this entity."""

    map_name: str | None = None
    """Human-readable name of this map (e.g., 'Ground Floor')."""

    floor_id: str | None = None
    """Matched HA floor_id (if determinable)."""

    room_segments: list[VacuumRoomSegment] = field(default_factory=list)
    """Room/segment data from the vacuum, if available."""

    last_map_bytes: bytes | None = None
    """Raw PNG bytes of the most recently fetched map image."""

    extra: dict[str, Any] = field(default_factory=dict)


def discover_vacuum_maps(
    hass: HomeAssistant, additional_entity_ids: list[str] | None = None
) -> list[VacuumMapSource]:
    """Scan entity registry and state machine for vacuum entities and map images.

    Returns VacuumMapSource objects with discovered room segments.
    """
    from homeassistant.helpers import entity_registry as er

    ent_reg = er.async_get(hass)
    found: list[VacuumMapSource] = []
    seen_entities: set[str] = set()

    # 1. Registry scan
    for entry in ent_reg.entities.values():
        if entry.domain not in ("vacuum", "image", "camera"):
            continue

        is_vac_domain = entry.domain == "vacuum"
        is_vac_platform = entry.platform in VACUUM_PLATFORMS
        name_lower = (entry.name or entry.original_name or entry.entity_id).lower()
        uid_lower = (entry.unique_id or "").lower()
        is_map_entity = "map" in name_lower or "map" in uid_lower or "floor" in name_lower

        if not is_vac_domain and not is_vac_platform and not is_map_entity:
            continue

        state = hass.states.get(entry.entity_id)
        attrs = state.attributes if state else {}

        segments = _extract_segments(attrs)

        # Fallback: check related vacuum/image entities on the same device
        if not segments and entry.device_id:
            for other_entry in ent_reg.entities.values():
                if (
                    other_entry.device_id == entry.device_id
                    and other_entry.entity_id != entry.entity_id
                ):
                    other_state = hass.states.get(other_entry.entity_id)
                    if other_state:
                        segments = _extract_segments(other_state.attributes)
                        if segments:
                            break

        seen_entities.add(entry.entity_id)
        found.append(
            VacuumMapSource(
                entity_id=entry.entity_id,
                platform=entry.platform,
                map_name=attrs.get("map_name")
                or attrs.get("friendly_name")
                or entry.name
                or entry.original_name
                or entry.entity_id,
                room_segments=segments,
                extra={
                    "device_id": entry.device_id,
                    "area_id": entry.area_id,
                },
            )
        )

    # 2. Check all states in vacuum domain directly in case some aren't in entity registry
    for eid in hass.states.async_entity_ids("vacuum"):
        if eid in seen_entities:
            continue
        state = hass.states.get(eid)
        if not state:
            continue
        segments = _extract_segments(state.attributes)
        seen_entities.add(eid)
        found.append(
            VacuumMapSource(
                entity_id=eid,
                platform="vacuum",
                map_name=state.attributes.get("friendly_name") or eid,
                room_segments=segments,
                extra={},
            )
        )

    # 3. Check explicitly configured vacuum entities
    if additional_entity_ids:
        for eid in additional_entity_ids:
            if eid in seen_entities:
                continue
            state = hass.states.get(eid)
            if not state:
                continue
            segments = _extract_segments(state.attributes)
            seen_entities.add(eid)
            found.append(
                VacuumMapSource(
                    entity_id=eid,
                    platform=eid.split(".")[0],
                    map_name=state.attributes.get("friendly_name") or eid,
                    room_segments=segments,
                    extra={},
                )
            )

    _LOGGER.info("Vacuum map discovery complete: found %d vacuum/map entity/ies", len(found))
    return found


def _extract_segments(attrs: dict[str, Any]) -> list[VacuumRoomSegment]:
    """Parse room segments from state attributes.

    Supports diverse robot vacuum attribute schemas:
      - ``rooms`` / ``room_list`` / ``rooms_list``: list of dicts with id + name
      - ``segments`` / ``segment_list``: dict or list mapping segment_id → name
      - ``room_mapping`` / ``map_rooms`` / ``custom_rooms`` / ``room_names``
    """
    import json

    segments: list[VacuumRoomSegment] = []
    seen_ids: set[str] = set()

    def _add_segment(seg_id: Any, seg_name: Any = None) -> None:
        if seg_id is None:
            return
        s_id = str(seg_id).strip()
        if not s_id or s_id in seen_ids:
            return
        seen_ids.add(s_id)
        name_str = str(seg_name).strip() if seg_name else f"Room {s_id}"
        segments.append(VacuumRoomSegment(segment_id=s_id, name=name_str))

    # Look through all potential room attribute keys
    for key in (
        "rooms",
        "room_list",
        "rooms_list",
        "segments",
        "segment_list",
        "room_mapping",
        "map_rooms",
        "custom_rooms",
        "room_names",
        "selected_rooms",
        "cleaned_rooms",
    ):
        val = attrs.get(key)
        if not val:
            continue

        if isinstance(val, str):
            try:
                val = json.loads(val)
            except (json.JSONDecodeError, ValueError, TypeError):
                pass

        # List of room dictionaries: [{"id": 16, "name": "Kitchen"}, ...]
        if isinstance(val, list):
            for idx, item in enumerate(val):
                if isinstance(item, dict):
                    sid = (
                        item.get("id")
                        or item.get("segment_id")
                        or item.get("room_id")
                        or item.get("key")
                    )
                    sname = (
                        item.get("name")
                        or item.get("room_name")
                        or item.get("label")
                        or item.get("room")
                    )
                    _add_segment(sid, sname)
                elif isinstance(item, (str, int)):
                    _add_segment(str(item), str(item))

        # Dictionary of room id -> name or name -> id: {"16": "Kitchen", ...}
        elif isinstance(val, dict):
            for k, v in val.items():
                if isinstance(v, dict):
                    sid = v.get("id") or v.get("segment_id") or k
                    sname = v.get("name") or v.get("room_name") or str(k)
                    _add_segment(sid, sname)
                elif isinstance(v, (str, int)):
                    # Check if key is numeric ID (standard) or name
                    if str(k).isdigit() or len(str(k)) <= 4:
                        _add_segment(k, str(v))
                    else:
                        _add_segment(str(v), str(k))

    return segments


async def async_fetch_map_image(hass: HomeAssistant, entity_id: str) -> bytes | None:
    """Fetch the current map image bytes from an image/camera entity.

    Returns raw PNG bytes or None on failure.
    The returned image is passed to the spatial engine for alignment;
    it is NOT stored in entity state — only the rendered heatmap overlay is.
    """
    import asyncio

    state = hass.states.get(entity_id)
    if state is None:
        _LOGGER.debug("Vacuum map entity %s not found in state machine", entity_id)
        return None

    domain = entity_id.split(".")[0]

    try:
        if domain == "image":
            # HA image platform: use async_get_image service
            from homeassistant.components.image import (
                async_get_still_stream,  # noqa: F401
            )

            # Fallback: read from state attributes if image_url is exposed
            img_url = state.attributes.get("entity_picture") or state.attributes.get(
                "image_url"
            )
            if img_url:
                import aiohttp

                async with (
                    aiohttp.ClientSession() as session,
                    session.get(
                        f"http://localhost:8123{img_url}",
                        timeout=aiohttp.ClientTimeout(total=10),
                    ) as resp,
                ):
                    if resp.status == 200:
                        return await resp.read()

        elif domain == "camera":
            from homeassistant.components.camera import async_get_image

            image = await async_get_image(hass, entity_id, timeout=10)
            return image.content if image else None
    except asyncio.TimeoutError:
        _LOGGER.warning("Timeout fetching map image from %s", entity_id)
    except Exception as exc:  # noqa: BLE001
        _LOGGER.warning("Error fetching map image from %s: %s", entity_id, exc)

    return None
