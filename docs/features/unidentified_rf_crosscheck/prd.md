# PRD — Unidentified Presence & RF Motion Cross-Checking Engine

## 1. Problem Statement & HA Use Cases
Home Wi-Fi presence detection suffers from two primary limitations:
1. **Device-bound Blindspots**: Device trackers only detect individuals carrying active, Wi-Fi-connected devices (smartphones, watches). When a guest, family member without a phone, or unauthorized intruder moves through the home, device trackers report no presence.
2. **Device-Free Ambiguity & False Alarms**: Device-free RF disturbance sensing detects physical human movement across mesh backhaul and stationary Wi-Fi links, but cannot inherently identify *who* is moving. Without cross-referencing against connected devices, every RF motion event could be misinterpreted as an intruder or cause duplicate presence triggers when a known family member moves.

### Core Home Assistant Use Cases
- **Intruder / Unauthorized Presence Alerts**: Trigger instantaneous home security alarms or push notifications only when physical RF motion is detected in an area with *no* verified family members nearby (`binary_sensor.{area}_unidentified_presence`).
- **De-duplicated Presence**: Maintain a single, unified room presence sensor (`binary_sensor.{area}_presence`) that accurately classifies occupancy as `"verified"` (known person), `"unidentified"` (unrecognized person), or `"mixed"` (both known and unknown occupants).
- **Physical Proximity & Activity Triage**: When RF disturbance coincides with a connected person's proximity, corroborate that the person is physically walking, reset standby/sleep decay, and maximize localization confidence.

---

## 2. Integration Scope

### In Scope
- **Cross-checking Arbiter**: Fusion logic between `RFSensingSnapshot` and `PersonLocalizationEngine` / router clients.
- **Proximity & Spatial Matching**:
  - Primary: Area match + AP distance threshold ($\le 3.5$ m or RSSI $\ge -60$ dBm).
  - Secondary Wall-Bleed Fallback: Geometric distance check on 2D grid ($\le 3.5$ m) to suppress false intruder alerts from adjacent rooms.
  - Asynchronous Coincidence Window: $\pm 15$ seconds sliding coincidence window to tolerate Wi-Fi polling delays and roaming handoffs.
  - Sitting Person Behavior: When RF motion is detected near a stationary occupant, transition activity to `walking` and attribute motion to the known person.
- **Entity Model**:
  - Enhanced `binary_sensor.{area}_presence` attributes (`occupant_type`, `unidentified_motion`, `verified_occupants`, `unidentified_count`, `rf_corroborated_occupant`).
  - Dedicated `binary_sensor.{area}_unidentified_presence` entity for one-click security automations.
- **Localization Feedback**: Corroborated person transitions to `walking`, resets sleep decay timer, and boosts confidence.

### Out of Scope
- Facial recognition or camera video analysis.
- Artificial coordinate snapping of the person's location to the AP (preserving smooth Kalman/trilateration filters).

---

## 3. Entity & Service Model

### 3.1 Entities
| Domain | Entity ID Pattern | Device Class | State / Values | Key Attributes |
| :--- | :--- | :--- | :--- | :--- |
| `binary_sensor` | `binary_sensor.{area}_presence` | `presence` | `on` / `off` | `occupant_type` (`verified`, `unidentified`, `mixed`, `none`), `unidentified_motion` (`bool`), `verified_occupants` (`list[str]`), `unidentified_count` (`int`), `rf_corroborated_occupant` (`str \| None`) |
| `binary_sensor` | `binary_sensor.{area}_unidentified_presence` | `motion` | `on` / `off` | `area_id`, `area_name`, `disturbance_score`, `coincidence_window_sec`, `last_unidentified_time` |

### 3.2 Services & Config Options
- `CONF_RF_PROXIMITY_THRESHOLD_M`: Distance threshold to match person to AP (default: `3.5` meters).
- `CONF_RF_COINCIDENCE_WINDOW_S`: Sliding coincidence window in seconds (default: `15.0` seconds).
- `CONF_RF_WALL_BLEED_SUPPRESSION`: Enable geometric wall-bleed check across adjacent rooms (default: `True`).

---

## 4. Acceptance Criteria (DoD)
1. **Unidentified Detection**: RF disturbance in an area with zero known occupants turns `binary_sensor.{area}_unidentified_presence` `on` and sets `occupant_type: "unidentified"` on `binary_sensor.{area}_presence`.
2. **Corroborated Match**: RF disturbance in an area with a known occupant within $\le 3.5$m keeps `binary_sensor.{area}_unidentified_presence` `off`, sets `occupant_type: "verified"`, and transitions person activity to `walking`.
3. **Mixed Presence**: If an area has a verified person far away ($> 3.5$m) and an unclaimed RF motion event occurs at a distant link/AP, `occupant_type` becomes `"mixed"` and `unidentified_presence` turns `on`.
4. **Wall-Bleed Suppression**: If a person is in an adjacent room but physically within 3.5m of the disturbed router link across a wall, RF motion is attributed to that person and does not trigger an intruder alert.
5. **Sliding Window**: A person roaming or polling within $\pm 15$s of the RF disturbance matches the event retroactively.
