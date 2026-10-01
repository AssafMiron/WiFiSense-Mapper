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

from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator

from .clients.base import APStats, ClientInfo
from .const import (
    CONF_ADAPTIVE_POLLING,
    CONF_DECO_ANCHORS,
    CONF_FAST_EVENT_PUSH,
    CONF_MICRO_ZONES,
    CONF_PERSON_TAGS,
    CONF_RF_OFF_DELAY,
    CONF_RF_SENSING_ENABLED,
    CONF_RF_SENSITIVITY,
    CONF_STATIONARY_DEVICES,
    DEFAULT_ADAPTIVE_POLLING,
    DEFAULT_ANOMALY_THRESHOLD,
    DEFAULT_FAST_EVENT_PUSH,
    DEFAULT_GRID_RESOLUTION,
    DEFAULT_POLL_INTERVAL,
    DEFAULT_RF_OFF_DELAY,
    DEFAULT_RF_SENSING_ENABLED,
    DEFAULT_RF_SENSITIVITY,
    DOMAIN,
    LAYER_ANOMALY,
    LAYER_COVERAGE,
    LAYER_MOTION,
    LAYER_RF_LINKS,
    LAYER_SIGNAL,
    LAYER_VARIANCE,
)
from .csi_discovery import CSINodeInfo, discover_csi_nodes
from .engine.baseline import BaselineLearner
from .engine.grid import SpatialGrid
from .engine.heatmap import HeatmapRenderer
from .engine.localization import MicroZone, PersonLocalizationEngine
from .engine.rf_sensing import (
    RFPerturbationDetector,
    RFSensingSnapshot,
)
from .engine.vacuum_align import VacuumMapAligner
from .engine.vacuum_map_parser import VacuumMapFeatures, parse_vacuum_map_image
from .registry_helpers import get_all_floors, get_floor_for_area
from .vacuum_helpers import (
    VacuumMapSource,
    async_fetch_map_image,
    discover_vacuum_maps,
)

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

        self.fast_event_push: bool = entry.options.get(
            CONF_FAST_EVENT_PUSH,
            entry.data.get(CONF_FAST_EVENT_PUSH, DEFAULT_FAST_EVENT_PUSH),
        )
        self._unsub_listeners: list[Any] = []
        self._fast_debounce_handle: Any = None
        self._updating_fast: bool = False
        self._external_trackers: dict[str, str] = {}  # entity_id -> orig_mac

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

        # RF Disturbance & Motion Sensing (Deco Mesh Backhaul + Stationary IoT)
        self.rf_sensing_enabled: bool = entry.options.get(
            CONF_RF_SENSING_ENABLED,
            entry.data.get(CONF_RF_SENSING_ENABLED, DEFAULT_RF_SENSING_ENABLED),
        )
        self.rf_sensitivity: str = entry.options.get(
            CONF_RF_SENSITIVITY, DEFAULT_RF_SENSITIVITY
        )
        self.rf_off_delay: float = float(
            entry.options.get(CONF_RF_OFF_DELAY, DEFAULT_RF_OFF_DELAY)
        )
        self.adaptive_polling: bool = entry.options.get(
            CONF_ADAPTIVE_POLLING,
            entry.data.get(CONF_ADAPTIVE_POLLING, DEFAULT_ADAPTIVE_POLLING),
        )
        self.rf_detector = RFPerturbationDetector(
            sensitivity=self.rf_sensitivity,
            off_delay_sec=self.rf_off_delay,
        )
        forced_stationary = entry.options.get(CONF_STATIONARY_DEVICES, [])
        if forced_stationary and isinstance(forced_stationary, list):
            self.rf_detector.classifier.set_forced_stationary(forced_stationary)
        person_tags = entry.options.get(CONF_PERSON_TAGS, {})
        if person_tags and isinstance(person_tags, dict):
            self.rf_detector.classifier.set_excluded_macs(list(person_tags.keys()))

        self.rf_snapshot: RFSensingSnapshot = RFSensingSnapshot()

    def async_setup_event_listeners(self) -> None:
        """Register state change event listeners for tracked devices and CSI sensors for instant updates."""
        if not self.fast_event_push:
            return

        from homeassistant.helpers import entity_registry as er
        from homeassistant.helpers.event import async_track_state_change_event

        self._external_trackers.clear()
        entities_to_track: set[str] = set()

        # Track CSI sensors
        for node in self.csi_nodes:
            if node.motion_score_entity_id:
                entities_to_track.add(node.motion_score_entity_id)
            if node.motion_detected_entity_id:
                entities_to_track.add(node.motion_detected_entity_id)
            if node.presence_entity_id:
                entities_to_track.add(node.presence_entity_id)

        # Track external router device_tracker entities in HA matching tracked MACs
        # NOTE: We strictly exclude WiFiSense's own entities (platform == DOMAIN) and
        # do NOT track person.* entities to prevent infinite circular feedback loops.
        ent_reg = er.async_get(self.hass)
        clean_mac_map: dict[str, str] = {
            m.lower().replace(":", "").replace("-", ""): m
            for m in self.localization_engine.trackers
        }
        for reg_entry in ent_reg.entities.values():
            if reg_entry.domain == "device_tracker":
                if reg_entry.platform == DOMAIN:
                    continue
                uid = (
                    (reg_entry.unique_id or "")
                    .lower()
                    .replace(":", "")
                    .replace("-", "")
                )
                for clean_mac, orig_mac in clean_mac_map.items():
                    if clean_mac and clean_mac in uid:
                        entities_to_track.add(reg_entry.entity_id)
                        self._external_trackers[reg_entry.entity_id] = orig_mac

        # Defensive guard: filter out any entities belonging to our own domain
        safe_entities = [
            eid
            for eid in entities_to_track
            if not eid.startswith(f"{DOMAIN}.") and f"_{DOMAIN}_" not in eid
        ]

        if safe_entities:
            _LOGGER.debug(
                "Registering fast-path state change listener for %d entities: %s",
                len(safe_entities),
                safe_entities,
            )
            unsub = async_track_state_change_event(
                self.hass,
                safe_entities,
                self._async_handle_fast_event,
            )
            self._unsub_listeners.append(unsub)

    @callback
    def _async_handle_fast_event(self, event: Any) -> None:
        """Handle incoming state change with 250ms debouncing and change validation."""
        if not self._scanning:
            return

        event_data = getattr(event, "data", {})
        entity_id = event_data.get("entity_id", "")
        old_state = event_data.get("old_state")
        new_state = event_data.get("new_state")

        # Ignore unavailable / unknown / empty states
        if new_state is None or new_state.state in ("unknown", "unavailable"):
            return

        # Ignore events where neither state nor attributes changed
        if (
            old_state is not None
            and old_state.state == new_state.state
            and old_state.attributes == new_state.attributes
        ):
            return

        # Defensive guard: Never react to our own integration's entities
        if entity_id.startswith(f"{DOMAIN}.") or f"_{DOMAIN}_" in entity_id:
            return

        if self._fast_debounce_handle is not None:
            self._fast_debounce_handle.cancel()
        self._fast_debounce_handle = self.hass.loop.call_later(
            0.25, self._run_fast_update
        )

    @callback
    def _run_fast_update(self) -> None:
        """Execute in-memory fast-path localization update and notify HA entities."""
        self._fast_debounce_handle = None
        if self._updating_fast or not self._scanning:
            return

        self._updating_fast = True
        try:
            # 1. Update CSI states from HA states
            self._update_csi_states()

            # 2. Update client states from external device_tracker entities if available
            self._update_clients_from_external_trackers()

            # 3. Fast localization update
            self._update_person_localizations()

            # 4. Notify HA entities immediately without executor jobs or heatmap rendering
            self.async_set_updated_data(self._current_data())
            _LOGGER.debug("Fast-path localization update dispatched to HA entities.")
        except Exception as exc:  # noqa: BLE001
            _LOGGER.debug("Fast-path update failed: %s", exc)
        finally:
            self._updating_fast = False

    def _update_clients_from_external_trackers(self) -> None:
        """Update client telemetry from cached external router device_tracker entities."""
        for entity_id, orig_mac in self._external_trackers.items():
            st = self.hass.states.get(entity_id)
            if st is None:
                continue
            attrs = st.attributes
            client = self.router_clients.get(orig_mac)
            if not client:
                client = ClientInfo(mac=orig_mac)
                self.router_clients[orig_mac] = client

            is_home = st.state not in ("not_home", "unavailable", "unknown")
            if client.extra is None:
                client.extra = {}
            client.extra["is_home"] = is_home

            raw_sig = (
                attrs.get("rssi") or attrs.get("signal_level") or attrs.get("signal")
            )
            if isinstance(raw_sig, dict):
                val = (
                    raw_sig.get("band5")
                    or raw_sig.get("band2_4")
                    or raw_sig.get("band6")
                )
                if isinstance(val, (int, float)):
                    client.rssi = int(val)
            elif isinstance(raw_sig, (int, float)):
                val_int = int(raw_sig)
                if val_int < 0:
                    client.rssi = val_int

            ap_val = (
                attrs.get("ap_mac")
                or attrs.get("ap_bssid")
                or attrs.get("bssid")
                or attrs.get("connected_ap")
                or attrs.get("ap")
            )
            if ap_val:
                norm_ap = str(ap_val).strip().lower().replace("-", ":")
                if norm_ap in self.ap_stats:
                    client.ap_mac = norm_ap
                else:
                    matched_mac = next(
                        (
                            ap.mac
                            for ap in self.ap_stats.values()
                            if ap.name
                            and ap.name.lower() == str(ap_val).strip().lower()
                        ),
                        None,
                    )
                    client.ap_mac = matched_mac or norm_ap
            if attrs.get("ip"):
                client.ip = str(attrs["ip"])
            if attrs.get("ssid"):
                client.ssid = str(attrs["ssid"])
            if attrs.get("band"):
                client.band = str(attrs["band"])

    def async_unload_listeners(self) -> None:
        """Unsubscribe all fast-path event listeners."""
        for unsub in self._unsub_listeners:
            try:
                unsub()
            except Exception:  # noqa: BLE001, S110
                pass
        self._unsub_listeners.clear()
        self._external_trackers.clear()
        if self._fast_debounce_handle is not None:
            self._fast_debounce_handle.cancel()
            self._fast_debounce_handle = None

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

        # 0. Fetch & parse vacuum maps (Roborock / Valetudo)
        await self._async_fetch_and_parse_vacuum_maps()

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

        # 4.5. Update RF link disturbance sensing (Deco backhaul + stationary IoT)
        await self._async_update_rf_sensing()

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
        deco_anchors: dict[str, Any] = self.entry.options.get(
            CONF_DECO_ANCHORS, self.entry.data.get(CONF_DECO_ANCHORS, {})
        )
        room_coords_map: dict[str, Any] = self.entry.options.get("room_coords_map", {})
        overwrite_registry: bool = self.entry.options.get(
            "overwrite_ha_device_areas", False
        )

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
                    floor_id=node_floor_map.get(raw_mac)
                    or node_floor_map.get(norm_mac),
                    client_count=0,
                    extra={"configured_in_options": True},
                )
            else:
                self.ap_stats[norm_mac].area_id = area_id
                if raw_mac in node_floor_map or norm_mac in node_floor_map:
                    self.ap_stats[norm_mac].floor_id = node_floor_map.get(
                        raw_mac
                    ) or node_floor_map.get(norm_mac)

        for mac, ap in self.ap_stats.items():
            # 2. Configured options override (node maps & deco anchors)
            anchor = (
                deco_anchors.get(mac)
                or deco_anchors.get(ap.mac)
                or (ap.name and deco_anchors.get(ap.name))
            )
            if anchor and isinstance(anchor, dict):
                if anchor.get("area_id"):
                    ap.area_id = anchor["area_id"]
                if anchor.get("floor_id"):
                    ap.floor_id = anchor["floor_id"]

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

            # 6. Register AP position on floor grid
            floor_id = ap.floor_id or "default"
            grid = self.grids.get(floor_id) or self.grids.get("default")
            if grid:
                if anchor and isinstance(anchor, dict):
                    x_m = float(
                        anchor.get(
                            "x_m", (anchor.get("x_pct", 50.0) / 100.0) * grid.width_m
                        )
                    )
                    y_m = float(
                        anchor.get(
                            "y_m", (anchor.get("y_pct", 50.0) / 100.0) * grid.height_m
                        )
                    )
                    grid.set_ap_marker(
                        ap.mac,
                        ap.name or f"Deco {ap.mac[-5:]}",
                        x_m,
                        y_m,
                        ap.area_id,
                    )
                else:
                    coords = node_coords_map.get(mac) or node_coords_map.get(ap.mac)
                    if (
                        coords
                        and isinstance(coords, (list, tuple))
                        and len(coords) >= 2
                    ):
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

    # ─── Vacuum map fetching & parsing ────────────────────────────────────────

    async def _async_fetch_and_parse_vacuum_maps(self) -> None:
        """Fetch vacuum map image bytes and parse semantic features in executor."""
        for source in self.vacuum_sources:
            try:
                img_bytes = await async_fetch_map_image(self.hass, source.entity_id)
                if not img_bytes:
                    continue
                source.last_map_bytes = img_bytes

                floor_id = source.floor_id or next(iter(self.grids), "default")
                grid = self.grids.get(floor_id) or self.grids.get("default")
                w_m = grid.width_m if grid else 10.0
                h_m = grid.height_m if grid else 10.0

                from functools import partial

                features: VacuumMapFeatures = await self.hass.async_add_executor_job(
                    partial(
                        parse_vacuum_map_image,
                        img_bytes,
                        segments=source.room_segments,
                        width_m=w_m,
                        height_m=h_m,
                        floor_id=floor_id,
                    )
                )

                self.localization_engine.set_vacuum_features(floor_id, features)

                # Auto-enrich micro-zones with detected furniture if none configured
                if features.furniture and not self.localization_engine.micro_zones:
                    for f in features.furniture:
                        self.localization_engine.micro_zones.append(
                            MicroZone.from_dict(f.to_micro_zone_dict())
                        )
                _LOGGER.debug(
                    "Parsed vacuum map for floor=%s: %d rooms, %d furniture items",
                    floor_id,
                    len(features.rooms),
                    len(features.furniture),
                )
            except Exception as exc:  # noqa: BLE001
                _LOGGER.debug(
                    "Failed to fetch/parse vacuum map from %s: %s",
                    source.entity_id,
                    exc,
                )

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
                LAYER_RF_LINKS,
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
                    elif layer == LAYER_RF_LINKS:
                        if hasattr(self._renderer, "render_rf_links"):
                            png = await self.hass.async_add_executor_job(
                                self._renderer.render_rf_links, grid, self.rf_snapshot
                            )
                        else:
                            continue
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
                is_connected = (
                    client.extra.get("is_home", True) if client.extra else True
                )
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
            area_name = (
                get_area_name_from_id(self.hass, area_id)
                if area_id
                else (tracker.current_area_name or "Home")
            )

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
                    if (
                        other_pos
                        and tracker.latest_state.x_m > 0
                        and tracker.latest_state.y_m > 0
                    ):
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

            # Collect all known AP coordinates on this floor for triangulation
            all_ap_positions: dict[str, tuple[float, float]] = {}
            for o_mac, o_ap in self.ap_stats.items():
                if grid:
                    pos = grid.get_ap_position_m(o_mac)
                    if pos:
                        all_ap_positions[o_ap.name or o_mac] = pos

            vac_features = self.localization_engine.get_vacuum_features(floor_id)

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
                all_ap_positions=all_ap_positions,
                vacuum_features=vac_features,
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

    async def _async_update_rf_sensing(self) -> None:
        """Feed telemetry to RF perturbation detector and evaluate area presence."""
        if not self.rf_sensing_enabled:
            self.rf_snapshot = RFSensingSnapshot()
            return

        # 1. Feed Deco backhaul links if router client supports it
        if self.router_client and hasattr(self.router_client, "async_get_backhaul_links"):
            try:
                cached_aps = list(self.ap_stats.values()) if self.ap_stats else None
                try:
                    backhaul_links = await self.router_client.async_get_backhaul_links(aps=cached_aps)
                except TypeError:
                    backhaul_links = await self.router_client.async_get_backhaul_links()
                for b_link in backhaul_links:
                    sat_mac = b_link["satellite_mac"]
                    parent_mac = b_link["parent_mac"]
                    rssi = b_link.get("rssi")
                    if rssi is None:
                        continue

                    # Look up area of satellite AP
                    area_id = b_link.get("area_id")
                    if not area_id and sat_mac in self.ap_stats:
                        area_id = self.ap_stats[sat_mac].area_id
                    if not area_id and parent_mac in self.ap_stats:
                        area_id = self.ap_stats[parent_mac].area_id

                    floor_id = None
                    if sat_mac in self.ap_stats:
                        floor_id = self.ap_stats[sat_mac].floor_id

                    self.rf_detector.feed_backhaul_sample(
                        satellite_mac=sat_mac,
                        parent_mac=parent_mac,
                        rssi=rssi,
                        area_id=area_id,
                        floor_id=floor_id,
                        satellite_name=b_link.get("satellite_name"),
                    )
            except Exception as exc:  # noqa: BLE001
                _LOGGER.debug("Failed to harvest backhaul links for RF sensing: %s", exc)

        # 2. Feed stationary IoT client links
        for client in self.router_clients.values():
            if client.rssi is None or not client.ap_mac:
                continue

            area_id = client.area_id
            floor_id = client.floor_id
            if not area_id and client.ap_mac in self.ap_stats:
                area_id = self.ap_stats[client.ap_mac].area_id
                floor_id = self.ap_stats[client.ap_mac].floor_id

            self.rf_detector.feed_client_sample(
                client_mac=client.mac,
                ap_mac=client.ap_mac,
                rssi=client.rssi,
                client_name=client.hostname,
                area_id=area_id,
                floor_id=floor_id,
            )

        # 3. Evaluate area states and disturbance scores
        self.rf_snapshot = self.rf_detector.evaluate_areas()

        # 4. Adaptive polling interval adjustment
        if self.adaptive_polling:
            configured_interval = timedelta(
                seconds=self.entry.options.get(
                    "poll_interval",
                    self.entry.data.get("poll_interval", DEFAULT_POLL_INTERVAL),
                )
            )
            if self.rf_snapshot.burst_recommended:
                burst_interval = timedelta(seconds=3)
                if self.update_interval != burst_interval:
                    self.update_interval = burst_interval
                    _LOGGER.debug(
                        "RF disturbance detected: accelerated polling to 3s burst mode"
                    )
            elif self.update_interval != configured_interval:
                self.update_interval = configured_interval
                _LOGGER.debug(
                    "RF field quiet: restored polling interval to %ds",
                    configured_interval.total_seconds(),
                )

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
            "rf_sensing": self.rf_snapshot,
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
