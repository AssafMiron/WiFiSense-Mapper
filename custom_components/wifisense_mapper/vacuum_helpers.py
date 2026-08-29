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


def discover_vacuum_maps(hass: HomeAssistant) -> list[VacuumMapSource]:
    """Scan entity registry for vacuum map image/camera entities.

    Returns one VacuumMapSource per discovered map entity.
    Room segments are read from entity state attributes where available.
    """
    from homeassistant.helpers import entity_registry as er

    ent_reg = er.async_get(hass)
    found: list[VacuumMapSource] = []

    for entry in ent_reg.entities.values():
        if entry.platform not in VACUUM_PLATFORMS:
            continue
        if entry.domain not in ("image", "camera"):
            continue

        # Look for "map" in the entity name or unique_id as a heuristic
        name_lower = (entry.name or entry.original_name or entry.entity_id).lower()
        uid_lower = (entry.unique_id or "").lower()
        if "map" not in name_lower and "map" not in uid_lower:
            continue

        state = hass.states.get(entry.entity_id)
        attrs = state.attributes if state else {}

        # Extract room segments from state attributes (Roborock / Valetudo style)
        segments = _extract_segments(attrs)

        source = VacuumMapSource(
            entity_id=entry.entity_id,
            platform=entry.platform,
            map_name=attrs.get("map_name")
            or attrs.get("friendly_name")
            or entry.entity_id,
            room_segments=segments,
            extra={
                "device_id": entry.device_id,
                "area_id": entry.area_id,
            },
        )
        found.append(source)
        _LOGGER.debug(
            "Vacuum map entity discovered: %s (platform=%s, segments=%d)",
            entry.entity_id,
            entry.platform,
            len(segments),
        )

    _LOGGER.info("Vacuum map discovery complete: found %d map entity/ies", len(found))
    return found


def _extract_segments(attrs: dict[str, Any]) -> list[VacuumRoomSegment]:
    """Parse room segments from state attributes.

    Supports the Roborock/Valetudo attribute formats:
      - ``rooms``: list of dicts with id + name
      - ``segments``: dict mapping segment_id → name
      - ``room_list``: list of {id, name} dicts
    """
    segments: list[VacuumRoomSegment] = []

    # Roborock core integration: attrs['rooms'] = [{id, name, ...}]
    rooms_raw = attrs.get("rooms") or attrs.get("room_list", [])
    if isinstance(rooms_raw, list):
        for room in rooms_raw:
            if isinstance(room, dict):
                seg_id = room.get("id") or room.get("segment_id") or room.get("room_id")
                seg_name = room.get("name") or room.get("room_name")
                if seg_id is not None:
                    segments.append(VacuumRoomSegment(segment_id=seg_id, name=seg_name))

    # Valetudo / mqtt_vacuum_camera: attrs['segments'] = {"1": "Kitchen", ...}
    segs_raw = attrs.get("segments")
    if isinstance(segs_raw, dict):
        for seg_id, seg_name in segs_raw.items():
            segments.append(
                VacuumRoomSegment(
                    segment_id=seg_id,
                    name=seg_name if isinstance(seg_name, str) else str(seg_name),
                )
            )

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
