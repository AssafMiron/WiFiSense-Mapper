# WiFiSense Mapper

**Custom Home Assistant integration for WiFi-based room mapping, signal heatmaps, and object/furniture detection.**

WiFiSense Mapper fuses data from your mesh router, ESP32 CSI sensors, existing Home Assistant floors & areas, and optional robot vacuum maps to create interactive room maps, signal strength / variance heatmaps, and anomaly detection for objects or furniture changes.

It is designed as a **glue layer** that reuses what you already have instead of reinventing everything.

## Features

- **Router polling** – Client RSSI, connected devices, and basic stats from TP-Link Deco (and extensible to UniFi / others)
- **ESP32 CSI support** – Native discovery of ESPectre and TOMMY nodes (`motion_detected`, `movement_score`, multi-node localization)
- **Deep HA integration** – Automatically uses your existing **Floors** and **Areas** registries
- **Robot vacuum map validation** – Optional alignment and cross-checking with Roborock, Valetudo, or other mapping vacuums
- **Mapping engine** – Per-floor 2D grids, RSSI/CSI fingerprints, heatmaps (signal strength, variance, movement), and baseline anomaly detection
- **Device tracking enhancement** – Rough room-level positioning using WiFi fingerprints + area/floor context
- **HACS-ready** – Standard custom component structure with config flow

## What this is *not*

This is **not** a replacement for ESPectre, TOMMY, your router integration, or Zircon3D/Floorplan.  
It is the missing piece that ties them together with HA’s organizational structure (floors/areas) and optional vacuum maps for better spatial context.

## Requirements

- Home Assistant 2024.x or newer (recommended)
- At least one of:
  - ESP32 CSI nodes running **ESPectre** or **TOMMY** (strongly recommended)
  - Supported mesh router (TP-Link Deco via existing patterns, UniFi, etc.)
- Optional but highly useful:
  - Defined **Floors** and **Areas** in Home Assistant
  - Mapping robot vacuum (Roborock core integration, Valetudo + map camera, etc.)

## Installation

### HACS (recommended)

1. Add this repository as a custom repository in HACS (Integration category)
2. Search for **WiFiSense Mapper** and install
3. Restart Home Assistant
4. Go to **Settings → Devices & Services → Add Integration** and search for WiFiSense Mapper

### Manual

Copy the `custom_components/wifisense_mapper` folder into your `config/custom_components/` directory and restart.

## Configuration

The integration uses a config flow:

1. Select or discover your data sources (router, CSI nodes)
2. Link detected nodes/zones to existing **Floors** and **Areas**
3. (Optional) Select vacuum map entities for alignment/validation
4. Configure polling intervals and basic thresholds

Services are provided for heatmap generation, baseline learning, calibration, and map export.

## Architecture Overview

- `coordinator.py` – Central data update coordinator
- Router client base class (extensible)
- Automatic discovery of ESPectre/TOMMY entities via the entity registry
- Floor & Area registry integration
- Optional vacuum map image / segment consumption
- Heatmap generation and simple anomaly detection (threshold + baseline)
- Map data stored via Home Assistant’s storage helpers

## Visualization

Heatmaps and map layers are exposed as image/camera entities so they work with:

- Zircon3D
- Floorplan cards
- picture-elements
- xiaomi-vacuum-map-card style overlays

## Limitations (please read)

- RSSI alone is coarse and multipath-sensitive. CSI (ESPectre/TOMMY) is significantly better for motion and presence.
- Object/furniture detection is based on persistent signal anomalies — not computer vision. Expect occasional false positives and the need for baseline learning.
- Mesh roaming and channel changes can affect CSI stability.
- Vacuum maps are used for **alignment and validation**, not as a primary sensing source.
- This is early-stage software. Expect rough edges.

## Inspiration & Related Projects

- [ESPectre](https://github.com/francescopace/espectre) – Excellent ESP32 CSI motion sensing
- TOMMY – Another strong ESP32 WiFi sensing project
- ha-tplink-deco and other router integrations
- Zircon3D, Floorplan, and vacuum map cards
- Home Assistant Floors & Areas system

## Contributing

Issues, PRs, and ideas are welcome. Please keep the focus on reliable HA integration and reuse of existing components rather than adding heavy new dependencies.

## License

MIT License – see [LICENSE](LICENSE)

---

**Status:** Early development / proof-of-concept stage.  
The goal is a clean, maintainable HACS integration that makes WiFi sensing spatially useful inside Home Assistant.
