"""Tests for Roborock and robot vacuum semantic map parser."""

from __future__ import annotations

import io

from PIL import Image

from custom_components.wifisense_mapper.engine.vacuum_map_parser import (
    RoomWalkableArea,
    VacuumMapFeatures,
    parse_vacuum_map_image,
)
from custom_components.wifisense_mapper.vacuum_helpers import VacuumRoomSegment


def _create_mock_vacuum_map_png() -> bytes:
    """Generate a synthetic 100x100 vacuum map PNG with rooms, walls, and dock."""
    im = Image.new("RGBA", (100, 100), (0, 0, 0, 0))  # transparent void
    pixels = im.load()

    # Living Room (left half, green tint)
    for y in range(10, 90):
        for x in range(10, 50):
            pixels[x, y] = (100, 180, 100, 255)

    # Office (right half, blue tint)
    for y in range(10, 90):
        for x in range(50, 90):
            pixels[x, y] = (100, 100, 180, 255)

    # Center dividing wall (high brightness border)
    for y in range(10, 90):
        pixels[50, y] = (255, 255, 255, 255)

    # Outer bounding walls
    for x in range(10, 90):
        pixels[x, 10] = (255, 255, 255, 255)
        pixels[x, 89] = (255, 255, 255, 255)
    for y in range(10, 90):
        pixels[10, y] = (255, 255, 255, 255)
        pixels[89, y] = (255, 255, 255, 255)

    # Dock landmark (bright yellow)
    pixels[20, 20] = (240, 220, 30, 255)

    buf = io.BytesIO()
    im.save(buf, format="PNG")
    return buf.getvalue()


def test_parse_vacuum_map_features() -> None:
    """Test parsing rooms, walls, dock, and furniture from vacuum image bytes."""
    png_bytes = _create_mock_vacuum_map_png()
    segments = [
        VacuumRoomSegment(segment_id="1", name="Living Room", area_id="living_room"),
        VacuumRoomSegment(segment_id="2", name="Office", area_id="office"),
    ]

    features = parse_vacuum_map_image(
        png_bytes,
        segments=segments,
        width_m=10.0,
        height_m=10.0,
        floor_id="ground_floor",
    )

    assert isinstance(features, VacuumMapFeatures)
    assert features.width_m == 10.0
    assert len(features.rooms) >= 1

    # Check that living room or office was recognized
    living = features.get_room_for_area("living_room")
    assert living is not None
    assert isinstance(living, RoomWalkableArea)
    assert living.min_x_m >= 0.5
    assert living.max_x_m <= 10.0

    # Check furniture generation for Living Room and Office
    furniture_names = [f.name for f in features.furniture]
    assert any("Sofa" in name for name in furniture_names)
    assert any("Dock" in name for name in furniture_names or "Dock" in str(features.dock_m))

    # Test wall crossing raycaster
    # Ray from Living room (2.0, 5.0) to Office (8.0, 5.0) must cross center wall
    crossings = features.count_wall_crossings(2.0, 5.0, 8.0, 5.0)
    assert crossings >= 1

    # Ray inside Living room should have 0 crossings
    inside_crossings = features.count_wall_crossings(2.0, 3.0, 3.0, 4.0)
    assert inside_crossings == 0


def test_room_clamping() -> None:
    """Test clamping coordinates to within room bounds."""
    room = RoomWalkableArea(
        segment_id="1",
        name="Living Room",
        area_id="living_room",
        min_x_m=1.0,
        max_x_m=5.0,
        min_y_m=1.0,
        max_y_m=8.0,
    )
    # Outside to the left
    cx, cy = room.clamp(0.2, 4.0)
    assert cx == 1.0
    assert cy == 4.0

    # Outside to the right
    cx, cy = room.clamp(7.5, 4.0)
    assert cx == 5.0
    assert cy == 4.0

    # Inside remains identical
    cx, cy = room.clamp(3.0, 5.0)
    assert cx == 3.0
    assert cy == 5.0
