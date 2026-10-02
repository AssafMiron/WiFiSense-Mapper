"""Tests for the WiFiSense Mapper coordinator."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from custom_components.wifisense_mapper.coordinator import WiFiSenseCoordinator
from custom_components.wifisense_mapper.engine.baseline import BaselineLearner
from custom_components.wifisense_mapper.engine.grid import SpatialGrid


@pytest.mark.asyncio
async def test_coordinator_update_with_router(
    mock_config_entry_deco,
    mock_deco_client,
    mock_router_clients,
    mock_ap_stats,
):
    """Test coordinator update cycle with a mock router client."""
    hass = MagicMock()
    hass.async_add_executor_job = AsyncMock(return_value=b"PNG_BYTES")
    hass.states.get = MagicMock(return_value=None)

    coord = WiFiSenseCoordinator(hass, mock_config_entry_deco, mock_deco_client)

    # Initialize a single floor grid
    coord.grids["ground_floor"] = SpatialGrid("ground_floor")
    coord.baselines["ground_floor"] = BaselineLearner("ground_floor")
    coord._scanning = True

    # Run the update
    data = await coord._async_update_data()

    # Verify router data was fetched
    mock_deco_client.async_get_clients.assert_called_once()
    mock_deco_client.async_get_ap_stats.assert_called_once()

    # Verify clients in data
    assert len(data["router_clients"]) == 3


@pytest.mark.asyncio
async def test_coordinator_stops_when_scanning_false(
    mock_config_entry_no_router,
):
    """Test that coordinator skips polling when scanning is paused."""
    hass = MagicMock()
    hass.async_add_executor_job = AsyncMock(return_value=b"PNG_BYTES")

    coord = WiFiSenseCoordinator(hass, mock_config_entry_no_router, None)
    coord.grids["default"] = SpatialGrid("default")
    coord.baselines["default"] = BaselineLearner("default")
    coord._scanning = False

    data = await coord._async_update_data()
    # Should return current (empty) data without polling
    assert data["scanning"] is False
    assert len(data["router_clients"]) == 0


@pytest.mark.asyncio
async def test_coordinator_start_stop_scan(mock_config_entry_no_router):
    """Test start_scan and stop_scan toggle the scanning flag."""
    hass = MagicMock()
    coord = WiFiSenseCoordinator(hass, mock_config_entry_no_router, None)

    assert coord._scanning is True
    coord.stop_scan()
    assert coord._scanning is False
    coord.start_scan()
    assert coord._scanning is True


@pytest.mark.asyncio
async def test_coordinator_router_failure_graceful(
    mock_config_entry_deco,
    mock_deco_client,
):
    """Test coordinator handles router polling failure without crashing."""
    hass = MagicMock()
    hass.async_add_executor_job = AsyncMock(return_value=b"PNG")
    hass.states.get = MagicMock(return_value=None)

    mock_deco_client.async_get_clients = AsyncMock(
        side_effect=Exception("Connection dropped")
    )
    mock_deco_client.async_get_ap_stats = AsyncMock(
        side_effect=Exception("Connection dropped")
    )

    coord = WiFiSenseCoordinator(hass, mock_config_entry_deco, mock_deco_client)
    coord.grids["g1"] = SpatialGrid("g1")
    coord.baselines["g1"] = BaselineLearner("g1")
    coord._scanning = True

    # Should not raise
    data = await coord._async_update_data()
    # Router clients should be empty (failed poll)
    assert len(data["router_clients"]) == 0


def test_resolve_floor_for_client_uses_ap(mock_config_entry_no_router, mock_ap_stats):
    """Test floor resolution uses AP floor_id when client floor_id is absent."""
    hass = MagicMock()
    coord = WiFiSenseCoordinator(hass, mock_config_entry_no_router, None)
    coord.grids["ground_floor"] = SpatialGrid("ground_floor")

    # Set up AP with floor
    from custom_components.wifisense_mapper.clients.base import (
        APStats,
        ClientInfo,
    )

    ap = APStats(mac="de:ad:be:ef:00:01", floor_id="ground_floor")
    coord.ap_stats["de:ad:be:ef:00:01"] = ap

    client = ClientInfo(mac="aa:bb:cc", ap_mac="de:ad:be:ef:00:01")
    floor = coord._resolve_floor_for_client(client)
    assert floor == "ground_floor"


@pytest.mark.asyncio
async def test_coordinator_rf_sensing_integration(mock_config_entry_deco):
    """Test coordinator feeds backhaul and stationary clients to RF detector."""
    hass = MagicMock()
    hass.async_add_executor_job = AsyncMock(return_value=b"PNG")
    hass.states.get = MagicMock(return_value=None)

    router_client = MagicMock()
    router_client.async_get_clients = AsyncMock(return_value=[])
    router_client.async_get_ap_stats = AsyncMock(return_value=[])
    router_client.async_get_backhaul_links = AsyncMock(
        return_value=[
            {
                "satellite_mac": "22:22:22:22:22:22",
                "satellite_name": "Living Room Deco",
                "parent_mac": "11:11:11:11:11:11",
                "area_id": "living_room",
                "rssi": -65,
                "type": "wifi",
            }
        ]
    )

    coord = WiFiSenseCoordinator(hass, mock_config_entry_deco, router_client)
    coord.grids["default"] = SpatialGrid("default")
    coord.baselines["default"] = BaselineLearner("default")

    # Run update
    data = await coord._async_update_data()
    assert "rf_sensing" in data
    snapshot = data["rf_sensing"]
    assert "backhaul:11:11:11:11:11:11->22:22:22:22:22:22" in snapshot.link_states
    link_info = snapshot.link_states["backhaul:11:11:11:11:11:11->22:22:22:22:22:22"]
    assert link_info["area_id"] == "living_room"
    assert link_info["last_rssi"] == -65


@pytest.mark.asyncio
async def test_coordinator_adaptive_polling_acceleration_and_recovery(
    mock_config_entry_deco,
):
    """Test coordinator dynamically switches to 3s burst mode during RF disturbance and reverts when quiet."""
    from datetime import timedelta

    hass = MagicMock()
    hass.async_add_executor_job = AsyncMock(return_value=b"PNG")
    hass.states.get = MagicMock(return_value=None)

    router_client = MagicMock()
    router_client.async_get_clients = AsyncMock(return_value=[])
    router_client.async_get_ap_stats = AsyncMock(return_value=[])

    coord = WiFiSenseCoordinator(hass, mock_config_entry_deco, router_client)
    coord.grids["default"] = SpatialGrid("default")
    coord.baselines["default"] = BaselineLearner("default")
    coord.adaptive_polling = True
    coord.rf_detector.off_delay_sec = 5.0
    coord.update_interval = timedelta(seconds=60)

    # 1. Quiet state: normal interval
    router_client.async_get_backhaul_links = AsyncMock(
        return_value=[
            {
                "satellite_mac": "22:22:22:22:22:22",
                "parent_mac": "11:11:11:11:11:11",
                "area_id": "living_room",
                "rssi": -60,
                "type": "wifi",
            }
        ]
    )
    for _ in range(3):
        await coord._async_update_data()

    assert coord.update_interval == timedelta(seconds=60)
    assert not coord.rf_snapshot.burst_recommended

    # 2. RF disturbance occurs: burst mode accelerates interval to 3s
    router_client.async_get_backhaul_links = AsyncMock(
        return_value=[
            {
                "satellite_mac": "22:22:22:22:22:22",
                "parent_mac": "11:11:11:11:11:11",
                "area_id": "living_room",
                "rssi": -88,
                "type": "wifi",
            }
        ]
    )
    for _ in range(2):
        await coord._async_update_data()

    assert coord.rf_snapshot.burst_recommended is True
    assert coord.update_interval == timedelta(seconds=3)

    # 3. RF disturbance subsides: quiet samples + timer clears burst mode back to configured 60s
    router_client.async_get_backhaul_links = AsyncMock(
        return_value=[
            {
                "satellite_mac": "22:22:22:22:22:22",
                "parent_mac": "11:11:11:11:11:11",
                "area_id": "living_room",
                "rssi": -60,
                "type": "wifi",
            }
        ]
    )
    # Simulate time jumping past off_delay_sec
    coord.rf_detector._area_last_motion_time["living_room"] = 0.0
    await coord._async_update_data()

    assert coord.rf_snapshot.burst_recommended is False
    assert coord.update_interval == timedelta(seconds=60)
