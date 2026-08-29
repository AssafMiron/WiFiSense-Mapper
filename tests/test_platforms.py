"""Tests for WiFiSense Mapper platforms and client bridges."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest
from homeassistant.helpers.device_registry import DeviceInfo

from custom_components.wifisense_mapper.binary_sensor import (
    CSIMotionBinarySensor,
    ObjectAnomalyBinarySensor,
    PresenceBinarySensor,
)
from custom_components.wifisense_mapper.clients.base import APStats, ClientInfo
from custom_components.wifisense_mapper.clients.unifi import UniFiClient
from custom_components.wifisense_mapper.coordinator import WiFiSenseCoordinator
from custom_components.wifisense_mapper.device_tracker import WifiSenseDeviceTracker
from custom_components.wifisense_mapper.image import HeatmapImageEntity


def _make_coordinator(mock_config_entry_no_router) -> WiFiSenseCoordinator:
    hass = MagicMock()
    hass.states.get = MagicMock(return_value=None)
    coord = WiFiSenseCoordinator(hass, mock_config_entry_no_router, None)
    clients = {
        "aa:bb:cc:dd:ee:01": ClientInfo(
            mac="aa:bb:cc:dd:ee:01",
            ip="192.168.1.101",
            hostname="Alice Phone",
            rssi=-60,
            ap_mac="de:ad:be:ef:00:01",
        )
    }
    ap_stats = {
        "de:ad:be:ef:00:01": APStats(
            mac="de:ad:be:ef:00:01",
            name="AP 1",
            area_id="living_room",
            floor_id="ground_floor",
        )
    }
    coord.router_clients = clients
    coord.ap_stats = ap_stats
    coord.data = {
        "router_clients": clients,
        "ap_stats": ap_stats,
        "csi_nodes": [],
        "heatmap_images": {"ground_floor": {"signal": b"FAKE_PNG"}},
        "scanning": True,
    }
    return coord


class TestBinarySensors:
    """Test binary sensor states and attributes."""

    def test_object_anomaly_sensor(self, mock_config_entry_no_router):
        coord = _make_coordinator(mock_config_entry_no_router)
        dev_info = DeviceInfo(identifiers={("wifisense_mapper", "test_floor")})
        sensor = ObjectAnomalyBinarySensor(
            coord,
            mock_config_entry_no_router,
            "ground_floor",
            "Ground Floor",
            3.0,
            dev_info,
        )
        assert sensor.is_on is False
        assert sensor.extra_state_attributes["threshold"] == 3.0

    def test_csi_motion_sensor(self, mock_config_entry_no_router):
        coord = _make_coordinator(mock_config_entry_no_router)
        dev_info = DeviceInfo(identifiers={("wifisense_mapper", "test_floor")})
        sensor = CSIMotionBinarySensor(
            coord, mock_config_entry_no_router, "ground_floor", "Ground Floor", dev_info
        )
        assert sensor.is_on is False

    def test_presence_sensor(self, mock_config_entry_no_router):
        coord = _make_coordinator(mock_config_entry_no_router)
        dev_info = DeviceInfo(identifiers={("wifisense_mapper", "test_area")})
        sensor = PresenceBinarySensor(
            coord, mock_config_entry_no_router, "living_room", "Living Room", dev_info
        )
        assert (
            sensor.is_on is True
        )  # Client aa:bb:cc:dd:ee:01 is associated to AP in living_room


class TestImageEntity:
    """Test image platform entities."""

    @pytest.mark.asyncio
    async def test_heatmap_image_bytes(self, mock_config_entry_no_router):
        coord = _make_coordinator(mock_config_entry_no_router)
        dev_info = DeviceInfo(identifiers={("wifisense_mapper", "test_floor")})
        image_entity = HeatmapImageEntity(
            coord,
            mock_config_entry_no_router,
            "ground_floor",
            "Ground Floor",
            "signal",
            dev_info,
        )
        image_bytes = await image_entity.async_image()
        assert image_bytes == b"FAKE_PNG"


class TestDeviceTracker:
    """Test room-level device tracker entity."""

    def test_device_tracker_properties(self, mock_config_entry_no_router):
        coord = _make_coordinator(mock_config_entry_no_router)
        dev_info = DeviceInfo(
            identifiers={("wifisense_mapper", "tracker_aa:bb:cc:dd:ee:01")}
        )
        tracker = WifiSenseDeviceTracker(
            coord, mock_config_entry_no_router, "aa:bb:cc:dd:ee:01", dev_info
        )
        assert tracker.is_connected is True
        assert tracker.name == "Alice Phone"
        assert tracker.location_name == "living_room"
        assert tracker.source_type == "router"
        assert tracker.latitude is None
        assert tracker.device_info == dev_info


class TestUniFiClient:
    """Test UniFi integration bridge client."""

    @pytest.mark.asyncio
    async def test_unifi_connect_not_loaded(self):
        hass = MagicMock()
        hass.config.components = set()
        client = UniFiClient(hass)
        assert await client.async_connect() is False
        clients = await client.async_get_clients()
        assert clients == []

    @pytest.mark.asyncio
    async def test_unifi_connect_loaded(self):
        hass = MagicMock()
        hass.config.components = {"unifi"}
        client = UniFiClient(hass)
        assert await client.async_connect() is True
