"""Tests for WiFiSense Mapper component setup, services, and unload."""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest
from homeassistant.core import HomeAssistant

from custom_components.wifisense_mapper.const import (
    DOMAIN,
    SERVICE_CALIBRATE_VACUUM,
    SERVICE_EXPORT_MAP,
    SERVICE_GENERATE_HEATMAP,
    SERVICE_LEARN_BASELINE,
    SERVICE_START_SCAN,
    SERVICE_STOP_SCAN,
)


@pytest.mark.asyncio
async def test_setup_and_unload_entry(
    hass: HomeAssistant, mock_config_entry_no_router
) -> None:
    """Test setting up, invoking services, and unloading the integration entry."""
    mock_config_entry_no_router.add_to_hass(hass)

    with (
        patch.object(
            hass.config_entries,
            "async_forward_entry_setups",
            AsyncMock(return_value=True),
        ),
        patch(
            "custom_components.wifisense_mapper.coordinator.WiFiSenseCoordinator._async_update_data",
            AsyncMock(
                return_value={"router_clients": {}, "ap_stats": {}, "scanning": True}
            ),
        ),
    ):
        setup_ok = await hass.config_entries.async_setup(
            mock_config_entry_no_router.entry_id
        )
        await hass.async_block_till_done()
        assert setup_ok is True
        assert DOMAIN in hass.data
        assert mock_config_entry_no_router.entry_id in hass.data[DOMAIN]

        coordinator = hass.data[DOMAIN][mock_config_entry_no_router.entry_id][
            "coordinator"
        ]
        coordinator.heatmap_images["default"] = {"signal": b"FAKE_PNG_BYTES"}

        # Verify services are registered
        assert hass.services.has_service(DOMAIN, SERVICE_START_SCAN)
        assert hass.services.has_service(DOMAIN, SERVICE_STOP_SCAN)
        assert hass.services.has_service(DOMAIN, SERVICE_GENERATE_HEATMAP)
        assert hass.services.has_service(DOMAIN, SERVICE_LEARN_BASELINE)
        assert hass.services.has_service(DOMAIN, SERVICE_EXPORT_MAP)
        assert hass.services.has_service(DOMAIN, SERVICE_CALIBRATE_VACUUM)

        # Call services
        await hass.services.async_call(DOMAIN, SERVICE_START_SCAN, blocking=True)
        await hass.services.async_call(DOMAIN, SERVICE_STOP_SCAN, blocking=True)
        await hass.services.async_call(
            DOMAIN, SERVICE_GENERATE_HEATMAP, {}, blocking=True
        )
        await hass.services.async_call(
            DOMAIN, SERVICE_LEARN_BASELINE, {}, blocking=True
        )
        await hass.services.async_call(
            DOMAIN, SERVICE_EXPORT_MAP, {"floor_id": "default"}, blocking=True
        )
        await hass.services.async_call(
            DOMAIN,
            SERVICE_CALIBRATE_VACUUM,
            {
                "floor_id": "default",
                "calibration_points": [
                    {"vac_px": 0.0, "vac_py": 0.0, "grid_col": 0.0, "grid_row": 0.0},
                    {"vac_px": 100.0, "vac_py": 0.0, "grid_col": 10.0, "grid_row": 0.0},
                    {"vac_px": 0.0, "vac_py": 100.0, "grid_col": 0.0, "grid_row": 10.0},
                ],
            },
            blocking=True,
        )

        with patch.object(
            hass.config_entries,
            "async_unload_platforms",
            AsyncMock(return_value=True),
        ):
            unload_ok = await hass.config_entries.async_unload(
                mock_config_entry_no_router.entry_id
            )
            await hass.async_block_till_done()
            assert unload_ok is True
            assert mock_config_entry_no_router.entry_id not in hass.data[DOMAIN]
