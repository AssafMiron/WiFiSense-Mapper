"""Tests for the extensible router discovery engine."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest
from homeassistant.core import HomeAssistant

from custom_components.wifisense_mapper.const import (
    ROUTER_TYPE_DECO,
    ROUTER_TYPE_UNIFI,
)
from custom_components.wifisense_mapper.router_discovery import (
    DecoDiscoveryProvider,
    UniFiDiscoveryProvider,
    clean_host,
    discover_all_routers,
    find_discovered_router,
    get_first_discovered_router_of_type,
)


def test_clean_host() -> None:
    """Test URL and IP cleaning helper."""
    assert clean_host("http://192.168.1.246") == "192.168.1.246"
    assert clean_host("https://192.168.0.1:8080/") == "192.168.0.1"
    assert clean_host("192.168.1.1") == "192.168.1.1"
    assert clean_host("http://192.168.1.246:80/deco") == "192.168.1.246"
    assert clean_host("deco.lan") == "deco.lan"
    assert clean_host("") == ""
    assert clean_host(None) == ""


@pytest.mark.asyncio
async def test_deco_discovery_provider(hass: HomeAssistant) -> None:
    """Test discovery of TP-Link Deco config entries."""
    mock_entry = MagicMock()
    mock_entry.entry_id = "deco_entry_123"
    mock_entry.title = "http://192.168.1.246"
    mock_entry.data = {
        "host": "192.168.1.246",
        "username": "admin",
        "password": "secret_password",
    }
    mock_entry.options = {}

    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(
            hass.config_entries,
            "async_entries",
            lambda domain, *args, **kwargs: [mock_entry] if domain == "tplink_deco" else [],
        )

        provider = DecoDiscoveryProvider()
        discovered = provider.discover(hass)

        assert len(discovered) == 1
        router = discovered[0]
        assert router.router_type == ROUTER_TYPE_DECO
        assert router.integration_domain == "tplink_deco"
        assert router.entry_id == "deco_entry_123"
        assert router.host == "192.168.1.246"
        assert router.username == "admin"
        assert router.password == "secret_password"
        assert router.is_bridge_only is False


@pytest.mark.asyncio
async def test_unifi_discovery_provider(hass: HomeAssistant) -> None:
    """Test discovery of UniFi Network config entries."""
    mock_entry = MagicMock()
    mock_entry.entry_id = "unifi_entry_456"
    mock_entry.title = "UniFi Dream Machine"
    mock_entry.data = {"host": "192.168.1.1"}
    mock_entry.options = {}

    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(
            hass.config_entries,
            "async_entries",
            lambda domain, *args, **kwargs: [mock_entry] if domain == "unifi" else [],
        )

        provider = UniFiDiscoveryProvider()
        discovered = provider.discover(hass)

        assert len(discovered) == 1
        router = discovered[0]
        assert router.router_type == ROUTER_TYPE_UNIFI
        assert router.integration_domain == "unifi"
        assert router.entry_id == "unifi_entry_456"
        assert router.is_bridge_only is True


@pytest.mark.asyncio
async def test_discover_all_routers_and_helpers(hass: HomeAssistant) -> None:
    """Test aggregating discovered routers and finding by ID."""
    mock_deco_entry = MagicMock()
    mock_deco_entry.entry_id = "deco_1"
    mock_deco_entry.title = "http://192.168.1.246"
    mock_deco_entry.data = {"host": "192.168.1.246", "password": "pass"}
    mock_deco_entry.options = {}

    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(
            hass.config_entries,
            "async_entries",
            lambda domain, *args, **kwargs: [mock_deco_entry] if domain == "tplink_deco" else [],
        )

        routers = discover_all_routers(hass)
        assert len(routers) == 1

        target_id = routers[0].discovery_id
        found = find_discovered_router(hass, target_id)
        assert found is not None
        assert found.host == "192.168.1.246"

        first_deco = get_first_discovered_router_of_type(hass, ROUTER_TYPE_DECO)
        assert first_deco is not None
        assert first_deco.host == "192.168.1.246"

        none_found = get_first_discovered_router_of_type(hass, "non_existent_type")
        assert none_found is None
