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
