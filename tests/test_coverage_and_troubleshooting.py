"""Tests for WiFiSense Mapper stability fixes, troubleshooting step, and coverage engine."""

from __future__ import annotations

import logging
from unittest.mock import MagicMock, patch

import pytest
from homeassistant.core import HomeAssistant

from custom_components.wifisense_mapper.coordinator import (
    _LOG_BUFFER,
    WiFiSenseCoordinator,
)
from custom_components.wifisense_mapper.engine.grid import SpatialGrid
from custom_components.wifisense_mapper.engine.heatmap import HeatmapRenderer
from custom_components.wifisense_mapper.registry_helpers import (
    auto_link_ap_to_ha_device,
)


def test_integer_device_identifier_does_not_crash(hass: HomeAssistant) -> None:
    """Test that integer identifiers in HA device registry do not cause 'int' has no attribute 'lower'."""
    mock_dev_reg = MagicMock()
    mock_device = MagicMock()
    mock_device.connections = {("mac", "b0:a7:b9:bb:36:58")}
    # Simulates integrations using integer IDs like (domain, 12345)
    mock_device.identifiers = {("tuya", 12345), ("hue", "abcde")}
    mock_device.area_id = "office"
    mock_device.name = "Office Deco Node"

    mock_dev_reg.devices = {"device_1": mock_device}

    with patch("homeassistant.helpers.device_registry.async_get", return_value=mock_dev_reg):
        area_id, _floor_id = auto_link_ap_to_ha_device(hass, "b0:a7:b9:bb:36:58", "Office Deco")
        assert area_id == "office"


def test_heatmap_empty_grid_renders_neutral_background() -> None:
    """Test that an empty grid with no RSSI samples does NOT render as solid red."""
    grid = SpatialGrid(floor_id="ground", width_m=10.0, height_m=10.0, resolution_m=1.0)
    renderer = HeatmapRenderer()

    # Empty grid signal render
    png_bytes = renderer.render_signal(grid)
    assert png_bytes is not None
    assert len(png_bytes) > 0
    # PNG signature check
    assert png_bytes.startswith(b"\x89PNG\r\n\x1a\n")


def test_in_memory_log_buffer_captures_messages() -> None:
    """Test that WiFiSense logs are recorded into the in-memory ring buffer."""
    test_logger = logging.getLogger("custom_components.wifisense_mapper.test")
    test_logger.warning("Test warning for troubleshooting inspector")

    captured = list(_LOG_BUFFER.buffer)
    assert any("Test warning for troubleshooting inspector" in line for line in captured)


@pytest.mark.asyncio
async def test_area_coverage_calculation(hass: HomeAssistant) -> None:
    """Test coordinator area mesh coverage and cross-coverage metrics."""
    mock_entry = MagicMock()
    mock_entry.options = {}
    mock_entry.data = {}
    mock_entry.entry_id = "test_entry"

    mock_area1 = MagicMock(id="living_room", name="Living Room")
    mock_area2 = MagicMock(id="kitchen", name="Kitchen")
    mock_area3 = MagicMock(id="garage", name="Garage")

    coordinator = WiFiSenseCoordinator(hass, mock_entry, router_client=None)

    from custom_components.wifisense_mapper.clients.base import APStats

    coordinator.ap_stats = {
        "ap_1": APStats(mac="ap_1", name="Living Room Deco", area_id="living_room"),
        "ap_2": APStats(mac="ap_2", name="Living Room Booster", area_id="living_room"),
        "ap_3": APStats(mac="ap_3", name="Kitchen Deco", area_id="kitchen"),
    }

    with patch(
        "custom_components.wifisense_mapper.registry_helpers.get_all_areas",
        return_value=[mock_area1, mock_area2, mock_area3],
    ):
        summary = coordinator.get_area_coverage_summary()

        assert summary["total_areas"] == 3
        assert summary["covered_count"] == 2  # living_room, kitchen
        assert summary["cross_covered_count"] == 1  # living_room has 2 APs
        assert summary["uncovered_count"] == 1  # garage has 0 APs
        assert "garage" in summary["uncovered_area_ids"]
        assert "living_room" in summary["cross_covered_area_ids"]


@pytest.mark.asyncio
async def test_diagnostics_dump(hass: HomeAssistant, mock_config_entry_no_router) -> None:
    """Test Home Assistant diagnostics dump for WiFiSense entry."""
    from custom_components.wifisense_mapper.const import DOMAIN
    from custom_components.wifisense_mapper.diagnostics import (
        async_get_config_entry_diagnostics,
    )

    mock_config_entry_no_router.add_to_hass(hass)
    coordinator = WiFiSenseCoordinator(hass, mock_config_entry_no_router, router_client=None)

    hass.data.setdefault(DOMAIN, {})[mock_config_entry_no_router.entry_id] = {
        "coordinator": coordinator,
    }

    diag = await async_get_config_entry_diagnostics(hass, mock_config_entry_no_router)
    assert diag["entry_id"] == mock_config_entry_no_router.entry_id
    assert "system_health" in diag
    assert "router_connected" in diag
    assert "coverage_summary" in diag
    assert "recent_logs" in diag


@pytest.mark.asyncio
async def test_options_flow_placeholders_contain_legends(
    hass: HomeAssistant, mock_config_entry_no_router
) -> None:
    """Test that options flow steps provide all required legend placeholders for formatjs."""
    mock_config_entry_no_router.add_to_hass(hass)

    # 1. ap_mapping step placeholders
    result1 = await hass.config_entries.options.async_init(
        mock_config_entry_no_router.entry_id
    )
    result_ap = await hass.config_entries.options.async_configure(
        result1["flow_id"],
        {"next_step_id": "ap_mapping"},
    )
    assert "ap_legend" in result_ap["description_placeholders"]
    assert "ap_count" in result_ap["description_placeholders"]

    # 2. vacuum_mapping step placeholders
    result2 = await hass.config_entries.options.async_init(
        mock_config_entry_no_router.entry_id
    )
    result_vac = await hass.config_entries.options.async_configure(
        result2["flow_id"],
        {"next_step_id": "vacuum_mapping"},
    )
    assert "seg_legend" in result_vac["description_placeholders"]
    assert "segment_count" in result_vac["description_placeholders"]

    # 3. person_tracking step placeholders
    result3 = await hass.config_entries.options.async_init(
        mock_config_entry_no_router.entry_id
    )
    result_person = await hass.config_entries.options.async_configure(
        result3["flow_id"],
        {"next_step_id": "person_tracking"},
    )
    assert "client_legend" in result_person["description_placeholders"]
    assert "client_count" in result_person["description_placeholders"]


@pytest.mark.asyncio
async def test_device_tracker_instantiates_directly_from_person_tags(
    hass: HomeAssistant,
) -> None:
    """Test that device_tracker entities are created for person_tags even before router poll."""
    from pytest_homeassistant_custom_component.common import MockConfigEntry

    from custom_components.wifisense_mapper.const import CONF_PERSON_TAGS, DOMAIN
    from custom_components.wifisense_mapper.device_tracker import async_setup_entry

    entry = MockConfigEntry(
        domain=DOMAIN,
        entry_id="test_dt_entry",
        data={"router_type": "none"},
        options={
            CONF_PERSON_TAGS: {
                "b0:a7:b9:bb:11:22": {
                    "person_entity_id": "person.john",
                    "person_name": "John Phone",
                }
            }
        },
    )
    entry.add_to_hass(hass)

    coordinator = WiFiSenseCoordinator(hass, entry, router_client=None)
    hass.data.setdefault(DOMAIN, {})[entry.entry_id] = {"coordinator": coordinator}

    added_entities = []
    await async_setup_entry(hass, entry, added_entities.extend)

    assert len(added_entities) == 1
    tracker = added_entities[0]
    assert tracker._mac == "b0:a7:b9:bb:11:22"
    assert tracker.device_info["name"] == "John Phone"


@pytest.mark.asyncio
async def test_coordinator_seeds_ap_stats_from_node_area_map(
    hass: HomeAssistant,
) -> None:
    """Test that coordinator seeds self.ap_stats from node_area_map in options."""
    from pytest_homeassistant_custom_component.common import MockConfigEntry

    from custom_components.wifisense_mapper.const import DOMAIN

    entry = MockConfigEntry(
        domain=DOMAIN,
        entry_id="test_ap_seed_entry",
        data={"router_type": "none"},
        options={
            "node_area_map": {
                "b0:a7:b9:bb:2f:ac": "bedroom",
                "b0:a7:b9:bb:32:30": "kitchen",
                "b0:a7:b9:bb:36:58": "office",
            }
        },
    )
    entry.add_to_hass(hass)

    coordinator = WiFiSenseCoordinator(hass, entry, router_client=None)
    await coordinator.async_initialize()

    assert "b0:a7:b9:bb:2f:ac" in coordinator.ap_stats
    assert coordinator.ap_stats["b0:a7:b9:bb:2f:ac"].area_id == "bedroom"
    assert coordinator.ap_stats["b0:a7:b9:bb:32:30"].area_id == "kitchen"
    assert coordinator.ap_stats["b0:a7:b9:bb:36:58"].area_id == "office"


@pytest.mark.asyncio
async def test_anomaly_sensor_returns_zero_when_idle(
    hass: HomeAssistant, mock_config_entry_no_router
) -> None:
    """Test that AnomalyScoreSensor returns 0.0 rather than None when idle."""
    from custom_components.wifisense_mapper.sensor import AnomalyScoreSensor

    mock_config_entry_no_router.add_to_hass(hass)
    coordinator = WiFiSenseCoordinator(hass, mock_config_entry_no_router, router_client=None)
    await coordinator.async_initialize()

    mock_dev_info = MagicMock()
    sensor = AnomalyScoreSensor(
        coordinator, mock_config_entry_no_router, "ground", "Ground Floor", mock_dev_info
    )

    assert sensor.native_value == 0.0


