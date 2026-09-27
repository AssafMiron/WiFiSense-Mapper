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
