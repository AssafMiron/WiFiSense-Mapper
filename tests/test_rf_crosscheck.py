"""Tests for RF Motion and Person Tracking Cross-Checking Engine."""

from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock

from custom_components.wifisense_mapper.const import (
    STATE_STATIONARY,
    STATE_WALKING,
)
from custom_components.wifisense_mapper.engine.grid import SpatialGrid
from custom_components.wifisense_mapper.engine.localization import (
    PersonLocalizationEngine,
    PersonTracker,
)
from custom_components.wifisense_mapper.engine.rf_crosscheck import (
    RFCrossCheckEngine,
    RFCrossCheckResult,
)
from custom_components.wifisense_mapper.engine.rf_sensing import RFSensingSnapshot


def test_crosscheck_empty_house_intruder() -> None:
    """Test that RF motion in an empty area triggers an unidentified motion alert."""
    engine = RFCrossCheckEngine(proximity_threshold_m=3.5, coincidence_window_s=15.0)

    rf_snapshot = RFSensingSnapshot(
        area_motion={"living_room": True},
        area_scores={"living_room": 85.0},
        area_active_links={"living_room": 2},
        link_states={
            "backhaul:main->sat": {
                "link_id": "backhaul:main->sat",
                "area_id": "living_room",
                "ap_mac": "aa:bb:cc:dd:ee:01",
                "is_perturbed": True,
            }
        },
    )

    trackers: dict[str, PersonTracker] = {}
    grids: dict[str, SpatialGrid] = {}
    ap_stats: dict[str, Any] = {}

    results, corroborated = engine.evaluate(
        rf_snapshot=rf_snapshot,
        trackers=trackers,
        ap_stats=ap_stats,
        grids=grids,
        all_area_ids={"living_room", "bedroom"},
    )

    lr_res = results["living_room"]
    assert lr_res.unidentified_motion is True
    assert lr_res.occupant_type == "unidentified"
    assert lr_res.unidentified_count == 1
    assert lr_res.rf_corroborated_occupant is None
    assert len(corroborated) == 0

    br_res = results["bedroom"]
    assert br_res.unidentified_motion is False
    assert br_res.occupant_type == "none"


def test_crosscheck_verified_person_in_proximity() -> None:
    """Test that a known person within 3.5m claims the RF disturbance."""
    engine = RFCrossCheckEngine(proximity_threshold_m=3.5)

    rf_snapshot = RFSensingSnapshot(
        area_motion={"living_room": True},
        area_scores={"living_room": 75.0},
        area_active_links={"living_room": 1},
        link_states={
            "link1": {
                "link_id": "link1",
                "area_id": "living_room",
                "ap_mac": "aa:bb:cc:dd:ee:01",
                "is_perturbed": True,
            }
        },
    )

    loc_engine = PersonLocalizationEngine()
    alice = loc_engine.configure_person("11:22:33:44:55:66", person_name="Alice")
    alice.latest_state.area_id = "living_room"
    alice.latest_state.ap_mac = "aa:bb:cc:dd:ee:01"
    alice.latest_state.distance_m = 2.1
    alice.latest_state.activity = STATE_WALKING

    results, corroborated = engine.evaluate(
        rf_snapshot=rf_snapshot,
        trackers=loc_engine.trackers,
        ap_stats={},
        grids={},
        all_area_ids={"living_room"},
    )

    lr_res = results["living_room"]
    assert lr_res.unidentified_motion is False
    assert lr_res.occupant_type == "verified"
    assert lr_res.rf_corroborated_occupant == "Alice"
    assert "Alice" in lr_res.verified_occupants
    assert alice in corroborated
    assert alice.latest_state.rf_corroborated is True


def test_crosscheck_sitting_person_stands_up() -> None:
    """Test that a sitting/stationary occupant standing up absorbs RF disturbance without intruder alert."""
    engine = RFCrossCheckEngine(proximity_threshold_m=3.5)

    rf_snapshot = RFSensingSnapshot(
        area_motion={"living_room": True},
        area_scores={"living_room": 80.0},
        area_active_links={"living_room": 1},
        link_states={
            "link1": {
                "link_id": "link1",
                "area_id": "living_room",
                "ap_mac": "aa:bb:cc:dd:ee:01",
                "is_perturbed": True,
            }
        },
    )

    loc_engine = PersonLocalizationEngine()
    bob = loc_engine.configure_person("aa:bb:cc:11:22:33", person_name="Bob")
    bob.latest_state.area_id = "living_room"
    bob.latest_state.ap_mac = "aa:bb:cc:dd:ee:01"
    bob.latest_state.distance_m = 1.8
    bob.latest_state.activity = STATE_STATIONARY

    results, corroborated = engine.evaluate(
        rf_snapshot=rf_snapshot,
        trackers=loc_engine.trackers,
        ap_stats={},
        grids={},
        all_area_ids={"living_room"},
    )

    lr_res = results["living_room"]
    assert lr_res.unidentified_motion is False
    assert lr_res.occupant_type == "verified"
    assert lr_res.rf_corroborated_occupant == "Bob"
    assert bob in corroborated
    # Verify Bob was transitioned from stationary to walking!
    assert bob.latest_state.activity == STATE_WALKING
    assert bob.latest_state.confidence == 1.0


def test_crosscheck_distant_occupant_mixed_presence() -> None:
    """Test that if a known person in the room is far away, an RF disturbance flags mixed presence."""
    engine = RFCrossCheckEngine(proximity_threshold_m=3.5)

    rf_snapshot = RFSensingSnapshot(
        area_motion={"living_room": True},
        area_scores={"living_room": 70.0},
        area_active_links={"living_room": 1},
        link_states={
            "link1": {
                "link_id": "link1",
                "area_id": "living_room",
                "ap_mac": "aa:bb:cc:dd:ee:01",
                "is_perturbed": True,
            }
        },
    )

    loc_engine = PersonLocalizationEngine()
    alice = loc_engine.configure_person("11:22:33:44:55:66", person_name="Alice")
    alice.latest_state.area_id = "living_room"
    alice.latest_state.ap_mac = "different:ap:mac"
    alice.latest_state.distance_m = 12.0  # Far away
    alice.latest_state.rssi = -85

    results, corroborated = engine.evaluate(
        rf_snapshot=rf_snapshot,
        trackers=loc_engine.trackers,
        ap_stats={},
        grids={},
        all_area_ids={"living_room"},
    )

    lr_res = results["living_room"]
    assert lr_res.unidentified_motion is True
    assert lr_res.occupant_type == "mixed"
    assert "Alice" in lr_res.verified_occupants
    assert lr_res.unidentified_count == 1
    assert len(corroborated) == 0


def test_crosscheck_wall_bleed_suppression() -> None:
    """Test that a person in an adjacent room within 3.5m physical grid distance suppresses false intruder alarm."""
    engine = RFCrossCheckEngine(proximity_threshold_m=3.5, wall_bleed_suppression=True)

    grid = SpatialGrid(floor_id="ground", width_m=12.0, height_m=10.0, resolution_m=0.5)
    # Hallway AP located at (x=5.0, y=5.0)
    grid.set_ap_position("aa:bb:cc:dd:ee:hall", 5.0, 5.0)

    rf_snapshot = RFSensingSnapshot(
        area_motion={"hallway": True},
        area_scores={"hallway": 82.0},
        area_active_links={"hallway": 1},
        link_states={
            "hall_link": {
                "link_id": "hall_link",
                "area_id": "hallway",
                "floor_id": "ground",
                "ap_mac": "aa:bb:cc:dd:ee:hall",
                "is_perturbed": True,
            }
        },
    )

    loc_engine = PersonLocalizationEngine()
    # Alice is in the adjacent Study at (x=6.5, y=5.5) -> distance is sqrt(1.5^2 + 0.5^2) = 1.58m
    alice = loc_engine.configure_person("11:22:33:44:55:66", person_name="Alice")
    alice.latest_state.area_id = "study"
    alice.latest_state.floor_id = "ground"
    alice.latest_state.x_m = 6.5
    alice.latest_state.y_m = 5.5

    results, corroborated = engine.evaluate(
        rf_snapshot=rf_snapshot,
        trackers=loc_engine.trackers,
        ap_stats={},
        grids={"ground": grid},
        all_area_ids={"hallway", "study"},
    )

    hall_res = results["hallway"]
    assert hall_res.unidentified_motion is False
    assert hall_res.occupant_type == "verified"
    assert alice in corroborated


def test_crosscheck_sliding_coincidence_window() -> None:
    """Test that recent person observation within coincidence window suppresses delayed RF motion alert."""
    engine = RFCrossCheckEngine(proximity_threshold_m=3.5, coincidence_window_s=15.0)

    now = 1000.0

    loc_engine = PersonLocalizationEngine()
    alice = loc_engine.configure_person("11:22:33:44:55:66", person_name="Alice")
    alice.latest_state.area_id = "living_room"
    alice.latest_state.distance_m = 2.0
    alice.latest_state.ap_mac = "aa:bb:cc:dd:ee:01"

    # Record observation at t=995s
    engine.record_person_observation(alice, now=995.0)

    # Now at t=1000s, Alice's phone temporarily went into sleep/standby, but RF motion triggers
    alice.latest_state.distance_m = 10.0  # stale / decay
    rf_snapshot = RFSensingSnapshot(
        area_motion={"living_room": True},
        area_scores={"living_room": 78.0},
        area_active_links={"living_room": 1},
        link_states={
            "link1": {
                "link_id": "link1",
                "area_id": "living_room",
                "ap_mac": "aa:bb:cc:dd:ee:01",
                "is_perturbed": True,
            }
        },
    )

    results, corroborated = engine.evaluate(
        rf_snapshot=rf_snapshot,
        trackers=loc_engine.trackers,
        ap_stats={},
        grids={},
        all_area_ids={"living_room"},
        now=now,
    )

    lr_res = results["living_room"]
    assert lr_res.unidentified_motion is False
    assert lr_res.occupant_type == "verified"
    assert alice in corroborated


def test_unidentified_presence_binary_sensor() -> None:
    """Test WiFiSenseUnidentifiedPresenceBinarySensor states and attributes."""
    from homeassistant.helpers.device_registry import DeviceInfo

    from custom_components.wifisense_mapper.binary_sensor import (
        PresenceBinarySensor,
        WiFiSenseUnidentifiedPresenceBinarySensor,
    )

    coord = MagicMock()
    coord.rf_proximity_threshold_m = 3.5
    coord.rf_coincidence_window_s = 15.0
    coord.rf_wall_bleed_suppression = True
    entry = MagicMock()
    entry.entry_id = "test_entry"

    dev_info = DeviceInfo(identifiers={("wifisense_mapper", "living_room")})

    unid_sensor = WiFiSenseUnidentifiedPresenceBinarySensor(
        coord, entry, "living_room", "Living Room", dev_info
    )
    presence_sensor = PresenceBinarySensor(
        coord, entry, "living_room", "Living Room", dev_info
    )

    # 1. Quiet state: no crosscheck result
    coord.data = {"rf_crosscheck": {}}
    assert unid_sensor.is_on is False
    assert presence_sensor.is_on is False
    attrs = unid_sensor.extra_state_attributes
    assert attrs["occupant_type"] == "none"

    # 2. Unidentified intruder state
    coord.data = {
        "rf_crosscheck": {
            "living_room": RFCrossCheckResult(
                area_id="living_room",
                unidentified_motion=True,
                occupant_type="unidentified",
                verified_occupants=[],
                unidentified_count=1,
                rf_corroborated_occupant=None,
                disturbance_score=85.0,
                active_links_count=2,
                last_unidentified_ts=12345.0,
            )
        }
    }
    assert unid_sensor.is_on is True
    assert presence_sensor.is_on is True
    unid_attrs = unid_sensor.extra_state_attributes
    assert unid_attrs["occupant_type"] == "unidentified"
    assert unid_attrs["disturbance_score"] == 85.0

    pres_attrs = presence_sensor.extra_state_attributes
    assert pres_attrs["occupant_type"] == "unidentified"
    assert pres_attrs["unidentified_motion"] is True
    assert pres_attrs["unidentified_count"] == 1

    # 3. Verified occupant state (motion matched to person)
    coord.data = {
        "rf_crosscheck": {
            "living_room": RFCrossCheckResult(
                area_id="living_room",
                unidentified_motion=False,
                occupant_type="verified",
                verified_occupants=["Alice"],
                unidentified_count=0,
                rf_corroborated_occupant="Alice",
                disturbance_score=75.0,
                active_links_count=2,
            )
        }
    }
    assert unid_sensor.is_on is False
    assert unid_sensor.extra_state_attributes["occupant_type"] == "verified"
