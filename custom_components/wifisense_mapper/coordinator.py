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
from collections import deque
from datetime import timedelta
from typing import TYPE_CHECKING, Any

from homeassistant.core import HomeAssistant
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator

from .clients.base import APStats, ClientInfo
from .const import (
    CONF_MICRO_ZONES,
    CONF_PERSON_TAGS,
    DEFAULT_ANOMALY_THRESHOLD,
    DEFAULT_GRID_RESOLUTION,
    DEFAULT_POLL_INTERVAL,
    DOMAIN,
    LAYER_ANOMALY,
    LAYER_COVERAGE,
    LAYER_MOTION,
    LAYER_SIGNAL,
    LAYER_VARIANCE,
)
from .csi_discovery import CSINodeInfo, discover_csi_nodes
from .engine.baseline import BaselineLearner
from .engine.grid import SpatialGrid
from .engine.heatmap import HeatmapRenderer
from .engine.localization import PersonLocalizationEngine
from .engine.vacuum_align import VacuumMapAligner
from .registry_helpers import get_all_floors, get_floor_for_area
from .vacuum_helpers import VacuumMapSource, discover_vacuum_maps

if TYPE_CHECKING:
    from homeassistant.config_entries import ConfigEntry

    from .clients.base import RouterClient

_LOGGER = logging.getLogger(__name__)


class LogBufferHandler(logging.Handler):
    """In-memory ring buffer capturing recent log records for UI troubleshooting."""

    def __init__(self, capacity: int = 50) -> None:
        super().__init__()
        self.buffer: deque[str] = deque(maxlen=capacity)
        self.setFormatter(
            logging.Formatter(
                "%(asctime)s [%(levelname)s] %(name)s: %(message)s",
                datefmt="%H:%M:%S",
            )
        )

    def emit(self, record: logging.LogRecord) -> None:
        try:
            msg = self.format(record)
            self.buffer.append(msg)
        except Exception:  # noqa: BLE001, S110
            pass


_LOG_BUFFER = LogBufferHandler(capacity=50)
_LOGGER.addHandler(_LOG_BUFFER)
logging.getLogger("custom_components.wifisense_mapper").addHandler(_LOG_BUFFER)


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

        self.localization_engine = PersonLocalizationEngine()
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

        # Configure micro zones
        micro_zones: list[dict[str, Any]] = self.entry.options.get(
            CONF_MICRO_ZONES, self.entry.data.get(CONF_MICRO_ZONES, [])
        )
        self.localization_engine.set_micro_zones(micro_zones)

        # Configure person tags
        person_tags: dict[str, Any] = self.entry.options.get(
            CONF_PERSON_TAGS, self.entry.data.get(CONF_PERSON_TAGS, {})
        )
        for mac, tag_data in person_tags.items():
            if isinstance(tag_data, str):
                self.localization_engine.configure_person(
                    mac=mac,
                    person_entity_id=tag_data,
                    person_name=tag_data.split(".")[-1].replace("_", " ").title(),
                )
            elif isinstance(tag_data, dict):
                self.localization_engine.configure_person(
                    mac=mac,
                    person_entity_id=tag_data.get("person_entity_id"),
                    person_name=tag_data.get("person_name"),
                )

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

        # Seed AP mappings and set CSI node positions from area assignments
        self._apply_ap_mappings()
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

        # Apply AP mappings regardless of router poll outcome so configured nodes exist
        self._apply_ap_mappings()

        # 2. Read CSI entity states
        self._update_csi_states()

        # 3. Update spatial grids with new RSSI samples
        self._feed_rssi_to_grids()

        # 4. Feed CSI scores to grids
        self._feed_csi_to_grids()

        # 5. Update person localization & activity engine
        self._update_person_localizations()

        # 6. Update baselines and compute anomaly scores
        anomaly_scores: dict[str, dict] = {}
        for floor_id, grid in self.grids.items():
            bl = self.baselines[floor_id]
            bl.update_from_grid(grid)
            scores = bl.compute_anomaly_scores(grid)
            anomaly_scores[floor_id] = scores

        # 7. Render heatmaps (in executor — non-blocking)
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
        from .clients.base import APStats, RouterClient
        from .registry_helpers import (
            async_sync_device_area,
            auto_link_ap_to_ha_device,
        )

        node_area_map: dict[str, str] = self.entry.options.get("node_area_map", {})
        node_floor_map: dict[str, str] = self.entry.options.get("node_floor_map", {})
        node_coords_map: dict[str, Any] = self.entry.options.get("node_coords_map", {})
        room_coords_map: dict[str, Any] = self.entry.options.get("room_coords_map", {})
        overwrite_registry: bool = self.entry.options.get("overwrite_ha_device_areas", False)

        # 1. Seed any AP configured in options that is not yet in ap_stats
        for raw_mac, area_id in node_area_map.items():
            norm_mac = RouterClient.normalize_mac(raw_mac)
            if not norm_mac:
                continue
            if norm_mac not in self.ap_stats:
                self.ap_stats[norm_mac] = APStats(
                    mac=norm_mac,
                    name=None,
                    area_id=area_id,
                    floor_id=node_floor_map.get(raw_mac) or node_floor_map.get(norm_mac),
                    client_count=0,
                    extra={"configured_in_options": True},
                )
            else:
                self.ap_stats[norm_mac].area_id = area_id
                if raw_mac in node_floor_map or norm_mac in node_floor_map:
                    self.ap_stats[norm_mac].floor_id = (
                        node_floor_map.get(raw_mac) or node_floor_map.get(norm_mac)
                    )

        for mac, ap in self.ap_stats.items():
            # 2. Configured options override
            if mac in node_area_map:
                ap.area_id = node_area_map[mac]
            if mac in node_floor_map:
                ap.floor_id = node_floor_map[mac]

            # 3. Auto-discovery from HA Device & Area Registry if unassigned
            if not ap.area_id or not ap.floor_id:
                auto_area, auto_floor = auto_link_ap_to_ha_device(
                    self.hass, ap.mac, ap.name
                )
                if not ap.area_id and auto_area:
                    ap.area_id = auto_area
                if not ap.floor_id and auto_floor:
                    ap.floor_id = auto_floor

            # 4. If floor_id is still unassigned but area_id is known, inherit from HA area
            if ap.area_id and not ap.floor_id:
                from .registry_helpers import get_floor_for_area
                area_floor = get_floor_for_area(self.hass, ap.area_id)
                if area_floor:
                    ap.floor_id = area_floor.floor_id

            # 5. Sync area back to HA device registry
            if ap.area_id:
                async_sync_device_area(
                    self.hass,
                    ap.mac,
                    ap.area_id,
                    overwrite=overwrite_registry,
                )

            # 5. Register AP position on floor grid
            floor_id = ap.floor_id or "default"
            grid = self.grids.get(floor_id) or self.grids.get("default")
            if grid:
                coords = node_coords_map.get(mac) or node_coords_map.get(ap.mac)
                if coords and isinstance(coords, (list, tuple)) and len(coords) >= 2:
                    grid.set_ap_marker(
                        ap.mac,
                        ap.name or f"Deco {ap.mac[-5:]}",
                        float(coords[0]),
                        float(coords[1]),
                        ap.area_id,
                    )

        # 6. Apply room label positions onto floor grids
        for r_key, r_info in room_coords_map.items():
            if isinstance(r_info, dict):
                r_floor = r_info.get("floor_id") or "default"
                grid = self.grids.get(r_floor) or self.grids.get("default")
                if grid:
                    grid.set_room_label(
                        area_id=r_info.get("area_id") or r_key,
                        name=r_info.get("name") or r_key,
                        x_m=float(r_info.get("x_m", 0.0)),
                        y_m=float(r_info.get("y_m", 0.0)),
                        segment_id=r_info.get("segment_id"),
                    )

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

            motion_eid = node.motion_detected_entity_id or node.presence_entity_id
            if motion_eid:
                state = self.hass.states.get(motion_eid)
                if state and state.state not in ("unknown", "unavailable"):
                    node.motion_detected_value = state.state.lower() in (  # type: ignore[attr-defined]
                        "on",
                        "home",
                        "detected",
                        "motion",
                        "true",
                    )

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

            for layer in [
                LAYER_SIGNAL,
                LAYER_VARIANCE,
                LAYER_MOTION,
                LAYER_ANOMALY,
                LAYER_COVERAGE,
            ]:
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
                    elif layer == LAYER_COVERAGE:
                        png = await self.hass.async_add_executor_job(
                            self._renderer.render_coverage, grid
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

    def _update_person_localizations(self) -> None:
        """Calculate real-time position, micro-zone, and activity for all configured person trackers."""
        import math

        from .clients.base import RouterClient
        from .engine.localization import calculate_distance_from_rssi
        from .registry_helpers import get_area_name_from_id, get_floor_name_from_id

        for mac, tracker in self.localization_engine.trackers.items():
            norm_mac = RouterClient.normalize_mac(mac) or mac.lower()
            client = self.router_clients.get(norm_mac) or self.router_clients.get(mac)

            is_connected = False
            if client:
                is_connected = client.extra.get("is_home", True) if client.extra else True
                if client.rssi is not None or client.ip is not None:
                    is_connected = True

            ap_mac = client.ap_mac if client else None
            rssi = client.rssi if client else None
            band = client.band if client else None

            ap_obj = self.ap_stats.get(ap_mac) if ap_mac else None
            ap_name = ap_obj.name if ap_obj else None

            floor_id = (
                (client and client.floor_id)
                or (ap_obj and ap_obj.floor_id)
                or (client and self._resolve_floor_for_client(client))
                or "default"
            )
            floor_name = get_floor_name_from_id(self.hass, floor_id)
            area_id = (client and client.area_id) or (ap_obj and ap_obj.area_id) or None
            area_name = get_area_name_from_id(self.hass, area_id) if area_id else (tracker.current_area_name or "Home")

            grid = self.grids.get(floor_id) or self.grids.get("default")
            grid_w = grid.width_m if grid else 10.0
            grid_h = grid.height_m if grid else 10.0

            ap_pos = grid.get_ap_position_m(ap_mac) if (grid and ap_mac) else None

            # Calculate distances to all known APs on this floor
            ap_distances: dict[str, float] = {}
            if rssi is not None and ap_name:
                d = calculate_distance_from_rssi(rssi, band=band)
                if d is not None:
                    ap_distances[ap_name] = d

            for other_mac, other_ap in self.ap_stats.items():
                if other_ap.name and other_ap.name not in ap_distances:
                    other_pos = grid.get_ap_position_m(other_mac) if grid else None
                    if other_pos and tracker.latest_state.x_m > 0 and tracker.latest_state.y_m > 0:
                        geom_d = round(
                            math.hypot(
                                tracker.latest_state.x_m - other_pos[0],
                                tracker.latest_state.y_m - other_pos[1],
                            ),
                            1,
                        )
                        ap_distances[other_ap.name] = geom_d

            # Get local CSI motion score on this floor / area
            csi_score = 0.0
            for node in self.csi_nodes:
                if node.floor_id == floor_id or node.area_id == area_id:
                    score = getattr(node, "motion_score_value", None)
                    if score is not None:
                        csi_score = max(csi_score, float(score))

            tracker.update(
                ap_mac=ap_mac,
                ap_name=ap_name,
                band=band,
                rssi=rssi,
                floor_id=floor_id,
                floor_name=floor_name,
                area_id=area_id,
                area_name=area_name,
                ap_pos_m=ap_pos,
                grid_width_m=grid_w,
                grid_height_m=grid_h,
                csi_motion_score=csi_score,
                micro_zones=self.localization_engine.micro_zones,
                is_connected=is_connected,
                ap_distances=ap_distances,
            )

    @property
    def is_scanning(self) -> bool:
        """Return True if scanning is active."""
        return self._scanning

    def get_recent_logs(self, max_lines: int = 30) -> list[str]:
        """Return recent log records captured by the in-memory buffer."""
        items = list(_LOG_BUFFER.buffer)
        return items[-max_lines:] if len(items) > max_lines else items

    def get_area_coverage_summary(self) -> dict[str, Any]:
        """Compute area-level mesh coverage, cross-coverage, and dead-zones."""
        from .registry_helpers import get_all_areas, get_floor_for_area

        all_areas = {a.id: a.name for a in get_all_areas(self.hass)}
        area_ap_map: dict[str, list[str]] = {aid: [] for aid in all_areas}
        floor_ap_map: dict[str, list[str]] = {}

        for mac, ap in self.ap_stats.items():
            if ap.area_id and ap.area_id in area_ap_map:
                area_ap_map[ap.area_id].append(mac)
            if ap.floor_id:
                floor_ap_map.setdefault(ap.floor_id, []).append(mac)

        covered = []
        cross_covered = []
        uncovered = []

        for aid in all_areas:
            direct_aps = area_ap_map.get(aid, [])
            floor = get_floor_for_area(self.hass, aid)
            floor_id = floor.floor_id if floor else None
            mesh_aps = floor_ap_map.get(floor_id, []) if floor_id else []

            # Multi-AP mesh coverage:
            # 1. An area is cross-covered if it has >= 2 direct APs OR its floor has >= 2 mesh APs
            # 2. An area is covered if it has >= 1 direct AP OR its floor has >= 1 mesh AP
            if len(direct_aps) >= 2 or len(mesh_aps) >= 2:
                cross_covered.append(aid)
                covered.append(aid)
            elif len(direct_aps) >= 1 or len(mesh_aps) >= 1:
                covered.append(aid)
            else:
                uncovered.append(aid)

        return {
            "area_ap_map": area_ap_map,
            "floor_ap_map": floor_ap_map,
            "covered_area_ids": covered,
            "cross_covered_area_ids": cross_covered,
            "uncovered_area_ids": uncovered,
            "covered_count": len(covered),
            "cross_covered_count": len(cross_covered),
            "uncovered_count": len(uncovered),
            "total_areas": len(all_areas),
            "all_areas": all_areas,
        }

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
            "coverage": self.get_area_coverage_summary(),
            "person_tracking": self.localization_engine.all_states(),
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

