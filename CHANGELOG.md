# Changelog

All notable changes to the **WiFiSense Mapper** integration will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

---

## [0.2.4] - 2026-09-02

### Fixed
- **Entity Naming & HA Best Practices**:
  - Deduplicated friendly names for entities where `has_entity_name = True` across image, binary sensor, and sensor platforms to prevent stuttering names in Home Assistant UI (e.g., avoiding "Ground Floor Heatmap Ground Floor Heatmap").
- **Deco AP & Client Attribution**:
  - Supported AP nickname-to-MAC resolution when Deco reports AP names as node nicknames (e.g., "office", "bedroom") instead of MAC addresses.
  - Added support for Deco string dBm, bar levels (1–4 bars converted to dBm), and multi-band RSSI dictionary formats.
  - Added strict MAC format validation in `RouterClient.normalize_mac` to prevent room names and nicknames from being treated as valid MACs.
- **Area Matching & Auto-Discovery**:
  - Guarded AP auto-matching against generic node names (e.g., "Main Deco", "Deco X60", "Living Room Deco") and misaligned parent integration areas.
- **Person Tracker Standby Telemetry**:
  - Resolved room tracking and active states when client AP is known even if RSSI is null or unmeasured.

---

## [0.2.3] - 2026-09-01

### Added
- **Deco Home Assistant Entity Bridge Mode**:
  - Automatically bridges through Home Assistant's `tplink_deco` device trackers and Device Registry to eliminate single-session admin web lockout and `{}` empty response errors.
  - Harvests connected clients, signal levels, and Deco node MAC associations without creating conflicting web sessions.
  - 1-Click zero-credentials setup when Deco is detected in Home Assistant.
- **Multi-AP Spatial Coverage & Overlays**:
  - Added `SpatialGrid.compute_multi_ap_coverage` computing dead zones (0.0), single AP coverage (1.0), and cross-covered overlap zones (2.0).
  - New `LAYER_COVERAGE` heatmap layer exposing `image.wifisense_mapper_<floor>_coverage` image entities for every floor.
  - Automatic rendering of room labels and Deco AP markers (`📶 Deco Name`) directly on floor heatmaps.
- **HA Device Registry Area Synchronization**:
  - Added `async_sync_device_area` to automatically update unassigned Deco devices in Home Assistant's native Device Registry with mapped areas.
  - Added `overwrite_ha_device_areas` toggle in Options Flow (`ap_mapping`).

### Fixed
- **Auto-Matcher Room Name Prioritization**:
  - Resolved latent mismatch bug by prioritizing device names (e.g. "Bedroom Deco" matching "bedroom" area with score $\ge 0.7$) over default parent integration areas (like "office").
- **Person Localization Standby & Coarse Positioning**:
  - Prevented person trackers from defaulting to `"Unknown Room"` during initial idle state.
  - Added coarse AP centering with confidence halo when exact dBm signal is unavailable.

---

## [0.2.2] - 2026-08-31

### Fixed
- **Options Flow Translation & Formatting**:
  - Provided missing formatjs translation placeholders (`ap_legend`, `seg_legend`, `client_legend`, `ap_count`, `segment_count`, `client_count`) across Options Flow steps to resolve `[formatjs Error: MISSING_VALUE]`.
  - Replaced dummy input textboxes (`info_no_vac`, `info_no_clients`, `info_no_aps`) with clean descriptive summaries.
- **Person WiFi Tracker Pipeline**:
  - Instantiated `WifiSenseDeviceTracker` entities directly from `person_tags` and `tracked_client_macs` at setup time, ensuring person trackers exist immediately before live router client telemetry arrives.
  - Enhanced client discovery in Options Flow across HA `device_tracker` entities and device registry devices.
- **Deco AP Naming & Telemetry Seeding**:
  - Expanded Deco node friendly name extraction across `custom_nickname`, `nickname`, `alias`, `room`, `location`, `device_name`, `model_name`, and `hardware_ver`.
  - Auto-seeded `self.ap_stats` from `node_area_map` during coordinator initialize and updates, activating room presence sensors, `area_ap_map`, and AP RSSI sensors immediately.
  - Elevated Deco client fetch errors from silent debug logs to warnings.
- **Sensors**:
  - Returned `0.0` default for `AnomalyScoreSensor.native_value` when idle to prevent `Unknown` state in UI.
  - Added `presence_entity_id` fallback when evaluating CSI motion states.
  - Fixed `matched_device` initialization in `registry_helpers.py` to prevent `UnboundLocalError`.

---

## [0.2.1] - 2026-08-31

### Fixed
- **Deco Client Telemetry & Persona Detection ([#6](https://github.com/AssafMiron/WiFiSense-Mapper/issues/6))**:
  - Replaced brittle `get_status()` with direct per-node `admin/client?form=client_list` requests (`{"device_mac": node_mac}`), bypassing `admin/wireless?form=wlan` errors on Deco X60 and ensuring client enumeration.
  - Added extraction of nested `signal_level` (`band5`, `band2_4`, `band6`) for client RSSI.
  - Added base64 string decoding for node `nickname`, `custom_nickname`, and client `name`.
  - Maintained per-node client AP association (`ap_mac`) for accurate presence localization.
- **Access Point Naming & Options Flow UX**:
  - Cross-referenced MAC addresses with Home Assistant's `device_registry` to retrieve user-configured friendly names (e.g., "Kitchen Deco", "Office Deco").
  - Displayed friendly names directly as the dropdown labels in Options Flow.
- **Robot Vacuum Room Segments**:
  - Expanded segment discovery to scan `vacuum.*` domain entities in addition to `image.*` and `camera.*` entities across multiple attribute schemas (`rooms`, `room_list`, `segments`, `map_rooms`, etc.).
- **Home Assistant Deprecations**:
  - Replaced deprecated `location_name` property override on `TrackerEntity` with `@property def state` in `device_tracker.py`.
  - Updated device registry iteration to avoid deprecation warnings in HA 2024+.

---

## [0.2.0] - 2026-08-31

### Added
- **Indoor Person Localization & Wearables Tracking**:
  - Added real-time person tracking binding discovered WiFi client MACs (smartwatches, smartphones, ESP32 tags) to Home Assistant `person.*` entities in Options Flow.
  - Implemented 2D Constant-Velocity Kalman filtering (`KalmanFilter2D`) to smooth RSSI telemetry and eliminate room boundary ping-pong / jitter.
  - Implemented multi-floor feasibility guards (`FloorTransitionGuard`) preventing spurious multi-floor ceiling bleed jumps.
  - Added micro-zone and furniture proximity detection (`MicroZone`) for sub-room localization (e.g., *"At Desk"*, *"On Couch"*).
  - Added real-time physical activity classification (`ActivityClassifier`): `Stationary / Sitting`, `Walking / Moving`, `Room Transitioning`, and `Away`.
- **Entities & Dashboard Visualizations**:
  - Added `sensor.wifisense_<person>_location` exposing current room / transition name with confidence, dwell time, and speed attributes.
  - Added `sensor.wifisense_<person>_activity` for automation triggers and presence telemetry.
  - Added `sensor.wifisense_<person>_coordinates` with normalized `x_pct` and `y_pct` coordinates (0–100%) for Lovelace `picture-elements` card overlays.
  - Upgraded `WifiSenseDeviceTracker` to integrate filtered indoor person location and coordinates attributes.
- **Unit & Integration Tests**:
  - Added comprehensive test suites in `tests/test_localization.py` and `tests/test_person_tracking.py`.

---

## [0.1.1] - 2026-08-30

### Fixed
- **Deco Client Data Parsing ([#3](https://github.com/AssafMiron/WiFiSense-Mapper/issues/3))**:
  - Fixed client list parsing by iterating over `status.devices` instead of the non-existent `status.clients` property on `tplinkrouterc6u` `Status` dataclasses.
  - Corrected per-client attribute accesses (`device.macaddr`, `device.ipaddr`, `device.hostname`, `device.signal`, `device.frequency`).
  - Fixed AP stats collection to query `admin/device?form=device_list` via `get_firmware()` to properly populate Deco mesh nodes and calculate per-node client counts.
  - Resolved username dropping in `DecoClient` by passing the configured username and `verify_ssl=False` to `TPLinkDecoClient`.
  - Added AP node MAC resolution from custom/model names so client AP association matches coordinator spatial stats.
- **Options Flow & Compatibility**:
  - Removed deprecated `config_entry` assignment in OptionsFlow.
  - Added fallback pure-Python PNG encoder and baseline grid for empty floors.

### Added
- **Options Flow Enhancement**:
  - Added Options Flow UI for Deco hub placement and Roborock room alignment.
  - Added auto-linking of Deco hubs to HA areas with RSSI distance analysis.
- **Unit Tests**:
  - Added dedicated unit test suite in `tests/test_deco.py` covering `tplinkrouterc6u` dataclass integration, field mappings, AP stats computation, and connection error handling.
- **Version Tracking**:
  - Added `VERSION` constant in `const.py` aligned with `manifest.json`.

---

## [0.1.0] - 2026-08-29

### Added
- Initial release of **WiFiSense Mapper** on HACS.
- Router client integrations for TP-Link Deco and UniFi.
- CSI motion and presence sensing integration with ESPectre / TOMMY.
- 2D grid spatial interpolation engine and multi-layer heatmap generator.
- Robot vacuum map scale alignment and coordinate transformation.
- Floor & Area registry linking and sensor/image platforms.
