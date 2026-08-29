"""Shared pytest fixtures for WiFiSense Mapper tests.

Uses pytest-homeassistant-custom-component for HA integration testing.
All fixtures follow the HA custom component testing conventions.
"""
from __future__ import annotations

import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from typing import Any

from homeassistant.core import HomeAssistant
from homeassistant.config_entries import ConfigEntry

from custom_components.wifisense_mapper.const import (
    DOMAIN,
    ROUTER_TYPE_DECO,
    ROUTER_TYPE_NONE,
    CONF_ROUTER_TYPE,
    CONF_ROUTER_HOST,
    CONF_ROUTER_PASSWORD,
)
from custom_components.wifisense_mapper.clients.base import ClientInfo, APStats
from custom_components.wifisense_mapper.csi_discovery import CSINodeInfo


# ─── Synthetic telemetry fixtures ─────────────────────────────────────────────

@pytest.fixture
def mock_router_clients() -> list[ClientInfo]:
    """A minimal set of synthetic WiFi clients for testing."""
    return [
        ClientInfo(
            mac="aa:bb:cc:dd:ee:01",
            ip="192.168.1.101",
            hostname="phone-alice",
            rssi=-55,
            ap_mac="de:ad:be:ef:00:01",
            ssid="HomeNet",
            band="5GHz",
        ),
        ClientInfo(
            mac="aa:bb:cc:dd:ee:02",
            ip="192.168.1.102",
            hostname="laptop-bob",
            rssi=-72,
            ap_mac="de:ad:be:ef:00:02",
            ssid="HomeNet",
            band="2.4GHz",
        ),
        ClientInfo(
            mac="aa:bb:cc:dd:ee:03",
            ip="192.168.1.103",
            hostname=None,
            rssi=-88,
            ap_mac="de:ad:be:ef:00:01",
            ssid="HomeNet",
            band="2.4GHz",
        ),
    ]


@pytest.fixture
def mock_ap_stats() -> list[APStats]:
    """Synthetic AP stats for two Deco nodes."""
    return [
        APStats(
            mac="de:ad:be:ef:00:01",
            name="Living Room Deco",
            channel=36,
            band="5GHz",
            client_count=2,
            noise_floor=-95,
            area_id="living_room",
        ),
        APStats(
            mac="de:ad:be:ef:00:02",
            name="Bedroom Deco",
            channel=6,
            band="2.4GHz",
            client_count=1,
            noise_floor=-98,
            area_id="bedroom",
        ),
    ]


@pytest.fixture
def mock_csi_nodes() -> list[CSINodeInfo]:
    """Synthetic CSI node data for two ESPectre nodes."""
    node1 = CSINodeInfo(
        device_id="espectre_node_001",
        platform="esphome",
        name="Living Room ESPectre",
        area_id="living_room",
        floor_id="ground_floor",
        motion_score_entity_id="sensor.living_room_espectre_motion_score",
        motion_detected_entity_id="binary_sensor.living_room_espectre_motion_detected",
    )
    node2 = CSINodeInfo(
        device_id="espectre_node_002",
        platform="esphome",
        name="Bedroom ESPectre",
        area_id="bedroom",
        floor_id="ground_floor",
        motion_score_entity_id="sensor.bedroom_espectre_motion_score",
        motion_detected_entity_id="binary_sensor.bedroom_espectre_motion_detected",
    )
    return [node1, node2]


@pytest.fixture
def mock_config_entry_deco(hass: HomeAssistant) -> ConfigEntry:
    """Mock config entry for a Deco router setup."""
    entry = MagicMock(spec=ConfigEntry)
    entry.entry_id = "test_entry_deco_001"
    entry.domain = DOMAIN
    entry.data = {
        CONF_ROUTER_TYPE: ROUTER_TYPE_DECO,
        CONF_ROUTER_HOST: "192.168.0.1",
        CONF_ROUTER_PASSWORD: "test_password",
    }
    entry.options = {}
    entry.unique_id = "wifisense_test_deco"
    return entry


@pytest.fixture
def mock_config_entry_no_router(hass: HomeAssistant) -> ConfigEntry:
    """Mock config entry for CSI/vacuum-only setup."""
    entry = MagicMock(spec=ConfigEntry)
    entry.entry_id = "test_entry_nrouter_001"
    entry.domain = DOMAIN
    entry.data = {CONF_ROUTER_TYPE: ROUTER_TYPE_NONE}
    entry.options = {}
    entry.unique_id = "wifisense_test_nrouter"
    return entry


@pytest.fixture
def mock_deco_client(mock_router_clients, mock_ap_stats):
    """Mock DecoClient that returns synthetic data."""
    client = MagicMock()
    client.async_connect = AsyncMock(return_value=True)
    client.async_get_clients = AsyncMock(return_value=mock_router_clients)
    client.async_get_ap_stats = AsyncMock(return_value=mock_ap_stats)
    client.async_disconnect = AsyncMock()
    client.is_connected = True
    return client


@pytest.fixture
def mock_store():
    """Mock HA storage Store."""
    store = MagicMock()
    store.async_load = AsyncMock(return_value=None)
    store.async_save = AsyncMock()
    return store
