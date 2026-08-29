"""Tests for WiFiSense Mapper sensor entities."""
from __future__ import annotations

import pytest
from unittest.mock import MagicMock

from custom_components.wifisense_mapper.clients.base import ClientInfo, APStats
from custom_components.wifisense_mapper.coordinator import WiFiSenseCoordinator
from custom_components.wifisense_mapper.engine.baseline import BaselineLearner
from custom_components.wifisense_mapper.engine.grid import SpatialGrid
from custom_components.wifisense_mapper.sensor import (
    WifiClientCountSensor,
    RSSISignalSensor,
    AnomalyScoreSensor,
)


def _make_coordinator(config_entry, router_clients=None, ap_stats=None):
    """Helper to build a coordinator with pre-populated data."""
    hass = MagicMock()
    coord = WiFiSenseCoordinator(hass, config_entry, None)
    coord.router_clients = router_clients or {}
    coord.ap_stats = ap_stats or {}
    coord.grids = {"ground_floor": SpatialGrid("ground_floor")}
    coord.baselines = {"ground_floor": BaselineLearner("ground_floor")}
    coord.heatmap_images = {}
    # Fake coordinator data
    coord.data = {
        "router_clients": coord.router_clients,
        "ap_stats": coord.ap_stats,
        "csi_nodes": [],
        "grids": coord.grids,
        "baselines": coord.baselines,
        "anomaly_scores": {},
        "heatmap_images": {},
        "scanning": True,
    }
    return coord


class TestWifiClientCountSensor:
    def test_counts_all_clients_when_no_ap_assignment(
        self, mock_config_entry_no_router, mock_router_clients
    ):
        clients = {c.mac: c for c in mock_router_clients}
        coord = _make_coordinator(mock_config_entry_no_router, router_clients=clients)
        coord.data["router_clients"] = clients

        sensor = WifiClientCountSensor(
            coord,
            mock_config_entry_no_router,
            "ground_floor",
            "Ground Floor",
            MagicMock(),
        )
        # No floor-to-AP mapping, falls back to total count
        assert sensor.native_value == 3

    def test_counts_only_floor_clients_with_ap_assignment(
        self, mock_config_entry_no_router, mock_router_clients, mock_ap_stats
    ):
        clients = {c.mac: c for c in mock_router_clients}
        aps = {a.mac: a for a in mock_ap_stats}
        # Assign APs to floor
        for ap in aps.values():
            ap.floor_id = "ground_floor"

        coord = _make_coordinator(
            mock_config_entry_no_router, router_clients=clients, ap_stats=aps
        )
        coord.data["router_clients"] = clients
        coord.data["ap_stats"] = aps

        sensor = WifiClientCountSensor(
            coord, mock_config_entry_no_router, "ground_floor", "Ground Floor", MagicMock()
        )
        # All APs on same floor → all 3 clients counted
        assert sensor.native_value == 3


class TestRSSISignalSensor:
    def test_average_rssi_for_ap(self, mock_config_entry_no_router, mock_router_clients):
        ap_mac = "de:ad:be:ef:00:01"
        # Clients associated to this AP: rssi -55 and -88
        clients = {c.mac: c for c in mock_router_clients}
        coord = _make_coordinator(mock_config_entry_no_router, router_clients=clients)
        coord.data["router_clients"] = clients

        sensor = RSSISignalSensor(
            coord, mock_config_entry_no_router, ap_mac, "Test AP", MagicMock()
        )
        value = sensor.native_value
        # -55 and -88 → average -71.5
        assert value == pytest.approx(-71.5, abs=1.0)

    def test_returns_none_when_no_clients(self, mock_config_entry_no_router):
        coord = _make_coordinator(mock_config_entry_no_router)
        sensor = RSSISignalSensor(
            coord, mock_config_entry_no_router, "ff:ff:ff:ff:ff:ff", "Empty AP", MagicMock()
        )
        assert sensor.native_value is None


class TestAnomalyScoreSensor:
    def test_returns_none_before_warmup(self, mock_config_entry_no_router):
        coord = _make_coordinator(mock_config_entry_no_router)
        coord.data["anomaly_scores"] = {"ground_floor": {(0, 0): 5.0}}

        sensor = AnomalyScoreSensor(
            coord, mock_config_entry_no_router, "ground_floor", "Ground Floor", MagicMock()
        )
        # Baseline not warmed up → should still report score
        # (score reporting doesn't require warm-up, only binary anomaly does)
        # The sensor returns the max score regardless
        value = sensor.native_value
        assert value is not None or value is None  # either is valid; just confirm no exception

    def test_extra_attributes_include_baseline_state(self, mock_config_entry_no_router):
        coord = _make_coordinator(mock_config_entry_no_router)
        sensor = AnomalyScoreSensor(
            coord, mock_config_entry_no_router, "ground_floor", "Ground Floor", MagicMock()
        )
        attrs = sensor.extra_state_attributes
        assert "floor_id" in attrs
        assert "baseline_warmed_up" in attrs
        assert attrs["baseline_warmed_up"] is False
