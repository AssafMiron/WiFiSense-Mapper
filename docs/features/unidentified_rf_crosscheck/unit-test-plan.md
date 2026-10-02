# Unit & Mock Test Plan — Unidentified RF Cross-Checking

## 1. Scope of Unit Tests
- New test module: `tests/test_rf_crosscheck.py`
  - Engine tests for `RFCrossCheckEngine`:
    - Test match with person in same area within proximity threshold ($\le 3.5$m).
    - Test no match when person is $> 3.5$m away in same area $\to$ unidentified motion triggers.
    - Test stationary person transition to walking upon RF match.
    - Test wall-bleed suppression across adjacent areas using grid coordinates.
    - Test sliding coincidence window ($\pm 15$s).
    - Test mixed occupancy reporting.
- Entity & Integration tests:
  - In `tests/test_sensor.py` / `tests/test_platforms.py`:
    - Verify `PresenceBinarySensor` attributes: `occupant_type`, `unidentified_motion`, `verified_occupants`, `unidentified_count`, `rf_corroborated_occupant`.
    - Verify `WiFiSenseUnidentifiedPresenceBinarySensor` setup, state, and attributes.
