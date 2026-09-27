"""Tests for Deco anchors step in Options Flow."""

from __future__ import annotations

from unittest.mock import patch

import pytest
from homeassistant import data_entry_flow
from homeassistant.core import HomeAssistant

from custom_components.wifisense_mapper.const import CONF_DECO_ANCHORS


@pytest.mark.asyncio
async def test_deco_anchors_options_step(hass: HomeAssistant, mock_config_entry_no_router) -> None:
    """Test displaying and submitting the Deco radio anchors form."""
    from homeassistant.helpers import area_registry as ar

    area_reg = ar.async_get(hass)
    area_reg.async_create("Living Room")

    mock_config_entry_no_router.add_to_hass(hass)

    result = await hass.config_entries.options.async_init(
        mock_config_entry_no_router.entry_id
    )
    assert result["type"] == data_entry_flow.FlowResultType.MENU
    assert "deco_anchors" in result["menu_options"]

    with patch(
        "custom_components.wifisense_mapper.config_flow.WiFiSenseOptionsFlow._get_known_aps",
        return_value={"11:22:33:44:55:01": "Living Room Deco"},
    ):
        result_step = await hass.config_entries.options.async_configure(
            result["flow_id"],
            {"next_step_id": "deco_anchors"},
        )
        assert result_step["type"] == data_entry_flow.FlowResultType.FORM
        assert result_step["step_id"] == "deco_anchors"

        # Submit anchor configuration
        user_input = {
            "area_11_22_33_44_55_01": "living_room",
            "x_pct_11_22_33_44_55_01": 45.0,
            "y_pct_11_22_33_44_55_01": 60.0,
        }
        submit_result = await hass.config_entries.options.async_configure(
            result["flow_id"],
            user_input,
        )
        assert submit_result["type"] == data_entry_flow.FlowResultType.CREATE_ENTRY
        data = submit_result["data"]
        assert CONF_DECO_ANCHORS in data
        assert "11:22:33:44:55:01" in data[CONF_DECO_ANCHORS]
        assert data[CONF_DECO_ANCHORS]["11:22:33:44:55:01"]["area_id"] == "living_room"
        assert data[CONF_DECO_ANCHORS]["11:22:33:44:55:01"]["x_pct"] == 45.0
        assert data[CONF_DECO_ANCHORS]["11:22:33:44:55:01"]["y_pct"] == 60.0
