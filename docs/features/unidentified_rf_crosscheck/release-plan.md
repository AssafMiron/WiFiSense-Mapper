# Release Plan — Unidentified RF Cross-Checking & Triage

## 1. Release Metadata
- **Feature Name**: Unidentified Presence Detection & RF Proximity Cross-Checking
- **Component Version**: 0.3.1 (or minor release)
- **Target Integration Domain**: `wifisense_mapper`

## 2. Breaking Changes & Migration Notes
- **Backward Compatibility**: Fully backward compatible.
  - Existing `binary_sensor.presence_{area}` entities continue to report `on` / `off` presence as before, with enriched attributes (`occupant_type`, `unidentified_motion`, `verified_occupants`, `unidentified_count`, `rf_corroborated_occupant`).
  - Existing `binary_sensor.{area}_rf_motion` entities continue to expose raw RF perturbations.
  - New entity `binary_sensor.{area}_unidentified_presence` is added per area, defaulting into the same device and area.
  - Configuration options default to safe values (`rf_proximity_threshold_m: 3.5`, `rf_coincidence_window_sec: 15`, `rf_wall_bleed_suppression: true`).

## 3. Manifest & HACS Validation
- `manifest.json`: Checked; iot_class `local_polling` / `local_push`, codeowners, dependencies remain valid.
- `hacs.json`: Checked; compliant with HACS requirements.
- Translation strings: Updated in `strings.json` and `translations/en.json`.
- Test coverage: Full pytest test suite with 129 passing unit and mock tests.
