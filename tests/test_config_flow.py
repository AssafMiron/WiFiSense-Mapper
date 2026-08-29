"""Tests for the WiFiSense Mapper config flow."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from homeassistant import data_entry_flow
from homeassistant.core import HomeAssistant

from custom_components.wifisense_mapper.const import (
    CONF_ROUTER_HOST,
    CONF_ROUTER_PASSWORD,
    CONF_ROUTER_TYPE,
    CONF_ROUTER_USERNAME,
    DOMAIN,
    ROUTER_TYPE_DECO,
    ROUTER_TYPE_NONE,
    ROUTER_TYPE_UNIFI,
)


@pytest.mark.asyncio
async def test_config_flow_no_router(hass: HomeAssistant) -> None:
    """Test config flow creates entry with no router (CSI-only mode)."""
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": "user"}
    )
    assert result["type"] == data_entry_flow.FlowResultType.FORM
    assert result["step_id"] == "user"

    result2 = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        {CONF_ROUTER_TYPE: ROUTER_TYPE_NONE},
    )
    assert result2["type"] == data_entry_flow.FlowResultType.CREATE_ENTRY
    assert result2["data"][CONF_ROUTER_TYPE] == ROUTER_TYPE_NONE


@pytest.mark.asyncio
async def test_config_flow_deco_connect_fail(hass: HomeAssistant) -> None:
    """Test config flow shows error when Deco connection fails."""
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": "user"}
    )
    await hass.config_entries.flow.async_configure(
        result["flow_id"],
        {CONF_ROUTER_TYPE: ROUTER_TYPE_DECO},
    )

    with patch(
        "custom_components.wifisense_mapper.clients.deco.DecoClient.async_connect",
        AsyncMock(return_value=False),
    ):
        result2 = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            {
                CONF_ROUTER_HOST: "192.168.0.1",
                CONF_ROUTER_PASSWORD: "wrong_pass",
            },
        )
    assert result2["type"] == data_entry_flow.FlowResultType.FORM
    assert "cannot_connect" in result2["errors"].get("base", "")


@pytest.mark.asyncio
async def test_config_flow_deco_success(hass: HomeAssistant) -> None:
    """Test config flow creates entry when Deco connection succeeds."""
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": "user"}
    )
    await hass.config_entries.flow.async_configure(
        result["flow_id"],
        {CONF_ROUTER_TYPE: ROUTER_TYPE_DECO},
    )

    with (
        patch(
            "custom_components.wifisense_mapper.clients.deco.DecoClient.async_connect",
            AsyncMock(return_value=True),
        ),
        patch(
            "custom_components.wifisense_mapper.clients.deco.DecoClient.async_disconnect",
            AsyncMock(),
        ),
    ):
        result2 = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            {CONF_ROUTER_HOST: "192.168.0.1", CONF_ROUTER_PASSWORD: "valid_pass"},
        )

    assert result2["type"] == data_entry_flow.FlowResultType.CREATE_ENTRY
    assert result2["data"][CONF_ROUTER_HOST] == "192.168.0.1"


@pytest.mark.asyncio
async def test_config_flow_deco_auto_detected_1_click_success(
    hass: HomeAssistant,
) -> None:
    """Test 1-click zero-credential setup when Deco is auto-detected in HA."""
    mock_entry = MagicMock()
    mock_entry.entry_id = "tplink_entry_abc"
    mock_entry.title = "http://192.168.1.246"
    mock_entry.data = {
        "host": "192.168.1.246",
        "username": "admin",
        "password": "saved_password",
    }
    mock_entry.options = {}

    with pytest.MonkeyPatch.context() as mp:
        orig_entries = hass.config_entries.async_entries

        def custom_entries(domain: str, *args, **kwargs):
            if domain == "tplink_deco":
                return [mock_entry]
            return orig_entries(domain, *args, **kwargs)

        mp.setattr(hass.config_entries, "async_entries", custom_entries)

        result = await hass.config_entries.flow.async_init(
            DOMAIN, context={"source": "user"}
        )
        assert result["type"] == data_entry_flow.FlowResultType.FORM
        opt_key = f"auto_{ROUTER_TYPE_DECO}_tplink_deco_{mock_entry.entry_id}"

        with (
            patch(
                "custom_components.wifisense_mapper.clients.deco.DecoClient.async_connect",
                AsyncMock(return_value=True),
            ),
            patch(
                "custom_components.wifisense_mapper.clients.deco.DecoClient.async_disconnect",
                AsyncMock(),
            ),
        ):
            result2 = await hass.config_entries.flow.async_configure(
                result["flow_id"],
                {CONF_ROUTER_TYPE: opt_key},
            )

        assert result2["type"] == data_entry_flow.FlowResultType.CREATE_ENTRY
        assert result2["data"][CONF_ROUTER_TYPE] == ROUTER_TYPE_DECO
        assert result2["data"][CONF_ROUTER_HOST] == "192.168.1.246"
        assert result2["data"][CONF_ROUTER_USERNAME] == "admin"
        assert result2["data"][CONF_ROUTER_PASSWORD] == "saved_password"


@pytest.mark.asyncio
async def test_config_flow_deco_auto_detected_fallback_on_connect_fail(
    hass: HomeAssistant,
) -> None:
    """Test fallback to manual router step if auto-connection fails."""
    mock_entry = MagicMock()
    mock_entry.entry_id = "tplink_entry_xyz"
    mock_entry.title = "http://192.168.1.246"
    mock_entry.data = {
        "host": "192.168.1.246",
        "username": "admin",
        "password": "old_password",
    }
    mock_entry.options = {}

    with pytest.MonkeyPatch.context() as mp:
        orig_entries = hass.config_entries.async_entries

        def custom_entries(domain: str, *args, **kwargs):
            if domain == "tplink_deco":
                return [mock_entry]
            return orig_entries(domain, *args, **kwargs)

        mp.setattr(hass.config_entries, "async_entries", custom_entries)

        result = await hass.config_entries.flow.async_init(
            DOMAIN, context={"source": "user"}
        )
        opt_key = f"auto_{ROUTER_TYPE_DECO}_tplink_deco_{mock_entry.entry_id}"

        with patch(
            "custom_components.wifisense_mapper.clients.deco.DecoClient.async_connect",
            AsyncMock(return_value=False),
        ):
            result2 = await hass.config_entries.flow.async_configure(
                result["flow_id"],
                {CONF_ROUTER_TYPE: opt_key},
            )

        # Should fall back to the router step form with errors
        assert result2["type"] == data_entry_flow.FlowResultType.FORM
        assert result2["step_id"] == "router"
        assert "cannot_connect" in result2["errors"].get("base", "")


@pytest.mark.asyncio
async def test_config_flow_deco_manual_with_smart_prefill(
    hass: HomeAssistant,
) -> None:
    """Test manual Deco setup pre-fills detected IP from HA."""
    mock_entry = MagicMock()
    mock_entry.entry_id = "tplink_entry_123"
    mock_entry.title = "http://192.168.1.246"
    mock_entry.data = {"host": "192.168.1.246", "username": "admin"}
    mock_entry.options = {}

    with pytest.MonkeyPatch.context() as mp:
        orig_entries = hass.config_entries.async_entries

        def custom_entries(domain: str, *args, **kwargs):
            if domain == "tplink_deco":
                return [mock_entry]
            return orig_entries(domain, *args, **kwargs)

        mp.setattr(hass.config_entries, "async_entries", custom_entries)

        result = await hass.config_entries.flow.async_init(
            DOMAIN, context={"source": "user"}
        )
        result2 = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            {CONF_ROUTER_TYPE: ROUTER_TYPE_DECO},
        )
        assert result2["type"] == data_entry_flow.FlowResultType.FORM
        assert result2["step_id"] == "router"

        # Check schema description suggested_value for host
        schema = result2["data_schema"].schema
        host_key = next(k for k in schema if k == CONF_ROUTER_HOST)
        assert host_key.description.get("suggested_value") == "192.168.1.246"


@pytest.mark.asyncio
async def test_config_flow_unifi_no_credentials(hass: HomeAssistant) -> None:
    """Test UniFi flow creates entry without credentials (bridge mode)."""
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": "user"}
    )
    result2 = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        {CONF_ROUTER_TYPE: ROUTER_TYPE_UNIFI},
    )
    assert result2["type"] == data_entry_flow.FlowResultType.CREATE_ENTRY
    assert result2["data"].get(CONF_ROUTER_PASSWORD, "") == ""


@pytest.mark.asyncio
async def test_options_flow(hass: HomeAssistant, mock_config_entry_no_router) -> None:
    """Test options flow saves updated poll interval."""
    mock_config_entry_no_router.add_to_hass(hass)

    result = await hass.config_entries.options.async_init(
        mock_config_entry_no_router.entry_id
    )
    assert result["type"] == data_entry_flow.FlowResultType.FORM

    result2 = await hass.config_entries.options.async_configure(
        result["flow_id"],
        {
            "poll_interval": 60,
            "heatmap_enabled": False,
            "anomaly_threshold": 4.0,
            "baseline_days": 14,
        },
    )
    assert result2["type"] == data_entry_flow.FlowResultType.CREATE_ENTRY
    assert result2["data"]["poll_interval"] == 60
    assert result2["data"]["heatmap_enabled"] is False
