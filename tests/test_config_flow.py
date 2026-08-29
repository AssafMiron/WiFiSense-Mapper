"""Tests for the WiFiSense Mapper config flow."""
from __future__ import annotations

import pytest
from unittest.mock import AsyncMock, patch

from homeassistant import data_entry_flow
from homeassistant.core import HomeAssistant

from custom_components.wifisense_mapper.const import (
    DOMAIN,
    CONF_ROUTER_TYPE,
    CONF_ROUTER_HOST,
    CONF_ROUTER_PASSWORD,
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
        "custom_components.wifisense_mapper.config_flow.DecoClient.async_connect",
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
            "custom_components.wifisense_mapper.config_flow.DecoClient.async_connect",
            AsyncMock(return_value=True),
        ),
        patch(
            "custom_components.wifisense_mapper.config_flow.DecoClient.async_disconnect",
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
    # Verify no password was stored
    assert result2["data"].get(CONF_ROUTER_PASSWORD, "") == ""


@pytest.mark.asyncio
async def test_options_flow(hass: HomeAssistant, mock_config_entry_no_router) -> None:
    """Test options flow saves updated poll interval."""
    hass.config_entries._entries[mock_config_entry_no_router.entry_id] = mock_config_entry_no_router

    result = await hass.config_entries.options.async_init(
        mock_config_entry_no_router.entry_id
    )
    assert result["type"] == data_entry_flow.FlowResultType.FORM

    result2 = await hass.config_entries.options.async_configure(
        result["flow_id"],
        {"poll_interval": 60, "heatmap_enabled": False, "anomaly_threshold": 4.0, "baseline_days": 14},
    )
    assert result2["type"] == data_entry_flow.FlowResultType.CREATE_ENTRY
    assert result2["data"]["poll_interval"] == 60
    assert result2["data"]["heatmap_enabled"] is False
