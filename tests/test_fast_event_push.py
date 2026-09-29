"""Tests for fast-path event-driven push in coordinator."""

from __future__ import annotations

import asyncio
from unittest.mock import MagicMock

import pytest
from homeassistant.core import HomeAssistant

from custom_components.wifisense_mapper.const import CONF_FAST_EVENT_PUSH
from custom_components.wifisense_mapper.coordinator import WiFiSenseCoordinator
from custom_components.wifisense_mapper.csi_discovery import CSINodeInfo


@pytest.mark.asyncio
async def test_fast_event_listener_registration(hass: HomeAssistant) -> None:
    """Test registering and triggering fast-path event listeners."""
    entry = MagicMock()
    entry.entry_id = "test_entry"
    entry.options = {CONF_FAST_EVENT_PUSH: True}
    entry.data = {}

    coord = WiFiSenseCoordinator(hass, entry, router_client=None)

    # Add a mock CSI node
    node = CSINodeInfo(
        device_id="csi_1",
        platform="espectre",
        motion_score_entity_id="sensor.csi_motion_score",
        motion_detected_entity_id="binary_sensor.csi_presence",
    )
    coord.csi_nodes.append(node)

    # Configure a tracked person
    coord.localization_engine.configure_person(
        mac="11:22:33:44:55:66",
        person_entity_id="person.assaf",
        person_name="Assaf",
    )

    # Set up listeners
    coord.async_setup_event_listeners()
    assert len(coord._unsub_listeners) >= 1

    # Simulate HA state change event on sensor.csi_motion_score
    hass.states.async_set("sensor.csi_motion_score", "35.5")
    await hass.async_block_till_done()

    # Fast event handler should have debounced handle scheduled
    assert coord._fast_debounce_handle is not None

    # Wait for the 250ms debounce timer to fire
    await asyncio.sleep(0.35)
    await hass.async_block_till_done()

    # The CSI state should be read into the node
    assert node.motion_score_value == 35.5
    assert coord._fast_debounce_handle is None

    # Test unload
    coord.async_unload_listeners()
    assert len(coord._unsub_listeners) == 0


@pytest.mark.asyncio
async def test_fast_event_push_disabled(hass: HomeAssistant) -> None:
    """Test that event listeners are skipped when fast_event_push is disabled."""
    entry = MagicMock()
    entry.entry_id = "test_entry"
    entry.options = {CONF_FAST_EVENT_PUSH: False}
    entry.data = {}

    coord = WiFiSenseCoordinator(hass, entry, router_client=None)
    coord.async_setup_event_listeners()
    assert len(coord._unsub_listeners) == 0


@pytest.mark.asyncio
async def test_fast_event_excludes_wifisense_and_person_entities(
    hass: HomeAssistant,
) -> None:
    """Verify that WiFiSense's own device_trackers and person.* entities are never tracked."""
    from homeassistant.helpers import entity_registry as er

    from custom_components.wifisense_mapper.const import DOMAIN

    ent_reg = er.async_get(hass)

    mac = "11:22:33:44:55:66"

    # 1. WiFiSense's own device tracker
    ent_reg.async_get_or_create(
        domain="device_tracker",
        platform=DOMAIN,
        unique_id=f"test_entry_tracker_{mac}",
        suggested_object_id="wifisense_assaf",
    )

    # 2. External router device tracker
    external_dt = ent_reg.async_get_or_create(
        domain="device_tracker",
        platform="tplink_deco",
        unique_id=f"deco_client_{mac}",
        suggested_object_id="assaf_phone",
    )

    entry = MagicMock()
    entry.entry_id = "test_entry"
    entry.options = {CONF_FAST_EVENT_PUSH: True}
    entry.data = {}

    coord = WiFiSenseCoordinator(hass, entry, router_client=None)
    coord.localization_engine.configure_person(
        mac=mac,
        person_entity_id="person.assaf",
        person_name="Assaf",
    )

    coord.async_setup_event_listeners()

    # Verify that ONLY the external device tracker is listened to, NOT WiFiSense's own or person entity
    # (Checking listener callback behavior)
    # Simulate state change on WiFiSense's own device tracker -> MUST NOT trigger debounce
    hass.states.async_set("device_tracker.wifisense_assaf", "Living Room")
    await hass.async_block_till_done()
    assert coord._fast_debounce_handle is None

    # Simulate state change on person.assaf -> MUST NOT trigger debounce
    hass.states.async_set("person.assaf", "Living Room")
    await hass.async_block_till_done()
    assert coord._fast_debounce_handle is None

    # Simulate state change on external device tracker -> MUST trigger debounce
    hass.states.async_set(
        external_dt.entity_id,
        "home",
        {"rssi": -55, "ap_mac": "aa:bb:cc:dd:ee:01"},
    )
    await hass.async_block_till_done()
    assert coord._fast_debounce_handle is not None

    # Wait for fast update to run
    await asyncio.sleep(0.35)
    await hass.async_block_till_done()

    # External telemetry should be ingested into router_clients
    assert mac in coord.router_clients
    assert coord.router_clients[mac].rssi == -55
    assert coord.router_clients[mac].ap_mac == "aa:bb:cc:dd:ee:01"

    coord.async_unload_listeners()


@pytest.mark.asyncio
async def test_fast_event_no_op_on_identical_state(hass: HomeAssistant) -> None:
    """Verify that event with identical state and attributes does not trigger debounce."""
    from homeassistant.helpers import entity_registry as er

    ent_reg = er.async_get(hass)
    mac = "aa:bb:cc:dd:ee:ff"
    external_dt = ent_reg.async_get_or_create(
        domain="device_tracker",
        platform="tplink_deco",
        unique_id=f"deco_{mac}",
        suggested_object_id="noa_phone",
    )

    entry = MagicMock()
    entry.entry_id = "test_entry"
    entry.options = {CONF_FAST_EVENT_PUSH: True}
    entry.data = {}

    coord = WiFiSenseCoordinator(hass, entry, router_client=None)
    coord.localization_engine.configure_person(mac=mac, person_name="Noa")
    coord.async_setup_event_listeners()

    # Initial state
    hass.states.async_set(external_dt.entity_id, "home", {"rssi": -60})
    await hass.async_block_till_done()
    assert coord._fast_debounce_handle is not None
    await asyncio.sleep(0.35)
    await hass.async_block_till_done()
    assert coord._fast_debounce_handle is None

    # Firing identical state again should not trigger debounce
    hass.states.async_set(external_dt.entity_id, "home", {"rssi": -60})
    await hass.async_block_till_done()
    assert coord._fast_debounce_handle is None

    coord.async_unload_listeners()
