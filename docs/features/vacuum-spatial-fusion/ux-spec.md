# UX Spec — Vacuum Map Spatial Fusion & Dual-Interval Engine

## 1. User Interface & Configuration Workflows

### 1.1 Options Flow Enhancements
The integration Options Flow is extended with intuitive configuration steps:
1. **General Timing & Polling Step**:
   - `poll_interval`: Background engine interval in seconds (default `60`, range `15–3600`).
   - `fast_event_push`: Boolean switch (default `true`) enabling instant event-driven location updates on client roaming and CSI motion.
   - `heatmap_enabled`: Enable/disable background PNG generation.
2. **Deco Mesh Anchors Placement Step**:
   - Lists the 3 detected Deco units.
   - Allows assigning each Deco to a Room (Area selector) and configuring its estimated position in the room (e.g., Center, North Wall, South Wall, or X/Y percentage 0–100%).
3. **Roborock Vacuum Alignment & Furniture Detection Step**:
   - Selects the Roborock map entity (`image.*_map`).
   - Displays discovered rooms and furniture clusters detected by the S8 MaxV.
   - Allows one-click conversion: *"Import Detected Furniture as Micro-Zones"*.

---

## 2. Lovelace Dashboard Integrations

### 2.1 Rich Picture-Elements Floorplan Card
Overlays the live Roborock map with live WiFiSense layers:
```yaml
type: picture-elements
image: /api/image_proxy/image.roborock_s8_maxv_map
elements:
  # 1. Semi-transparent Signal / Wall Attenuation Layer
  - type: image
    entity: image.ground_floor_signal_heatmap
    style:
      left: 0%
      top: 0%
      width: 100%
      opacity: 0.5
      mix-blend-mode: multiply

  # 2. Deco Mesh Radio Anchors
  - type: icon
    icon: mdi:router-wireless
    title: "Living Room Deco"
    style: {left: 45%, top: 50%, color: "#03a9f4", "--mdc-icon-size": "28px"}

  - type: icon
    icon: mdi:router-wireless
    title: "Office Deco"
    style: {left: 80%, top: 25%, color: "#03a9f4", "--mdc-icon-size": "28px"}

  # 3. Person Dynamic Avatar with Micro-Zone State
  - type: state-badge
    entity: sensor.assaf_location
    style:
      left: "${state_attr('sensor.assaf_location', 'x_pct')}%"
      top: "${state_attr('sensor.assaf_location', 'y_pct')}%"
      transform: translate(-50%, -50%)
```

### 2.2 Modern Presence & Micro-Zone Tile Cards
```yaml
type: vertical-stack
cards:
  - type: tile
    entity: sensor.assaf_location
    name: Assaf Current Room
    icon: mdi:account-badge
    state_content:
      - state
      - micro_zone
      - activity

  - type: tile
    entity: sensor.assaf_activity
    name: Physical Activity
    icon: mdi:motion-sensor
```

---

## 3. UI Copy & Localization Strings (`strings.json` / `en.json`)

```json
{
  "options": {
    "step": {
      "init": {
        "title": "WiFiSense Mapper Engine Settings",
        "data": {
          "poll_interval": "Background Engine Interval (seconds)",
          "fast_event_push": "Enable instant event-driven location push",
          "heatmap_enabled": "Enable 2D heatmap generation"
        }
      },
      "deco_anchors": {
        "title": "Configure Deco Radio Anchors",
        "description": "Specify the physical locations of your 3 Deco hubs to anchor spatial triangulation to your room layout."
      },
      "vacuum_furniture": {
        "title": "Roborock Furniture & Micro-Zones",
        "description": "Detected furniture clusters from Roborock S8 MaxV can be automatically tracked as sub-room micro-zones."
      }
    }
  }
}
```
