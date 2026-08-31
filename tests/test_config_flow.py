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
    """Test options flow menu and general settings."""
    mock_config_entry_no_router.add_to_hass(hass)

    result = await hass.config_entries.options.async_init(
        mock_config_entry_no_router.entry_id
    )
    assert result["type"] == data_entry_flow.FlowResultType.MENU
    assert "general" in result["menu_options"]
    assert "ap_mapping" in result["menu_options"]
    assert "vacuum_mapping" in result["menu_options"]

    # Select general step
    result_general = await hass.config_entries.options.async_configure(
        result["flow_id"],
        {"next_step_id": "general"},
    )
    assert result_general["type"] == data_entry_flow.FlowResultType.FORM
    assert result_general["step_id"] == "general"

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


@pytest.mark.asyncio
async def test_options_flow_ap_mapping(hass: HomeAssistant, mock_config_entry_no_router) -> None:
    """Test options flow AP and Deco placement step."""
    from homeassistant.helpers import area_registry as ar

    area_reg = ar.async_get(hass)
    area_reg.async_create("Office")

    mock_config_entry_no_router.add_to_hass(hass)
    hass.config_entries.async_update_entry(
        mock_config_entry_no_router,
        options={"node_area_map": {"aa:bb:cc:dd:ee:ff": ""}},
    )

    result = await hass.config_entries.options.async_init(
        mock_config_entry_no_router.entry_id
    )
    result_ap = await hass.config_entries.options.async_configure(
        result["flow_id"],
        {"next_step_id": "ap_mapping"},
    )
    assert result_ap["type"] == data_entry_flow.FlowResultType.FORM
    assert result_ap["step_id"] == "ap_mapping"

    # Submit AP mapping using the generated friendly label
    result_saved = await hass.config_entries.options.async_configure(
        result["flow_id"],
        {"Deco Hub (aa:bb:cc:dd:ee:ff)": "office"},
    )
    assert result_saved["type"] == data_entry_flow.FlowResultType.CREATE_ENTRY
    assert result_saved["data"]["node_area_map"]["aa:bb:cc:dd:ee:ff"] == "office"


@pytest.mark.asyncio
async def test_options_flow_ap_mapping_device_registry_friendly_names(
    hass: HomeAssistant, mock_config_entry_no_router
) -> None:
    """Test options flow uses friendly names from Home Assistant Device Registry."""
    mock_config_entry_no_router.add_to_hass(hass)

    from homeassistant.helpers import device_registry as dr

    dev_reg = dr.async_get(hass)
    dev_reg.async_get_or_create(
        config_entry_id=mock_config_entry_no_router.entry_id,
        connections={(dr.CONNECTION_NETWORK_MAC, "b0:a7:b9:bb:2f:ac")},
        name="Kitchen Deco",
    )

    hass.config_entries.async_update_entry(
        mock_config_entry_no_router,
        options={"node_area_map": {"b0:a7:b9:bb:2f:ac": ""}},
    )

    result = await hass.config_entries.options.async_init(
        mock_config_entry_no_router.entry_id
    )
    result_ap = await hass.config_entries.options.async_configure(
        result["flow_id"],
        {"next_step_id": "ap_mapping"},
    )
    assert result_ap["type"] == data_entry_flow.FlowResultType.FORM
    schema_keys = [str(k) for k in result_ap["data_schema"].schema]
    assert any("Kitchen Deco" in k for k in schema_keys)


@pytest.mark.asyncio
async def test_options_flow_vacuum_mapping_with_vacuum_domain_attributes(
    hass: HomeAssistant, mock_config_entry_no_router
) -> None:
    """Test vacuum room segments discovery from a vacuum domain entity's state attributes."""
    from homeassistant.helpers import area_registry as ar

    area_reg = ar.async_get(hass)
    area_reg.async_create("Living Room")

    # Set vacuum state with rooms attribute list
    hass.states.async_set(
        "vacuum.roborock_s7",
        "docked",
        {
            "friendly_name": "Roborock S7",
            "rooms": [
                {"id": 16, "name": "Living Room"},
                {"id": 17, "name": "Kitchen"},
            ],
        },
    )

    mock_config_entry_no_router.add_to_hass(hass)
    result = await hass.config_entries.options.async_init(
        mock_config_entry_no_router.entry_id
    )
    result_vac = await hass.config_entries.options.async_configure(
        result["flow_id"],
        {"next_step_id": "vacuum_mapping"},
    )
    assert result_vac["type"] == data_entry_flow.FlowResultType.FORM
    assert result_vac["step_id"] == "vacuum_mapping"

    # Verify both room segments were discovered
    schema_keys = [str(k) for k in result_vac["data_schema"].schema]
    assert any("Living Room" in k for k in schema_keys)
    assert any("Kitchen" in k for k in schema_keys)

    living_room_key = next(k for k in schema_keys if "Living Room" in k)
    result_saved = await hass.config_entries.options.async_configure(
        result["flow_id"],
        {living_room_key: "living_room"},
    )
    assert result_saved["type"] == data_entry_flow.FlowResultType.CREATE_ENTRY
    assert result_saved["data"]["vacuum_room_mappings"]["16"] == "living_room"



