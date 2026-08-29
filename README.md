# WiFiSense Mapper

**Custom Home Assistant integration for WiFi-based room mapping, signal heatmaps, and object/furniture anomaly detection.**

[![HACS](https://img.shields.io/badge/HACS-Custom-orange.svg)](https://hacs.xyz)
[![HA version](https://img.shields.io/badge/Home%20Assistant-2024.1%2B-blue)](https://www.home-assistant.io)
[![License](https://img.shields.io/badge/License-MIT-green)](LICENSE)

WiFiSense Mapper is a **glue layer** — it fuses telemetry from your mesh router, ESP32 CSI sensing nodes, existing HA Floors & Areas, and optional robot vacuum maps into interactive 2D heatmaps, spatial anomaly detection, and rough room-level device tracking. It does **not** replace ESPectre, TOMMY, your router integration, or Zircon3D/Floorplan. It makes them work together.

---

## Table of Contents

- [Features](#features)
- [What This Is Not](#what-this-is-not)
- [Requirements](#requirements)
- [Installation](#installation)
- [Configuration](#configuration)
- [Entity Reference](#entity-reference)
- [Services Reference](#services-reference)
- [Visualization (Lovelace)](#visualization-lovelace)
- [Calibration Guide](#calibration-guide)
- [Limitations](#limitations-please-read)
- [Architecture](#architecture)
- [Related Projects](#related-projects)
- [Contributing](#contributing)

---

## Features

| Feature | Description |
|---------|-------------|
| **Router polling** | TP-Link Deco local API (via `tplinkrouterc6u`), UniFi bridge (via HA entity states), extensible base class |
| **ESP32 CSI nodes** | Auto-discovers ESPectre and TOMMY nodes via entity/device registry (esphome + MQTT fallback) |
| **HA-native organization** | Reads Floors and Areas registries — never duplicates your setup |
| **Vacuum map integration** | Discovers Roborock, Valetudo, Dreame map entities for room boundary validation |
| **2D spatial grids** | Per-floor sparse grids accumulating RSSI + CSI scores with distance decay |
| **Heatmaps** | 4 layers (signal, variance, motion, anomaly) rendered as PNG `image.*` entities |
| **Anomaly detection** | Pure-Python EWMA + z-score baseline learner — no ML dependencies |
| **Device tracking** | Room-level positioning via AP association → area assignment chain |
| **HACS-ready** | Standard custom component with config flow, options flow, and services |

---

## What This Is Not

- ❌ A replacement for [ESPectre](https://github.com/francescopace/espectre) or TOMMY
- ❌ A router management UI
- ❌ A computer vision or camera-based system
- ❌ A GPS-accuracy positioning system
- ✅ The missing spatial context layer between your existing integrations

---

## Requirements

- **Home Assistant** 2024.1 or newer
- **At least one data source:**
  - ESP32 CSI nodes running ESPectre or TOMMY (strongly recommended for motion/presence)
  - Supported mesh router (TP-Link Deco, UniFi)
- **Strongly recommended:**
  - Floors and Areas already defined in HA Settings → Areas & Zones
- **Optional:**
  - Mapping robot vacuum (Roborock, Valetudo, Dreame)

### Python dependencies (auto-installed)

| Package | Purpose | Required? |
|---------|---------|-----------|
| `tplinkrouterc6u>=4.3.0` | TP-Link Deco local API auth | If using Deco |
| `Pillow` | PNG heatmap rendering | Optional (falls back to BMP) |

---

## Installation

### HACS (recommended)

1. In HACS, go to **Integrations** → **⋮** → **Custom repositories**
2. Add `https://github.com/assafmiron/WiFiSense-Mapper` as category **Integration**
3. Search for **WiFiSense Mapper** and install
4. Restart Home Assistant
5. Go to **Settings → Devices & Services → Add Integration** → search **WiFiSense Mapper**

### Manual

```bash
cp -r custom_components/wifisense_mapper /config/custom_components/
```

Restart Home Assistant, then add via Settings → Devices & Services.

---

## Configuration

### Step 1 — Router Type

Choose between:
- **TP-Link Deco** — provide the local IP and admin password
- **UniFi** — no credentials needed; bridges through the existing HA UniFi integration
- **None** — CSI or vacuum-only mode

### Step 2 — Deco credentials (Deco only)

Enter the Deco's local IP (e.g. `192.168.0.1`) and admin password. The integration will attempt a test connection before creating the entry.

### Options Flow

After setup, configure via **Settings → Devices & Services → WiFiSense Mapper → Configure**:

| Option | Default | Description |
|--------|---------|-------------|
| Poll interval | 30s | How often to poll router + CSI states |
| Heatmap enabled | on | Toggle CPU-intensive heatmap rendering |
| Anomaly threshold | 3.0 σ | Z-score for object anomaly alerts |
| Baseline learning window | 7 days | Rolling window for baseline EWMA |
| Vacuum map entities | (none) | Select vacuum `image.*`/`camera.*` map entities |

---

## Entity Reference

### Sensors

| Entity | Unit | Description |
|--------|------|-------------|
| `sensor.{floor}_wifi_client_count` | clients | WiFi clients associated on this floor |
| `sensor.{ap_name}_average_rssi` | dBm | Mean RSSI of clients on an AP |
| `sensor.{node}_motion_score` | score | ESPectre/TOMMY CSI motion score |
| `sensor.{floor}_anomaly_score` | σ | Max z-score deviation from baseline |

### Binary Sensors

| Entity | Class | Description |
|--------|-------|-------------|
| `binary_sensor.{area}_presence` | presence | Fused WiFi + CSI presence per area |
| `binary_sensor.{floor}_object_anomaly` | problem | Signal anomaly vs learned baseline |
| `binary_sensor.{floor}_csi_motion` | motion | Aggregated CSI motion for a floor |

### Image Entities (Heatmaps)

| Entity | Description |
|--------|-------------|
| `image.{floor}_signal_heatmap` | RSSI signal strength layer |
| `image.{floor}_variance_heatmap` | RSSI variance (obstacle shadows) |
| `image.{floor}_motion_heatmap` | CSI motion intensity layer |
| `image.{floor}_anomaly_heatmap` | Anomaly z-score layer |

Access via: `/api/image_proxy/image.ground_floor_signal_heatmap`

### Device Trackers

| Entity | State | Description |
|--------|-------|-------------|
| `device_tracker.{hostname}` | area name / `not_home` | Room-level location |

---

## Services Reference

All services are callable via Developer Tools → Services or automations.

### `wifisense_mapper.start_scan` / `stop_scan`
Pause or resume data collection without unloading the integration.

### `wifisense_mapper.generate_heatmap`
```yaml
service: wifisense_mapper.generate_heatmap
data:
  floor_id: ground_floor   # optional, defaults to all floors
  layer: signal            # signal | variance | motion | anomaly
```

### `wifisense_mapper.learn_baseline`
Resets the EWMA baseline for a floor. Use after significant room changes.
```yaml
service: wifisense_mapper.learn_baseline
data:
  floor_id: ground_floor   # optional, defaults to all floors
```

### `wifisense_mapper.calibrate_vacuum_map`
Provide ≥3 non-collinear point correspondences to align the vacuum map with the WiFi grid.
```yaml
service: wifisense_mapper.calibrate_vacuum_map
data:
  floor_id: ground_floor
  calibration_points:
    - {vac_px: 120, vac_py: 80, grid_col: 4, grid_row: 3}
    - {vac_px: 300, vac_py: 80, grid_col: 10, grid_row: 3}
    - {vac_px: 120, vac_py: 250, grid_col: 4, grid_row: 8}
```

### `wifisense_mapper.export_map`
Exports heatmap to `/config/www/wifisense_{floor}_{layer}.png` (or JSON grid data).
```yaml
service: wifisense_mapper.export_map
data:
  floor_id: ground_floor
  format: png    # png | json
  layer: signal
```

### `wifisense_mapper.link_node_to_area`
Override the auto-detected area for a CSI node or AP.
```yaml
service: wifisense_mapper.link_node_to_area
data:
  node_id: "espectre_node_001"
  area_id: "living_room"
```

---

## Visualization (Lovelace)

### picture-elements with heatmap overlay

```yaml
type: picture-elements
image: /local/floorplan.png
elements:
  - type: image
    entity: image.ground_floor_signal_heatmap
    style:
      left: 0%
      top: 0%
      width: 100%
      opacity: 0.6
  - type: state-badge
    entity: binary_sensor.living_room_presence
    style: {left: 30%, top: 40%}
```

### Zircon3D / Floorplan

Export the heatmap PNG via `export_map` service and reference it as a layer in your Zircon3D or Floorplan configuration.

### Presence indicator cards

```yaml
type: entities
entities:
  - entity: binary_sensor.living_room_presence
  - entity: binary_sensor.ground_floor_object_anomaly
  - entity: sensor.ground_floor_anomaly_score
  - entity: sensor.ground_floor_wifi_client_count
```

---

## Calibration Guide

### Node placement tips

- Place ESP32 CSI nodes at **chest height** (~1.0–1.2 m) pointing toward the center of the monitored area.
- Aim for **1–2 nodes per room** for reliable motion detection.
- Avoid placing nodes directly behind large appliances (refrigerators, TVs) — RF shielding reduces CSI quality.
- **2.4 GHz** gives wider CSI coverage; **5 GHz** is more directional and better for precise zone discrimination.

### Vacuum map alignment

1. Run your vacuum on the floor you want to calibrate.
2. Identify 3+ easily-recognizable points visible in both the vacuum map UI and your floor plan (e.g., doorways, room corners).
3. Note the pixel coordinates in the vacuum map image.
4. Note the corresponding WiFi grid cell (col, row) at the integration's default resolution (0.5 m/cell).
5. Call `calibrate_vacuum_map` with these correspondences.
6. Check the calibration residual in the logs — values below 1.0 cells indicate good alignment.

### Baseline learning

After installing new furniture or making major room changes:
1. Call `wifisense_mapper.learn_baseline` to reset the EWMA.
2. Let the integration collect data for at least 24 hours (ideally 7 days) before relying on anomaly alerts.
3. Monitor `sensor.{floor}_anomaly_score` — once it stabilizes below your threshold, anomaly detection is reliable.

---

## Limitations (please read)

### RSSI accuracy
RSSI (Received Signal Strength Indicator) is coarse and highly affected by:
- **Multipath reflections** from walls, furniture, and people
- **Mesh roaming** — a client may hop between APs between polls, causing apparent location jumps
- **Channel interference** from neighboring networks
- **Body shielding** — a phone in a pocket can show 10–15 dBm variation

**Best practice:** Use CSI nodes (ESPectre/TOMMY) for motion/presence. Use RSSI only for rough AP-proximity positioning.

### CSI accuracy
Channel State Information is more detailed than RSSI but:
- Requires active 802.11 traffic in the area (not passive sensing)
- Multi-path dependent — highly variable in rooms with lots of reflective surfaces
- Sensitive to antenna orientation and node placement
- Channel switches by nearby APs temporarily disrupt readings

### Anomaly detection
- Statistical (EWMA z-score), not computer vision — cannot identify *what* changed
- False positives during baseline learning period (first ~100 samples)
- False positives after router channel changes or new WiFi devices appear
- Expect a tuning period of 1–2 weeks after initial setup

### Vacuum maps
- Used for **validation and spatial context only** — not a primary sensor
- Pixel coordinate systems vary by manufacturer/firmware; recalibration needed after map regeneration
- Multi-floor support requires separate calibration per floor

### Device tracking
- Tracks **devices** (phones, laptops), not people
- A device in standby may not appear connected
- Room-level accuracy: typically 2–8 meters in residential homes

---

## Architecture

```
wifisense_mapper/
├── __init__.py          # Setup, unload, service handlers, storage
├── manifest.json        # HA manifest (tplinkrouterc6u dependency)
├── config_flow.py       # Multi-step UI setup + options flow
├── coordinator.py       # Central DataUpdateCoordinator
├── const.py             # Domain constants
├── csi_discovery.py     # ESPectre/TOMMY entity discovery
├── registry_helpers.py  # Floor/Area registry wrappers
├── vacuum_helpers.py    # Vacuum map entity discovery + image fetch
├── sensor.py            # RSSI, CSI, anomaly, client count sensors
├── binary_sensor.py     # Presence, anomaly, CSI motion binary sensors
├── image.py             # Heatmap image entities
├── device_tracker.py    # Room-level device trackers
├── services.yaml        # Service definitions
├── strings.json         # UI copy
└── engine/
    ├── grid.py          # Sparse 2D spatial grid
    ├── heatmap.py       # IDW interpolation + PNG rendering
    ├── baseline.py      # EWMA baseline learner + z-score anomaly
    └── vacuum_align.py  # Affine transform calibration (vacuum ↔ grid)
```

Data flow:
```
Router (Deco/UniFi) ──→ RSSI samples ──→ SpatialGrid ──→ HeatmapRenderer ──→ image.* entities
ESPectre/TOMMY      ──→ CSI scores   ──→ SpatialGrid ──→ BaselineLearner ──→ binary_sensor.* / sensor.*
Vacuum map entity   ──→ PNG bytes    ──→ VacuumAligner ──→ room boundary hints
HA Floor/Area reg   ──→ spatial context for all entities
```

---

## Related Projects

| Project | Role |
|---------|------|
| [ESPectre](https://github.com/francescopace/espectre) | ESP32 CSI sensing — preferred primary motion source |
| TOMMY | Alternative ESP32 WiFi sensing |
| [ha-tplink-deco](https://github.com/amosyuen/ha-tplink-deco) | Pattern reference for Deco local API |
| [tplinkrouterc6u](https://pypi.org/project/tplinkrouterc6u/) | PyPI package for Deco authentication |
| [Zircon3D](https://github.com/nickcoutsos/keyswitch-layout-display) | 3D floorplan visualization |
| [Floorplan](https://github.com/ExperienceLovelace/ha-floorplan) | SVG-based Lovelace floorplan cards |
| [Roborock HA integration](https://www.home-assistant.io/integrations/roborock/) | Core HA vacuum map source |
| [Valetudo](https://valetudo.cloud/) | Open firmware for robot vacuums with map API |
| [mqtt_vacuum_camera](https://github.com/sca075/mqtt_vacuum_camera) | MQTT-based vacuum map camera |

---

## Contributing

Issues, pull requests, and ideas are welcome! Please keep contributions focused on:
- Reliable HA integration patterns
- Reuse of existing integrations (not reimplementing what exists)
- Light external dependencies
- Clear documentation of accuracy/limitation trade-offs

Before submitting a PR, run:
```bash
pip install -r requirements_test.txt
ruff check custom_components/wifisense_mapper tests/
pytest tests/ -v
```

---

## License

MIT License — see [LICENSE](LICENSE)

---

**Status:** Active development | v0.1.0
The goal is a clean, production-ready HACS integration that makes WiFi sensing spatially useful inside Home Assistant.
