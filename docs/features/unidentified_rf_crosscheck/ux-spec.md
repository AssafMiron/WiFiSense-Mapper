# UX Specification — Unidentified Presence & RF Motion Cross-Checking

## 1. Options Flow UX
The Options Flow of WiFiSense Mapper gains an **RF Motion & Presence Calibration** section or expands the existing RF Sensing settings with:
- **`rf_proximity_threshold_m`** (Number selector, `1.0` to `10.0` meters, step `0.5`, default `3.5 m`):
  - Description: *"Maximum distance between a connected person and the router/AP to consider physical RF motion as caused by that person."*
- **`rf_coincidence_window_sec`** (Number selector, `5` to `60` seconds, step `5`, default `15 s`):
  - Description: *"Time window to match asynchronous router polling and mesh roaming handoffs with RF disturbances."*
- **`rf_wall_bleed_suppression`** (Boolean toggle, default `true`):
  - Description: *"Suppress false intruder alarms if a family member is in an adjacent room within physical range of the router across a wall."*

---

## 2. Lovelace Card & Dashboard Visualizations

### 2.1 Presence Badge & Conditional Alert Card
```yaml
type: vertical-stack
cards:
  # Area Presence & Verification Status
  - type: entities
    title: Living Room Occupancy
    entities:
      - entity: binary_sensor.presence_living_room
        secondary_info: last-changed
      - entity: binary_sensor.living_room_unidentified_presence
        name: "Unidentified Movement"
      - entity: binary_sensor.living_room_rf_motion
        name: "Raw RF Disturbance"

  # High-Priority Intruder Warning (Only displays when unknown movement is active)
  - type: conditional
    conditions:
      - entity: binary_sensor.living_room_unidentified_presence
        state: "on"
    card:
      type: markdown
      content: >
        ⚠️ **Unidentified Motion Detected in Living Room!**
        Physical movement sensed with no verified family member nearby.
```

### 2.2 Picture Elements Floorplan Integration
```yaml
- type: state-badge
  entity: binary_sensor.living_room_unidentified_presence
  style:
    top: 45%
    left: 60%
    "--label-badge-red": "#f44336"
```

---

## 3. UI Copy & Translation Schema (`strings.json` / `en.json`)

```json
{
  "entity": {
    "binary_sensor": {
      "unidentified_presence": {
        "name": "{area} Unidentified Presence"
      }
    }
  },
  "options": {
    "step": {
      "rf_sensing": {
        "data": {
          "rf_proximity_threshold_m": "Proximity Match Threshold (meters)",
          "rf_coincidence_window_sec": "Coincidence Match Window (seconds)",
          "rf_wall_bleed_suppression": "Wall-Bleed Cross-Room Suppression"
        },
        "description": "Configure RF disturbance cross-checking with connected person tracking."
      }
    }
  }
}
```
