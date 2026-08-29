"""WiFiSense Mapper — Integration Entry Point.

Handles:
  - async_setup_entry:  Initialize coordinator, platforms, services, storage.
  - async_unload_entry: Clean up coordinator and all platforms.
  - Service handlers for all wifisense_mapper.* services.
"""
from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

import voluptuous as vol
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant, ServiceCall
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers.storage import Store

from .clients.base import RouterClient
from .clients.deco import DecoClient
from .clients.unifi import UniFiClient
from .const import (
    CONF_ROUTER_HOST,
    CONF_ROUTER_PASSWORD,
    CONF_ROUTER_TYPE,
    CONF_ROUTER_USERNAME,
    DOMAIN,
    LAYER_SIGNAL,
    PLATFORMS,
    ROUTER_TYPE_DECO,
    ROUTER_TYPE_UNIFI,
    SERVICE_CALIBRATE_VACUUM,
    SERVICE_EXPORT_MAP,
    SERVICE_GENERATE_HEATMAP,
    SERVICE_LEARN_BASELINE,
    SERVICE_LINK_NODE_AREA,
    SERVICE_START_SCAN,
    SERVICE_STOP_SCAN,
    STORAGE_KEY_BASELINES,
    STORAGE_KEY_CALIBRATION,
    STORAGE_KEY_GRIDS,
    STORAGE_VERSION,
)
from .coordinator import WiFiSenseCoordinator
from .engine.baseline import BaselineLearner
from .engine.grid import SpatialGrid
from .engine.vacuum_align import VacuumMapAligner

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up WiFiSense Mapper from a config entry."""
    hass.data.setdefault(DOMAIN, {})

    # Build router client
    router_client: RouterClient | None = _build_router_client(hass, entry)

    # Initialize coordinator
    coordinator = WiFiSenseCoordinator(hass, entry, router_client)

    # Load persisted state from HA storage
    await _load_persisted_state(hass, entry, coordinator)

    # Run initial discovery (CSI nodes, vacuum sources, floor/area grids)
    await coordinator.async_initialize()

    # First data fetch
    await coordinator.async_config_entry_first_refresh()

    hass.data[DOMAIN][entry.entry_id] = {
        "coordinator": coordinator,
        "stores": _build_stores(hass, entry),
    }

    # Set up platforms
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)

    # Register services (once, even with multiple entries)
    if not hass.services.has_service(DOMAIN, SERVICE_START_SCAN):
        _register_services(hass)

    # Listen for option changes to update coordinator settings
    entry.async_on_unload(entry.add_update_listener(_async_options_update))

    # Schedule periodic state persistence
    async def _save_state_periodic(_now: Any = None) -> None:
        await _save_persisted_state(hass, entry, coordinator)

    from homeassistant.helpers.event import async_track_time_interval  # noqa: PLC0415
    from datetime import timedelta  # noqa: PLC0415

    entry.async_on_unload(
        async_track_time_interval(hass, _save_state_periodic, timedelta(minutes=15))
    )

    _LOGGER.info("WiFiSense Mapper entry %s loaded successfully", entry.entry_id)
    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a config entry and clean up all resources."""
    coordinator: WiFiSenseCoordinator = hass.data[DOMAIN][entry.entry_id]["coordinator"]

    # Save state before unloading
    await _save_persisted_state(hass, entry, coordinator)

    # Disconnect router client
    if coordinator.router_client:
        await coordinator.router_client.async_disconnect()

    # Unload platforms
    unloaded = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)

    if unloaded:
        hass.data[DOMAIN].pop(entry.entry_id)

    # Remove services if no entries remain
    if not hass.data[DOMAIN]:
        for service in [
            SERVICE_START_SCAN,
            SERVICE_STOP_SCAN,
            SERVICE_GENERATE_HEATMAP,
            SERVICE_LEARN_BASELINE,
            SERVICE_CALIBRATE_VACUUM,
            SERVICE_EXPORT_MAP,
            SERVICE_LINK_NODE_AREA,
        ]:
            hass.services.async_remove(DOMAIN, service)

    return unloaded


# ─── Router client factory ────────────────────────────────────────────────────

def _build_router_client(hass: HomeAssistant, entry: ConfigEntry) -> RouterClient | None:
    router_type = entry.data.get(CONF_ROUTER_TYPE, "none")
    if router_type == ROUTER_TYPE_DECO:
        return DecoClient(
            host=entry.data.get(CONF_ROUTER_HOST, ""),
            username=entry.data.get(CONF_ROUTER_USERNAME, "admin"),
            password=entry.data.get(CONF_ROUTER_PASSWORD, ""),
        )
    elif router_type == ROUTER_TYPE_UNIFI:
        return UniFiClient(hass)
    return None


# ─── Storage helpers ──────────────────────────────────────────────────────────

def _build_stores(hass: HomeAssistant, entry: ConfigEntry) -> dict[str, Store]:
    return {
        "grids": Store(hass, STORAGE_VERSION, f"{STORAGE_KEY_GRIDS}.{entry.entry_id}"),
        "baselines": Store(hass, STORAGE_VERSION, f"{STORAGE_KEY_BASELINES}.{entry.entry_id}"),
        "calibration": Store(hass, STORAGE_VERSION, f"{STORAGE_KEY_CALIBRATION}.{entry.entry_id}"),
    }


async def _load_persisted_state(
    hass: HomeAssistant,
    entry: ConfigEntry,
    coordinator: WiFiSenseCoordinator,
) -> None:
    """Load grids, baselines, and calibration from HA storage."""
    stores = _build_stores(hass, entry)

    grid_data = await stores["grids"].async_load() or {}
    for floor_id, gd in grid_data.items():
        try:
            coordinator.grids[floor_id] = SpatialGrid.from_dict(gd)
        except Exception as exc:  # noqa: BLE001
            _LOGGER.warning("Failed to restore grid for floor %s: %s", floor_id, exc)

    baseline_data = await stores["baselines"].async_load() or {}
    for floor_id, bd in baseline_data.items():
        try:
            coordinator.baselines[floor_id] = BaselineLearner.from_dict(bd)
        except Exception as exc:  # noqa: BLE001
            _LOGGER.warning("Failed to restore baseline for floor %s: %s", floor_id, exc)

    calibration_data = await stores["calibration"].async_load() or {}
    for floor_id, cd in calibration_data.items():
        try:
            coordinator.aligners[floor_id] = VacuumMapAligner.from_dict(cd)
        except Exception as exc:  # noqa: BLE001
            _LOGGER.warning("Failed to restore calibration for floor %s: %s", floor_id, exc)

    _LOGGER.debug(
        "Loaded state: %d grids, %d baselines, %d calibrations",
        len(coordinator.grids), len(coordinator.baselines), len(coordinator.aligners),
    )


async def _save_persisted_state(
    hass: HomeAssistant,
    entry: ConfigEntry,
    coordinator: WiFiSenseCoordinator,
) -> None:
    """Persist grids, baselines, and calibration to HA storage."""
    stores = _build_stores(hass, entry)

    await stores["grids"].async_save(
        {fid: grid.to_dict() for fid, grid in coordinator.grids.items()}
    )
    await stores["baselines"].async_save(
        {fid: bl.to_dict() for fid, bl in coordinator.baselines.items()}
    )
    await stores["calibration"].async_save(
        {fid: al.to_dict() for fid, al in coordinator.aligners.items()}
    )
    _LOGGER.debug("WiFiSense state persisted to HA storage")


# ─── Options update listener ──────────────────────────────────────────────────

async def _async_options_update(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Reload integration when options change."""
    _LOGGER.debug("Options updated, reloading WiFiSense entry")
    await hass.config_entries.async_reload(entry.entry_id)


# ─── Service registration ─────────────────────────────────────────────────────

def _register_services(hass: HomeAssistant) -> None:
    """Register all wifisense_mapper services."""

    async def handle_start_scan(call: ServiceCall) -> None:
        for entry_data in hass.data.get(DOMAIN, {}).values():
            entry_data["coordinator"].start_scan()

    async def handle_stop_scan(call: ServiceCall) -> None:
        for entry_data in hass.data.get(DOMAIN, {}).values():
            entry_data["coordinator"].stop_scan()

    async def handle_generate_heatmap(call: ServiceCall) -> None:
        floor_id: str | None = call.data.get("floor_id")
        layer: str = call.data.get("layer", LAYER_SIGNAL)

        for entry_data in hass.data.get(DOMAIN, {}).values():
            coordinator: WiFiSenseCoordinator = entry_data["coordinator"]
            floors_to_render = [floor_id] if floor_id else list(coordinator.grids.keys())

            for fid in floors_to_render:
                grid = coordinator.grids.get(fid)
                if grid is None:
                    continue
                scores = coordinator.baselines.get(fid, BaselineLearner(fid)).compute_anomaly_scores(grid)
                await coordinator._async_render_heatmaps({fid: scores})

            coordinator.async_update_listeners()

    async def handle_learn_baseline(call: ServiceCall) -> None:
        floor_id: str | None = call.data.get("floor_id")

        for entry_data in hass.data.get(DOMAIN, {}).values():
            coordinator: WiFiSenseCoordinator = entry_data["coordinator"]
            floors_to_reset = [floor_id] if floor_id else list(coordinator.baselines.keys())
            for fid in floors_to_reset:
                if fid in coordinator.baselines:
                    coordinator.baselines[fid].reset()
            _LOGGER.info("Baseline reset for floors: %s", floors_to_reset)

    async def handle_calibrate_vacuum(call: ServiceCall) -> None:
        floor_id: str = call.data["floor_id"]
        points_raw = call.data["calibration_points"]

        if isinstance(points_raw, str):
            try:
                points_raw = json.loads(points_raw)
            except json.JSONDecodeError as exc:
                raise HomeAssistantError(f"Invalid calibration_points JSON: {exc}") from exc

        for entry_data in hass.data.get(DOMAIN, {}).values():
            coordinator: WiFiSenseCoordinator = entry_data["coordinator"]
            if floor_id not in coordinator.aligners:
                coordinator.aligners[floor_id] = VacuumMapAligner(floor_id)
            aligner = coordinator.aligners[floor_id]
            aligner.reset_calibration()
            for pt in points_raw:
                aligner.add_calibration_point(
                    vac_px=float(pt["vac_px"]),
                    vac_py=float(pt["vac_py"]),
                    grid_col=float(pt["grid_col"]),
                    grid_row=float(pt["grid_row"]),
                )
            if aligner.is_calibrated:
                _LOGGER.info(
                    "Vacuum map calibrated for floor %s (residual=%.2f cells)",
                    floor_id, aligner.calibration_residual(),
                )
            else:
                raise HomeAssistantError(
                    "Calibration failed. Provide at least 3 non-collinear point pairs."
                )

    async def handle_export_map(call: ServiceCall) -> None:
        floor_id: str = call.data["floor_id"]
        fmt: str = call.data.get("format", "png")
        layer: str = call.data.get("layer", LAYER_SIGNAL)

        for entry_data in hass.data.get(DOMAIN, {}).values():
            coordinator: WiFiSenseCoordinator = entry_data["coordinator"]
            if fmt == "json":
                grid = coordinator.grids.get(floor_id)
                if grid is None:
                    raise HomeAssistantError(f"No grid data for floor: {floor_id}")
                data = grid.to_dict()
                out_path = Path(hass.config.config_dir) / "www" / f"wifisense_{floor_id}.json"
                out_path.parent.mkdir(parents=True, exist_ok=True)
                out_path.write_text(json.dumps(data, indent=2))
                _LOGGER.info("Grid JSON exported to %s", out_path)
            else:
                images = coordinator.heatmap_images.get(floor_id, {})
                png = images.get(layer)
                if not png:
                    raise HomeAssistantError(f"No heatmap rendered for floor={floor_id} layer={layer}")
                out_path = Path(hass.config.config_dir) / "www" / f"wifisense_{floor_id}_{layer}.png"
                out_path.parent.mkdir(parents=True, exist_ok=True)
                out_path.write_bytes(png)
                _LOGGER.info("Heatmap PNG exported to %s", out_path)

    async def handle_link_node_area(call: ServiceCall) -> None:
        node_id: str = call.data["node_id"]
        area_id: str = call.data["area_id"]

        for entry_data in hass.data.get(DOMAIN, {}).values():
            coordinator: WiFiSenseCoordinator = entry_data["coordinator"]
            # Update CSI node
            for node in coordinator.csi_nodes:
                if node.device_id == node_id:
                    node.area_id = area_id
                    _LOGGER.info("CSI node %s linked to area %s", node_id, area_id)
                    return
            # Update AP
            if node_id in coordinator.ap_stats:
                coordinator.ap_stats[node_id].area_id = area_id
                _LOGGER.info("AP %s linked to area %s", node_id, area_id)
                return
            raise HomeAssistantError(f"Node not found: {node_id}")

    hass.services.async_register(DOMAIN, SERVICE_START_SCAN, handle_start_scan)
    hass.services.async_register(DOMAIN, SERVICE_STOP_SCAN, handle_stop_scan)
    hass.services.async_register(
        DOMAIN, SERVICE_GENERATE_HEATMAP, handle_generate_heatmap,
        schema=vol.Schema({
            vol.Optional("floor_id"): cv.string,
            vol.Optional("layer", default=LAYER_SIGNAL): vol.In(["signal", "variance", "motion", "anomaly"]),
        }),
    )
    hass.services.async_register(
        DOMAIN, SERVICE_LEARN_BASELINE, handle_learn_baseline,
        schema=vol.Schema({vol.Optional("floor_id"): cv.string}),
    )
    hass.services.async_register(
        DOMAIN, SERVICE_CALIBRATE_VACUUM, handle_calibrate_vacuum,
        schema=vol.Schema({
            vol.Required("floor_id"): cv.string,
            vol.Required("calibration_points"): vol.Any(str, list),
        }),
    )
    hass.services.async_register(
        DOMAIN, SERVICE_EXPORT_MAP, handle_export_map,
        schema=vol.Schema({
            vol.Required("floor_id"): cv.string,
            vol.Optional("format", default="png"): vol.In(["png", "json"]),
            vol.Optional("layer", default=LAYER_SIGNAL): vol.In(["signal", "variance", "motion", "anomaly"]),
        }),
    )
    hass.services.async_register(
        DOMAIN, SERVICE_LINK_NODE_AREA, handle_link_node_area,
        schema=vol.Schema({
            vol.Required("node_id"): cv.string,
            vol.Required("area_id"): cv.string,
        }),
    )
