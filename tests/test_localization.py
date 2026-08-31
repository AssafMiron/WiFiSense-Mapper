"""Tests for WiFiSense Mapper — Localization Engine."""

from custom_components.wifisense_mapper.const import (
    STATE_AWAY,
    STATE_STATIONARY,
    STATE_TRANSITIONING,
    STATE_WALKING,
)
from custom_components.wifisense_mapper.engine.localization import (
    KalmanFilter2D,
    MicroZone,
    PersonLocalizationEngine,
    PersonTracker,
)


def test_kalman_filter_2d_smoothing() -> None:
    """Test that KalmanFilter2D smooths position jumps and estimates speed."""
    kf = KalmanFilter2D(x=0.0, y=0.0, process_noise=0.1, measurement_noise=2.0)

    # Initial update
    t0 = 1000.0
    x, y, _speed = kf.update(0.0, 0.0, ts=t0)
    assert abs(x) < 0.5
    assert abs(y) < 0.5

    # Series of noisy observations around (5.0, 5.0)
    for i in range(1, 10):
        noisy_x = 5.0 + (0.5 if i % 2 == 0 else -0.5)
        noisy_y = 5.0 + (-0.5 if i % 2 == 0 else 0.5)
        x, y, _speed = kf.update(noisy_x, noisy_y, ts=t0 + i)

    # Filtered output should have converged near (5.0, 5.0)
    assert 3.5 <= x <= 6.5
    assert 3.5 <= y <= 6.5


def test_micro_zone_matching() -> None:
    """Test MicroZone spatial containment."""
    desk_zone = MicroZone(
        name="Desk",
        area_id="office",
        floor_id="first_floor",
        x_m=3.0,
        y_m=4.0,
        radius_m=1.0,
    )

    # Inside
    assert desk_zone.is_inside(3.2, 4.1, floor_id="first_floor") is True
    # Outside radius
    assert desk_zone.is_inside(5.0, 4.0, floor_id="first_floor") is False
    # Wrong floor
    assert desk_zone.is_inside(3.2, 4.1, floor_id="ground_floor") is False


def test_person_tracker_lifecycle() -> None:
    """Test PersonTracker states: stationary, walking, transitioning, and away decay."""
    tracker = PersonTracker(
        mac="AA:BB:CC:DD:EE:FF",
        person_entity_id="person.alice",
        person_name="Alice",
    )

    now = 10000.0

    # 1. First reading: stationary in Office
    state = tracker.update(
        ap_mac="11:22:33:44:55:66",
        rssi=-50,
        floor_id="ground_floor",
        floor_name="Ground Floor",
        area_id="office",
        area_name="Office",
        ap_pos_m=(3.0, 3.0),
        grid_width_m=10.0,
        grid_height_m=10.0,
        csi_motion_score=2.0,
        now_ts=now,
    )
    assert state.person_name == "Alice"
    assert state.area_name == "Office"
    assert state.floor_name == "Ground Floor"
    assert state.activity == STATE_STATIONARY
    assert state.confidence == 1.0

    # 2. Transition to Kitchen with high CSI motion
    now += 2.0
    state = tracker.update(
        ap_mac="11:22:33:44:55:77",
        rssi=-45,
        floor_id="ground_floor",
        floor_name="Ground Floor",
        area_id="kitchen",
        area_name="Kitchen",
        ap_pos_m=(7.0, 3.0),
        grid_width_m=10.0,
        grid_height_m=10.0,
        csi_motion_score=25.0,  # high motion
        now_ts=now,
    )
    assert state.area_name == "Kitchen"
    assert state.last_area_name == "Office"
    assert state.activity in (STATE_TRANSITIONING, STATE_WALKING)

    # 3. Micro-zone matching at Kitchen Island
    island = MicroZone(
        name="Kitchen Island",
        area_id="kitchen",
        floor_id="ground_floor",
        x_m=state.x_m,
        y_m=state.y_m,
        radius_m=2.0,
    )
    now += 15.0
    state = tracker.update(
        ap_mac="11:22:33:44:55:77",
        rssi=-45,
        floor_id="ground_floor",
        floor_name="Ground Floor",
        area_id="kitchen",
        area_name="Kitchen",
        ap_pos_m=(7.0, 3.0),
        grid_width_m=10.0,
        grid_height_m=10.0,
        csi_motion_score=1.0,
        micro_zones=[island],
        now_ts=now,
    )
    assert state.micro_zone == "Kitchen Island"
    assert state.activity == STATE_STATIONARY

    # 4. Standby confidence decay
    now += 100.0  # 100s without packets
    state = tracker.update(
        ap_mac=None,
        rssi=None,
        floor_id="ground_floor",
        floor_name="Ground Floor",
        area_id="kitchen",
        area_name="Kitchen",
        ap_pos_m=None,
        grid_width_m=10.0,
        grid_height_m=10.0,
        now_ts=now,
    )
    assert state.confidence < 1.0
    assert state.activity == STATE_STATIONARY

    # 5. Long absence -> Away
    now += 1000.0
    state = tracker.update(
        ap_mac=None,
        rssi=None,
        floor_id="ground_floor",
        floor_name="Ground Floor",
        area_id="kitchen",
        area_name="Kitchen",
        ap_pos_m=None,
        grid_width_m=10.0,
        grid_height_m=10.0,
        now_ts=now,
    )
    assert state.activity == STATE_AWAY
    assert state.confidence == 0.0


def test_person_localization_engine() -> None:
    """Test PersonLocalizationEngine registration and serialization."""
    engine = PersonLocalizationEngine()
    tracker = engine.configure_person(
        mac="AA:BB:CC:11:22:33",
        person_entity_id="person.bob",
        person_name="Bob",
    )
    assert tracker.mac == "aa:bb:cc:11:22:33"
    assert tracker.person_name == "Bob"

    engine.set_micro_zones([
        {
            "name": "Couch",
            "area_id": "living_room",
            "floor_id": "ground_floor",
            "x_m": 2.0,
            "y_m": 5.0,
            "radius_m": 1.5,
        }
    ])
    assert len(engine.micro_zones) == 1
    assert engine.micro_zones[0].name == "Couch"

    states = engine.all_states()
    assert "aa:bb:cc:11:22:33" in states
    assert states["aa:bb:cc:11:22:33"].person_name == "Bob"
