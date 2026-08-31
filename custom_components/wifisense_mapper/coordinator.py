"""WiFiSense Mapper — Central DataUpdateCoordinator.

Orchestrates all data collection: router polling, CSI state reading,
vacuum map fetching, and spatial engine updates.

Update cycle (runs every ``poll_interval`` seconds):
  1. Poll router client → update router_clients dict.
  2. Query CSI node states from hass.states.
  3. Feed RSSI + CSI data into SpatialGrid per floor.
  4. Update BaselineLearner for each floor.
  5. Compute anomaly scores.
  6. (Optional) Fetch vacuum map image bytes.
  7. (Optional) Trigger async heatmap render in executor.

Thread safety:
  All state mutations happen inside the async coordinator update,
  which is serialized by the HA event loop. Executor jobs (heatmap
  rendering, router polling via tplinkrouterc6u) run in threads but
  only produce data that is consumed after they return.
"""

from __future__ import annotations

import logging
from datetime import timedelta
from typing import TYPE_CHECKING, Any

from homeassistant.core import HomeAssistant
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator

from .clients.base import APStats, ClientInfo
from .const import (
    DEFAULT_ANOMALY_THRESHOLD,
    DEFAULT_GRID_RESOLUTION,
    DEFAULT_POLL_INTERVAL,
    DOMAIN,
    LAYER_ANOMALY,
    LAYER_MOTION,
    LAYER_SIGNAL,
    LAYER_VARIANCE,
)
from .csi_discovery import CSINodeInfo, discover_csi_nodes
from .engine.baseline import BaselineLearner
from .engine.grid import SpatialGrid
from .engine.heatmap import HeatmapRenderer
from .engine.vacuum_align import VacuumMapAligner
from .registry_helpers import get_all_floors, get_floor_for_area
from .vacuum_helpers import VacuumMapSource, discover_vacuum_maps

if TYPE_CHECKING:
    from homeassistant.config_entries import ConfigEntry

    from .clients.base import RouterClient

_LOGGER = logging.getLogger(__name__)


class WiFiSenseCoordinator(DataUpdateCoordinator[dict[str, Any]]):
    """Central coordinator for all WiFiSense Mapper data."""

    def __init__(
        self,
        hass: HomeAssistant,
        entry: ConfigEntry,
        router_client: RouterClient | None,
    ) -> None:
        poll_interval = entry.options.get(
            "poll_interval",
            entry.data.get("poll_interval", DEFAULT_POLL_INTERVAL),
        )
        super().__init__(
            hass,
            _LOGGER,
            name=DOMAIN,
            update_interval=timedelta(seconds=poll_interval),
            config_entry=entry,
        )

        self.entry = entry
        self.router_client = router_client

        self.anomaly_threshold: float = entry.options.get(
            "anomaly_threshold", DEFAULT_ANOMALY_THRESHOLD
        )
        self.heatmap_enabled: bool = entry.options.get("heatmap_enabled", True)

        # Live data
        self.router_clients: dict[str, ClientInfo] = {}  # mac → ClientInfo
        self.ap_stats: dict[str, APStats] = {}  # mac → APStats
        self.csi_nodes: list[CSINodeInfo] = []
        self.vacuum_sources: list[VacuumMapSource] = []

        # Spatial engine — keyed by floor_id
        self.grids: dict[str, SpatialGrid] = {}
        self.baselines: dict[str, BaselineLearner] = {}
        self.aligners: dict[str, VacuumMapAligner] = {}
        self.heatmap_images: dict[str, dict[str, bytes]] = {}
        # floor_id → {layer_name → PNG bytes}

        self._renderer = HeatmapRenderer()
        self._scanning: bool = True

    # ─── Initial setup ────────────────────────────────────────────────────────

    async def async_initialize(self) -> None:
        """Discover nodes, initialize grids, and load persisted state."""
        _LOGGER.debug("WiFiSenseCoordinator: initializing")

        # Discover CSI nodes
        self.csi_nodes = discover_csi_nodes(self.hass)
        _LOGGER.info("Found %d CSI node(s)", len(self.csi_nodes))

        # Discover vacuum map sources
        self.vacuum_sources = discover_vacuum_maps(self.hass)
        _LOGGER.info("Found %d vacuum map source(s)", len(self.vacuum_sources))

        # Initialize grids per floor
        floors = get_all_floors(self.hass)
        for floor in floors:
            if floor.floor_id not in self.grids:
                self.grids[floor.floor_id] = SpatialGrid(
                    floor_id=floor.floor_id,
                    resolution_m=DEFAULT_GRID_RESOLUTION,
                )
            if floor.floor_id not in self.baselines:
                self.baselines[floor.floor_id] = BaselineLearner(floor.floor_id)

        # Fallback: if no floors defined, use a single "default" grid
        if not floors:
            _LOGGER.warning(
                "No floors defined in HA. Using a single default grid. "
                "Consider defining Floors and Areas in Settings → Areas & Zones."
            )
            self.grids.setdefault("default", SpatialGrid(floor_id="default"))
            self.baselines.setdefault("default", BaselineLearner("default"))

        # Set CSI node positions from area assignments
        self._update_node_positions()

    # ─── Main update cycle ────────────────────────────────────────────────────

    async def _async_update_data(self) -> dict[str, Any]:
        """Fetch new data from all sources and update spatial state."""
        if not self._scanning:
            return self._current_data()

        # 1. Poll router
        if self.router_client:
            try:
                clients = await self.router_client.async_get_clients()
                self.router_clients = {c.mac: c for c in clients}
                ap_list = await self.router_client.async_get_ap_stats()
                self.ap_stats = {a.mac: a for a in ap_list}
                self._apply_ap_mappings()
            except Exception as exc:  # noqa: BLE001
                _LOGGER.warning("Router poll failed: %s", exc)

        # 2. Read CSI entity states
        self._update_csi_states()

        # 3. Update spatial grids with new RSSI samples
        self._feed_rssi_to_grids()

        # 4. Feed CSI scores to grids
        self._feed_csi_to_grids()

        # 5. Update baselines and compute anomaly scores
        anomaly_scores: dict[str, dict] = {}
        for floor_id, grid in self.grids.items():
            bl = self.baselines[floor_id]
            bl.update_from_grid(grid)
            scores = bl.compute_anomaly_scores(grid)
            anomaly_scores[floor_id] = scores

        # 6. Render heatmaps (in executor — non-blocking)
        if self.heatmap_enabled:
            await self._async_render_heatmaps(anomaly_scores)

        _LOGGER.debug(
            "Coordinator update complete: %d clients, %d CSI nodes, %d floors",
            len(self.router_clients),
            len(self.csi_nodes),
            len(self.grids),
        )

        return self._current_data(anomaly_scores=anomaly_scores)

    # ─── AP & Node mappings ───────────────────────────────────────────────────

    def _apply_ap_mappings(self) -> None:
        """Apply manual and auto-discovered area/floor assignments to APs and clients."""
        from .registry_helpers import auto_link_ap_to_ha_device

        node_area_map: dict[str, str] = self.entry.options.get("node_area_map", {})
        node_floor_map: dict[str, str] = self.entry.options.get("node_floor_map", {})

        for mac, ap in self.ap_stats.items():
            # 1. Configured options override
            if mac in node_area_map:
                ap.area_id = node_area_map[mac]
            if mac in node_floor_map:
                ap.floor_id = node_floor_map[mac]

            # 2. Auto-discovery from HA Device & Area Registry if unassigned
            if not ap.area_id or not ap.floor_id:
                auto_area, auto_floor = auto_link_ap_to_ha_device(
                    self.hass, ap.mac, ap.name
                )
                if not ap.area_id and auto_area:
                    ap.area_id = auto_area
                if not ap.floor_id and auto_floor:
                    ap.floor_id = auto_floor

        # Propagate AP area/floor onto associated clients
        for client in self.router_clients.values():
            if client.ap_mac and client.ap_mac in self.ap_stats:
                ap = self.ap_stats[client.ap_mac]
                if ap.area_id and not client.area_id:
                    client.area_id = ap.area_id
                if ap.floor_id and not client.floor_id:
                    client.floor_id = ap.floor_id

    # ─── CSI state reading ────────────────────────────────────────────────────

    def _update_csi_states(self) -> None:
        """Read current CSI entity states from HA state machine."""
        for node in self.csi_nodes:
            if node.motion_score_entity_id:
                state = self.hass.states.get(node.motion_score_entity_id)
                if state and state.state not in ("unknown", "unavailable"):
                    try:
                        node.motion_score_value = float(state.state)  # type: ignore[attr-defined]
                    except ValueError:
                        pass

            if node.motion_detected_entity_id:
                state = self.hass.states.get(node.motion_detected_entity_id)
                if state:
                    node.motion_detected_value = state.state == "on"  # type: ignore[attr-defined]

    # ─── Spatial grid feeding ─────────────────────────────────────────────────

    def _feed_rssi_to_grids(self) -> None:
        """Route RSSI samples to the correct floor grid."""
        for mac, client in self.router_clients.items():
            if client.rssi is None:
                continue
            floor_id = self._resolve_floor_for_client(client)
            grid = self.grids.get(floor_id) or self.grids.get("default")
            if grid and client.ap_mac:
                grid.update_rssi(
                    ap_mac=client.ap_mac,
                    client_mac=mac,
                    rssi=client.rssi,
                )

    def _feed_csi_to_grids(self) -> None:
        """Route CSI motion scores to the correct floor grid."""
        for node in self.csi_nodes:
            score = getattr(node, "motion_score_value", None)
            if score is None:
                continue
            floor_id = node.floor_id or "default"
            grid = self.grids.get(floor_id) or self.grids.get("default")
            if grid:
                grid.update_csi_score(node.device_id, float(score))

    # ─── Heatmap rendering ────────────────────────────────────────────────────

    async def _async_render_heatmaps(self, anomaly_scores: dict[str, dict]) -> None:
        """Render all heatmap layers for all floors in executor threads."""
        for floor_id, grid in self.grids.items():
            scores = anomaly_scores.get(floor_id, {})
            layers: dict[str, bytes] = {}

            for layer in [LAYER_SIGNAL, LAYER_VARIANCE, LAYER_MOTION, LAYER_ANOMALY]:
                try:
                    if layer == LAYER_SIGNAL:
                        png = await self.hass.async_add_executor_job(
                            self._renderer.render_signal, grid
                        )
                    elif layer == LAYER_VARIANCE:
                        png = await self.hass.async_add_executor_job(
                            self._renderer.render_variance, grid
                        )
                    elif layer == LAYER_MOTION:
                        png = await self.hass.async_add_executor_job(
                            self._renderer.render_motion, grid
                        )
                    else:  # anomaly
                        png = await self.hass.async_add_executor_job(
                            self._renderer.render_anomaly, grid, scores
                        )
                    layers[layer] = png
                except Exception as exc:  # noqa: BLE001
                    _LOGGER.debug(
                        "Heatmap render failed for floor=%s layer=%s: %s",
                        floor_id,
                        layer,
                        exc,
                    )

            self.heatmap_images[floor_id] = layers

    # ─── Helper methods ───────────────────────────────────────────────────────

    def _resolve_floor_for_client(self, client: ClientInfo) -> str:
        """Return the floor_id for a router client based on its AP location."""
        if client.floor_id:
            return client.floor_id
        if client.ap_mac and client.ap_mac in self.ap_stats:
            ap = self.ap_stats[client.ap_mac]
            if ap.floor_id:
                return ap.floor_id
        return next(iter(self.grids), "default")

    def _update_node_positions(self) -> None:
        """Assign CSI node floor_id based on device area assignments."""
        for node in self.csi_nodes:
            if node.area_id and not node.floor_id:
                floor = get_floor_for_area(self.hass, node.area_id)
                if floor:
                    node.floor_id = floor.floor_id

    def _current_data(self, anomaly_scores: dict | None = None) -> dict[str, Any]:
        """Return the coordinator's data snapshot for entity polling."""
        return {
            "router_clients": self.router_clients,
            "ap_stats": self.ap_stats,
            "csi_nodes": self.csi_nodes,
            "grids": self.grids,
            "baselines": self.baselines,
            "anomaly_scores": anomaly_scores or {},
            "heatmap_images": self.heatmap_images,
            "scanning": self._scanning,
        }

    # ─── Scanning control ─────────────────────────────────────────────────────

    def start_scan(self) -> None:
        """Resume data collection."""
        self._scanning = True
        _LOGGER.info("WiFiSense scanning started")

    def stop_scan(self) -> None:
        """Pause data collection without unloading the integration."""
        self._scanning = False
        _LOGGER.info("WiFiSense scanning paused")
