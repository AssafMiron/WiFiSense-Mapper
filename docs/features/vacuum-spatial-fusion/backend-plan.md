# Backend Plan — Vacuum Map Spatial Fusion & Dual-Interval Engine

## 1. Coordinator Refactor & Event-Driven Fast-Path

### 1.1 Dual-Interval Architecture in `coordinator.py`
The `WiFiSenseCoordinator` will be decoupled into:
1. **Background Polling Loop** (`_async_update_data`, default 60s):
   - Handles slow I/O, vacuum map image fetching, grid raycasting, baseline updates, and async heatmap rendering in executor jobs.
2. **Fast-Path Event Dispatcher** (`async_fast_update_person`):
   - Invoked directly by HA state change event listeners or fast timer.
   - Reads current states of tracked client `device_tracker.*` and CSI motion entities.
   - Evaluates `PersonLocalizationEngine` in-memory.
   - Dispatches `self.async_set_updated_data()` or triggers targeted entity updates without running executor jobs.

### 1.2 Event Subscriptions in `__init__.py` / `coordinator.py`
Using Home Assistant's `async_track_state_change_event`:
- Subscribe to:
  - Tracked person `device_tracker.*` entities.
  - CSI motion score and presence sensors.
- Debounce rate: 250ms sliding window to prevent event storms during rapid WiFi signal fluctuations.

---

## 2. Configuration & Schema Updates

### 2.1 Constants (`const.py`)
- `CONF_FAST_EVENT_PUSH`: `"fast_event_push"` (default: `True`)
- `CONF_DECO_ANCHORS`: `"deco_anchors"` (dict mapping Deco MAC/Name to `{area_id, x_pct, y_pct}`)
- `DEFAULT_BACKGROUND_INTERVAL`: `60` seconds

### 2.2 Options Flow (`config_flow.py`)
- Step for positioning Deco anchors with presets (`Center`, `Top-Left`, `Top-Right`, etc. or custom coordinates).
- Step for importing Roborock furniture items into `micro_zones`.

---

## 3. Storage & Persistence
- Stores learned baseline grids and calibrated Deco anchors using `homeassistant.helpers.storage.Store`.
