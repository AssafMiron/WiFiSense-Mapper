"""Tests for WiFiSense Mapper — Person Tracking & Sensor Entities."""

from unittest.mock import MagicMock

import pytest
from homeassistant.core import HomeAssistant
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.wifisense_mapper.const import (
    CONF_PERSON_TAGS,
    DOMAIN,
    STATE_STATIONARY,
)
from custom_components.wifisense_mapper.coordinator import WiFiSenseCoordinator
from custom_components.wifisense_mapper.device_tracker import WifiSenseDeviceTracker
from custom_components.wifisense_mapper.engine.localization import PersonTrackingState
from custom_components.wifisense_mapper.sensor import (
    WifiSensePersonActivitySensor,
    WifiSensePersonCoordinatesSensor,
    WifiSensePersonLocationSensor,
)


@pytest.mark.asyncio
async def test_person_tracking_coordinator_flow(
    hass: HomeAssistant,
    mock_deco_client,
) -> None:
    """Test coordinator person tracking localization update cycle."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        entry_id="test_person_entry",
        data={"router_type": "none"},
        options={
            CONF_PERSON_TAGS: {
                "aa:bb:cc:dd:ee:01": {
                    "person_entity_id": "person.alice",
                    "person_name": "Alice",
                }
            }
        },
    )
    entry.add_to_hass(hass)

    coordinator = WiFiSenseCoordinator(hass, entry, mock_deco_client)
    await coordinator.async_initialize()

    # Verify tracker is registered
    assert "aa:bb:cc:dd:ee:01" in coordinator.localization_engine.trackers
    tracker = coordinator.localization_engine.trackers["aa:bb:cc:dd:ee:01"]
    assert tracker.person_name == "Alice"

    # Refresh data
    await coordinator.async_refresh()

    person_data = coordinator.data.get("person_tracking", {})
    assert "aa:bb:cc:dd:ee:01" in person_data
    state: PersonTrackingState = person_data["aa:bb:cc:dd:ee:01"]
    assert state.person_name == "Alice"
    assert state.area_name in ("Living Room", "Home", "living_room")


def test_person_sensors() -> None:
    """Test WifiSensePersonLocationSensor, ActivitySensor, and CoordinatesSensor."""
    coordinator = MagicMock()
    entry = MagicMock()
    entry.entry_id = "test_entry"
    device_info = MagicMock()

    mac = "aa:bb:cc:dd:ee:01"
    p_state = PersonTrackingState(
        mac=mac,
        person_name="Alice",
        area_name="Kitchen",
        floor_name="Ground Floor",
        activity=STATE_STATIONARY,
        x_m=4.25,
        y_m=6.80,
        x_pct=42.5,
        y_pct=68.0,
        confidence=0.95,
        dwell_time_s=120,
        micro_zone="Dining Table",
    )
    coordinator.data = {"person_tracking": {mac: p_state}}

    loc_sensor = WifiSensePersonLocationSensor(
        coordinator, entry, mac, "Alice", device_info
    )
    act_sensor = WifiSensePersonActivitySensor(
        coordinator, entry, mac, "Alice", device_info
    )
    coord_sensor = WifiSensePersonCoordinatesSensor(
        coordinator, entry, mac, "Alice", device_info
    )

    assert loc_sensor.native_value == "Kitchen"
    assert loc_sensor.extra_state_attributes["micro_zone"] == "Dining Table"

    assert act_sensor.native_value == STATE_STATIONARY
    assert act_sensor.extra_state_attributes["dwell_time_s"] == 120

    assert coord_sensor.native_value == "4.25, 6.80"
    assert coord_sensor.extra_state_attributes["x_pct"] == 42.5
    assert coord_sensor.extra_state_attributes["y_pct"] == 68.0


def test_device_tracker_person_attributes() -> None:
    """Test WifiSenseDeviceTracker extra attributes when linked to person tracking."""
    coordinator = MagicMock()
    entry = MagicMock()
    entry.entry_id = "test_entry"
    device_info = MagicMock()

    mac = "aa:bb:cc:dd:ee:01"
    p_state = PersonTrackingState(
        mac=mac,
        person_name="Alice",
        area_name="Living Room",
        floor_name="Ground Floor",
        activity=STATE_STATIONARY,
        x_m=2.0,
        y_m=3.0,
        x_pct=20.0,
        y_pct=30.0,
        confidence=1.0,
        dwell_time_s=50,
    )
    coordinator.data = {"person_tracking": {mac: p_state}}
    coordinator.router_clients = {}

    tracker = WifiSenseDeviceTracker(coordinator, entry, mac, device_info)
    assert tracker.location_name == "Living Room"
    attrs = tracker.extra_state_attributes
    assert attrs["person_name"] == "Alice"
    assert attrs["x_pct"] == 20.0
    assert attrs["y_pct"] == 30.0
