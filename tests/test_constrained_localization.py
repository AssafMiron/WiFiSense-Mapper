"""Tests for constrained localization, multi-AP triangulation, and furniture snapping."""

from __future__ import annotations

from custom_components.wifisense_mapper.const import STATE_STATIONARY, STATE_WALKING
from custom_components.wifisense_mapper.engine.localization import (
    MicroZone,
    PersonTracker,
    calculate_distance_from_rssi,
)
from custom_components.wifisense_mapper.engine.vacuum_map_parser import (
    FurnitureCluster,
    RoomWalkableArea,
    VacuumMapFeatures,
)


def test_calculate_distance_with_wall_attenuation() -> None:
    """Test distance calculation with obstacle / wall compensation."""
    # Free space: -60 dBm at 5GHz yields approx 2.6m
    dist_free = calculate_distance_from_rssi(-60, band="5", wall_crossings=0)
    assert dist_free is not None

    # With 2 wall crossings (8 dB loss compensation), distance estimate is closer than raw RSSI would suggest
    dist_walls = calculate_distance_from_rssi(-60, band="5", wall_crossings=2, wall_attenuation_db=4.0)
    assert dist_walls is not None
    assert dist_walls < dist_free


def test_multi_ap_triangulation() -> None:
    """Test that person position pulls towards secondary AP rather than random angle."""
    tracker = PersonTracker(mac="aa:bb:cc:dd:ee:01", person_name="Assaf")

    # AP 1 at (2.0, 5.0), AP 2 at (8.0, 5.0)
    ap1_pos = (2.0, 5.0)
    all_aps = {
        "Living Room Deco": (2.0, 5.0),
        "Office Deco": (8.0, 5.0),
    }
    ap_distances = {
        "Living Room Deco": 2.0,
        "Office Deco": 4.5,
    }

    state = tracker.update(
        ap_mac="11:22:33:44:55:01",
        ap_name="Living Room Deco",
        band="5",
        rssi=-50,
        floor_id="ground_floor",
        floor_name="Ground Floor",
        area_id="living_room",
        area_name="Living Room",
        ap_pos_m=ap1_pos,
        grid_width_m=10.0,
        grid_height_m=10.0,
        ap_distances=ap_distances,
        all_ap_positions=all_aps,
    )

    # Position should pull rightward along X axis towards Office Deco (x > 2.0)
    assert state.x_m > 2.0
    # Y should remain centered near 5.0
    assert 4.0 <= state.y_m <= 6.0


def test_room_boundary_clamping_and_furniture_snapping() -> None:
    """Test clamping within room walls and snapping to furniture micro-zone."""
    tracker = PersonTracker(mac="aa:bb:cc:dd:ee:02", person_name="Assaf")

    # Define vacuum features with Living room from x=1.0 to 5.0
    room = RoomWalkableArea(
        segment_id="1",
        name="Living Room",
        area_id="living_room",
        min_x_m=1.0,
        max_x_m=5.0,
        min_y_m=1.0,
        max_y_m=6.0,
        centroid_x_m=3.0,
        centroid_y_m=3.5,
    )
    sofa = FurnitureCluster(
        name="Living Room Sofa",
        furniture_type="sofa",
        area_id="living_room",
        floor_id="ground_floor",
        x_m=2.5,
        y_m=3.0,
        radius_m=1.5,
    )
    features = VacuumMapFeatures(
        width_m=10.0,
        height_m=10.0,
        rooms={"living_room": room},
        furniture=[sofa],
    )

    # AP placed at (1.5, 3.0), RSSI close to sofa
    state = tracker.update(
        ap_mac="11:22:33:44:55:01",
        ap_name="Living Room Deco",
        band="5",
        rssi=-48,
        floor_id="ground_floor",
        floor_name="Ground Floor",
        area_id="living_room",
        area_name="Living Room",
        ap_pos_m=(1.5, 3.0),
        grid_width_m=10.0,
        grid_height_m=10.0,
        vacuum_features=features,
    )

    # Must be clamped inside living room bounds
    assert 1.0 <= state.x_m <= 5.0
    assert 1.0 <= state.y_m <= 6.0

    # Because speed is stationary and position is near sofa, micro-zone should snap to sofa!
    assert state.micro_zone == "Living Room Sofa"
    assert state.activity == STATE_STATIONARY


def test_walking_activity_release() -> None:
    """Test that moving releases micro-zone and sets Walking activity."""
    tracker = PersonTracker(mac="aa:bb:cc:dd:ee:03", person_name="Assaf")
    mz = MicroZone(name="Desk", area_id="office", floor_id="ground_floor", x_m=5.0, y_m=5.0, radius_m=1.2)

    # First update at (5.0, 5.0)
    tracker.update(
        ap_mac="11:22:33:44:55:01",
        ap_name="Office Deco",
        rssi=-45,
        floor_id="ground_floor",
        floor_name="Ground Floor",
        area_id="office",
        area_name="Office",
        ap_pos_m=(5.0, 5.0),
        grid_width_m=10.0,
        grid_height_m=10.0,
        micro_zones=[mz],
        now_ts=100.0,
    )

    # Second update at distant position with short time elapsed -> high velocity
    state = tracker.update(
        ap_mac="11:22:33:44:55:01",
        ap_name="Office Deco",
        rssi=-80,
        floor_id="ground_floor",
        floor_name="Ground Floor",
        area_id="office",
        area_name="Office",
        ap_pos_m=(9.0, 9.0),
        grid_width_m=10.0,
        grid_height_m=10.0,
        micro_zones=[mz],
        now_ts=101.0,
    )

    assert state.activity == STATE_WALKING
