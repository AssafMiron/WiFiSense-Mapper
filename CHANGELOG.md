# Changelog

All notable changes to the **WiFiSense Mapper** integration will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

---

## [0.1.1] - 2026-08-30

### Fixed
- **Deco Client Data Parsing ([#3](https://github.com/AssafMiron/WiFiSense-Mapper/issues/3))**:
  - Fixed client list parsing by iterating over `status.devices` instead of the non-existent `status.clients` property on `tplinkrouterc6u` `Status` dataclasses.
  - Corrected per-client attribute accesses (`device.macaddr`, `device.ipaddr`, `device.hostname`, `device.signal`, `device.frequency`).
  - Fixed AP stats collection to query `admin/device?form=device_list` via `get_firmware()` to properly populate Deco mesh nodes and calculate per-node client counts.
  - Resolved username dropping in `DecoClient` by passing the configured username and `verify_ssl=False` to `TPLinkDecoClient`.
  - Added AP node MAC resolution from custom/model names so client AP association matches coordinator spatial stats.

### Added
- **Unit Tests**: Added dedicated unit test suite in `tests/test_deco.py` covering `tplinkrouterc6u` dataclass integration, field mappings, AP stats computation, and connection error handling.
- **Version Tracking**: Added `VERSION` constant in `const.py` aligned with `manifest.json`.

---

## [0.1.0] - 2026-08-29

### Added
- Initial release of **WiFiSense Mapper** Home Assistant custom integration.
- Router client integrations for TP-Link Deco and UniFi.
- CSI motion and presence sensing integration with ESPectre / TOMMY.
- 2D grid spatial interpolation engine and multi-layer heatmap generator.
- Robot vacuum map scale alignment and coordinate transformation.
- Floor & Area registry linking and sensor/image platforms.
