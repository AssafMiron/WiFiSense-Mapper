"""Tests for the TP-Link Deco client."""

from __future__ import annotations

from ipaddress import IPv4Address
from unittest.mock import MagicMock, patch

import pytest
from macaddress import EUI48  # type: ignore[import-untyped]
from tplinkrouterc6u.common.dataclass import (  # type: ignore[import-untyped]
    Device,
    Firmware,
    Status,
)
from tplinkrouterc6u.common.package_enum import (  # type: ignore[import-untyped]
    Connection,
)

from custom_components.wifisense_mapper.clients.deco import DecoClient


def test_deco_client_init() -> None:
    """Test DecoClient initialization."""
    client = DecoClient("192.168.0.1", "admin", "secret_pass")
    assert client.host == "192.168.0.1"
    assert client.username == "admin"
    assert client.password == "secret_pass"
    assert not client.is_connected


def test_deco_connect_sync_passes_username_and_ssl() -> None:
    """Test that _connect_sync passes username and verify_ssl=False to TPLinkDecoClient."""
    client = DecoClient("192.168.0.1", "custom_user", "secret_pass")

    with patch(
        "tplinkrouterc6u.TPLinkDecoClient"
    ) as mock_tplink_cls:
        mock_instance = MagicMock()
        mock_tplink_cls.return_value = mock_instance

        client._connect_sync()

        mock_tplink_cls.assert_called_once_with(
            "192.168.0.1",
            "secret_pass",
            username="custom_user",
            verify_ssl=False,
        )
        mock_instance.authorize.assert_called_once()
        assert client._client == mock_instance


def test_deco_get_clients_sync_with_real_dataclasses() -> None:
    """Test _get_clients_sync maps tplinkrouterc6u Status and Device dataclasses."""
    client = DecoClient("192.168.0.1", "admin", "secret_pass")
    client._client = MagicMock()

    # Cached mesh nodes for AP name resolution
    client._client.devices = [
        {
            "mac": "11-22-33-44-55-01",
            "custom_name": "Deco-LivingRoom",
            "device_model": "Deco M5",
        }
    ]

    status = Status()
    dev1 = Device(
        type=Connection.HOST_5G,
        _macaddr=EUI48("AA-BB-CC-DD-EE-01"),
        _ipaddr=IPv4Address("192.168.68.50"),
        hostname="iPhone-Alice",
    )
    dev1.signal = -55
    dev1.ap_name = "Deco-LivingRoom"
    dev1.ssid = "HomeWiFi"
    dev1.frequency = "5GHz"
    dev1.down_speed = 1200
    dev1.up_speed = 300

    dev2 = Device(
        type=Connection.HOST_2G,
        _macaddr=EUI48("AA-BB-CC-DD-EE-02"),
        _ipaddr=IPv4Address("192.168.68.51"),
        hostname="Smart-Plug",
    )
    dev2.signal = -70
    dev2.ap_name = "AA-BB-CC-DD-00-01"
    dev2.ssid = "HomeWiFi"
    # frequency left None to test Connection.get_band() fallback

    status.devices = [dev1, dev2]
    client._client.get_status.return_value = status

    clients = client._get_clients_sync()

    assert len(clients) == 2

    # Verify dev1 (ap_name 'Deco-LivingRoom' resolved to AP MAC '11:22:33:44:55:01')
    c1 = clients[0]
    assert c1.mac == "aa:bb:cc:dd:ee:01"
    assert c1.ip == "192.168.68.50"
    assert c1.hostname == "iPhone-Alice"
    assert c1.rssi == -55
    assert c1.ap_mac == "11:22:33:44:55:01"
    assert c1.ssid == "HomeWiFi"
    assert c1.band == "5GHz"
    assert c1.extra["via_deco"] is True
    assert c1.extra["down_speed"] == 1200
    assert c1.extra["up_speed"] == 300
    assert c1.extra["client_type"] == "host_5g"

    # Verify dev2
    c2 = clients[1]
    assert c2.mac == "aa:bb:cc:dd:ee:02"
    assert c2.ip == "192.168.68.51"
    assert c2.hostname == "Smart-Plug"
    assert c2.rssi == -70
    assert c2.ap_mac == "aa:bb:cc:dd:00:01"
    assert c2.band == "2.4GHz"
    assert c2.extra["client_type"] == "host_2g"


def test_deco_get_ap_stats_sync_mesh_nodes() -> None:
    """Test _get_ap_stats_sync retrieves AP nodes and computes client counts."""
    client = DecoClient("192.168.0.1", "admin", "secret_pass")
    client._client = MagicMock()

    # Mock get_firmware populating self.devices
    mesh_devices = [
        {
            "mac": "11-22-33-44-55-01",
            "device_model": "Deco M5",
            "custom_name": "Deco-LivingRoom",
            "role": "master",
            "hardware_ver": "3.0",
            "software_ver": "1.5.7",
            "ip": "192.168.68.1",
        },
        {
            "mac": "11-22-33-44-55-02",
            "device_model": "Deco M5",
            "custom_name": "Deco-Bedroom",
            "role": "slave",
            "hardware_ver": "3.0",
            "software_ver": "1.5.7",
            "ip": "192.168.68.2",
        },
    ]
    client._client.devices = mesh_devices

    # Connected clients
    status = Status()
    dev1 = Device(
        type=Connection.HOST_5G,
        _macaddr=EUI48("AA-BB-CC-DD-EE-01"),
        _ipaddr=IPv4Address("192.168.68.50"),
        hostname="iPhone",
    )
    dev1.ap_name = "Deco-LivingRoom"

    dev2 = Device(
        type=Connection.HOST_5G,
        _macaddr=EUI48("AA-BB-CC-DD-EE-02"),
        _ipaddr=IPv4Address("192.168.68.51"),
        hostname="iPad",
    )
    dev2.ap_name = "11-22-33-44-55-01"

    dev3 = Device(
        type=Connection.HOST_2G,
        _macaddr=EUI48("AA-BB-CC-DD-EE-03"),
        _ipaddr=IPv4Address("192.168.68.52"),
        hostname="Lamp",
    )
    dev3.ap_name = "11:22:33:44:55:02"

    status.devices = [dev1, dev2, dev3]
    client._client.get_status.return_value = status

    ap_stats = client._get_ap_stats_sync()

    assert len(ap_stats) == 2

    # Node 1 (Living Room) should have 2 matching clients (dev1 by custom_name, dev2 by mac)
    ap1 = ap_stats[0]
    assert ap1.mac == "11:22:33:44:55:01"
    assert ap1.name == "Deco-LivingRoom"
    assert ap1.client_count == 2
    assert ap1.extra["role"] == "master"
    assert ap1.extra["hardware_ver"] == "3.0"
    assert ap1.extra["software_ver"] == "1.5.7"

    # Node 2 (Bedroom) should have 1 matching client (dev3 by mac)
    ap2 = ap_stats[1]
    assert ap2.mac == "11:22:33:44:55:02"
    assert ap2.name == "Deco-Bedroom"
    assert ap2.client_count == 1
    assert ap2.extra["role"] == "slave"


def test_deco_get_ap_stats_single_node_attribution() -> None:
    """Test single mesh node attributes all connected clients even without ap_name match."""
    client = DecoClient("192.168.0.1", "admin", "secret_pass")
    client._client = MagicMock()

    client._client.devices = [
        {
            "mac": "11-22-33-44-55-01",
            "device_model": "Deco M5",
            "role": "master",
        }
    ]

    status = Status()
    dev1 = Device(
        type=Connection.HOST_5G,
        _macaddr=EUI48("AA-BB-CC-DD-EE-01"),
        _ipaddr=IPv4Address("192.168.68.50"),
        hostname="Phone",
    )
    dev1.ap_name = None
    status.devices = [dev1]
    client._client.get_status.return_value = status

    ap_stats = client._get_ap_stats_sync()
    assert len(ap_stats) == 1
    assert ap_stats[0].client_count == 1


def test_deco_get_ap_stats_fallback_to_lan_mac() -> None:
    """Test fallback to LAN MAC if devices list is empty."""
    client = DecoClient("192.168.0.1", "admin", "secret_pass")
    client._client = MagicMock()
    client._client.devices = []
    client._client.get_firmware.return_value = Firmware("1.0", "M5", "1.0")

    status = Status(_lan_macaddr=EUI48("11-22-33-44-55-99"))
    status.devices = [
        Device(
            type=Connection.HOST_5G,
            _macaddr=EUI48("AA-BB-CC-DD-EE-01"),
            _ipaddr=IPv4Address("192.168.68.50"),
            hostname="Phone",
        )
    ]
    client._client.get_status.return_value = status

    ap_stats = client._get_ap_stats_sync()
    assert len(ap_stats) == 1
    assert ap_stats[0].mac == "11:22:33:44:55:99"
    assert ap_stats[0].name == "Deco Master"
    assert ap_stats[0].client_count == 1


@pytest.mark.asyncio
async def test_deco_async_methods() -> None:
    """Test async wrappers (async_connect, async_get_clients, async_get_ap_stats, async_disconnect)."""
    client = DecoClient("192.168.0.1", "admin", "secret_pass")

    def fake_connect():
        client._client = MagicMock()

    with patch.object(client, "_connect_sync", side_effect=fake_connect) as mock_connect_sync:
        success = await client.async_connect()
        assert success is True
        assert client.is_connected is True
        mock_connect_sync.assert_called_once()

    with patch.object(client, "_get_clients_sync", return_value=[]) as mock_get_clients:
        res = await client.async_get_clients()
        assert res == []
        mock_get_clients.assert_called_once()

    with patch.object(client, "_get_ap_stats_sync", return_value=[]) as mock_get_ap_stats:
        res_ap = await client.async_get_ap_stats()
        assert res_ap == []
        mock_get_ap_stats.assert_called_once()

    await client.async_disconnect()
    assert client.is_connected is False
    assert client._client is None
