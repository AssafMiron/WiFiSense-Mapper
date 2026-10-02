# Entities Reference

WiFiSense Mapper creates native Home Assistant entities across several platforms. These entities update dynamically as telemetry is polled from routers and ESP32 CSI nodes.

---

## 1. Binary Sensors (`binary_sensor.*`)

Binary sensors provide on/off signals for automations, alerts, and security systems.

| Entity ID | Device Class | State (`on`/`off`) | Description |
|---|---|---|---|
| `binary_sensor.{area}_presence` | `presence` | `on` = Presence detected | Fused presence indicator. Fires if any WiFi client is associated to this area's AP, a localized person is present, CSI nodes detect motion, or unidentified RF motion occurs. |
| `binary_sensor.{area}_unidentified_presence` | `motion` | `on` = Unidentified motion | **Intruder / Device-Free Alert**. Fires when physical RF motion is detected in this room with **no** matching connected family member/device nearby. |
| `binary_sensor.{area}_rf_motion` | `motion` | `on` = Motion detected | **Device-Free RF Motion Sensing**. Triggers when human movement perturbs Deco wireless backhaul or stationary Wi-Fi IoT links in this room (works even without phones). |
| `binary_sensor.{floor}_csi_motion` | `motion` | `on` = Motion detected | Aggregated ESP32 CSI motion for the entire floor. Triggers on Doppler / subcarrier disruptions. |
| `binary_sensor.{floor}_object_anomaly` | `problem` | `on` = Anomaly active | Triggers when the spatial anomaly z-score exceeds the configured threshold vs. the learned baseline. |

### Presence Binary Sensor Attributes
```yaml
area_id: living_room
occupants:
  - "Alice"
occupant_count: 1
occupant_type: "verified"  # "verified" | "unidentified" | "mixed" | "none"
unidentified_motion: false
verified_occupants:
  - "Alice"
unidentified_count: 0
rf_corroborated_occupant: "Alice"
device_count: 3
devices:
  - "iPhone-15"
  - "MacBook-Pro"
  - "aa:bb:cc:dd:ee:ff"
nearest_distance_m: 2.1
```

### Unidentified Presence Binary Sensor Attributes
```yaml
area_id: living_room
area_name: Living Room
disturbance_score: 82.5
active_links_count: 2
occupant_type: "unidentified"
verified_occupants: []
proximity_threshold_m: 3.5
coincidence_window_sec: 15.0
last_unidentified_ts: 1727827200.0
```

### RF Motion Binary Sensor Attributes
```yaml
area_id: living_room
area_name: Living Room
disturbance_score: 74.5
active_links_count: 3
sensitivity: medium
off_delay_sec: 30
monitored_links:
  - link_id: "backhaul:11:11:11:11:11:11->22:22:22:22:22:22"
    type: backhaul
    peer_name: Living Room Deco
    rssi: -78
    baseline: -65.0
    variance: 8.4
    score: 74.5
    perturbed: true
```

### Anomaly Binary Sensor Attributes
```yaml
floor_id: ground_floor
threshold: 3.0
max_anomaly_score: 4.12
anomalous_cell_count: 6
baseline_warmed_up: true
```

---

## 2. Sensors (`sensor.*`)

Sensor entities provide numeric values and signal strength measurements for dashboards, gauges, and historical statistics.

| Entity ID | Unit | State Class | Description |
|---|---|---|---|
| `sensor.{area}_rf_disturbance` | `%` | `measurement` | Real-time RF signal disturbance percentage (0–100%) indicating line-of-sight obstruction in this room. |
| `sensor.{floor}_wifi_client_count` | `clients` | `measurement` | Total count of associated WiFi client devices on this floor. |
| `sensor.{ap_name}_average_rssi` | `dBm` | `measurement` | Average signal strength across all clients connected to this specific AP. |
| `sensor.{node}_motion_score` | `score` | `measurement` | Real-time CSI motion score reported by an ESPectre or TOMMY node. |
| `sensor.{floor}_anomaly_score` | `σ` (z-score) | `measurement` | Maximum statistical deviation score across all grid cells on this floor. |
| `sensor.{person}_location` | — | — | Current room / area for a tracked person (e.g. `Living Room`, `Away`, or `Living Room → Kitchen` during transitions). |
| `sensor.{person}_activity` | — | — | Current physical state (`Stationary / Sitting`, `Walking / Moving`, `Room Transitioning`, or `Away`). |
| `sensor.{person}_confidence` | `%` | `measurement` | Confidence percentage of the room estimation based on RSSI margins, CSI, and dwell time. |
| `sensor.{person}_dwell_time` | `s` | `total` | Time in seconds the person has remained within their current room. |

---

## 3. Image Entities (`image.*`)

Heatmap layers are rendered as 2D PNG images and exposed as standard Home Assistant `ImageEntity` objects.

| Entity ID | Layer | Description |
|---|---|---|
| `image.{floor}_signal_heatmap` | `signal` | WiFi signal strength (RSSI) interpolated across the floor. |
| `image.{floor}_variance_heatmap` | `variance` | Signal variance identifying RF shadows and structural obstructions. |
| `image.{floor}_motion_heatmap` | `motion` | Multi-node CSI motion intensity. |
| `image.{floor}_anomaly_heatmap` | `anomaly` | Heatmap of anomalous cells deviating from learned baseline. |
| `image.{floor}_rf_links_heatmap` | `rf_links` | Real-time RF link rays between Deco nodes and stationary IoT devices (green = clear, orange/red = perturbed). |

### Accessing Image Streams:
You can directly use the image URL inside Lovelace cards:
```
/api/image_proxy/image.ground_floor_signal_heatmap
```

---

## 4. Device Trackers (`device_tracker.*`)

WiFiSense Mapper provides room-level device tracking for individual Wi-Fi clients and mapped persons:

| Entity ID | State | Source Type | Description |
|---|---|---|---|
| `device_tracker.{person}_wifi` | `{area_name}` / `not_home` | `router` | Position of a tracked Person mapped via the Person Tracking options flow. Attached directly to the Person's unified HA device. |
| `device_tracker.{hostname_or_mac}` | `{area_name}` / `not_home` | `router` | Position of an individual WiFi client based on connected AP proximity and area assignment (opt-in or when tagged). |

> [!NOTE]
> Device tracking operates at **room-level** granularity (AP proximity), not GPS accuracy. Devices in deep sleep may report stale locations until active WiFi traffic resumes. Trackers for untagged devices are kept disabled by default to avoid entity explosion on large home networks.
