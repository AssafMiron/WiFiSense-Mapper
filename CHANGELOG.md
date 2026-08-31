# Changelog

All notable changes to the **WiFiSense Mapper** integration will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

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
