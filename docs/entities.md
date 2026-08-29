# Entities Reference

WiFiSense Mapper creates native Home Assistant entities across several platforms. These entities update dynamically as telemetry is polled from routers and ESP32 CSI nodes.

---

## 1. Binary Sensors (`binary_sensor.*`)

Binary sensors provide on/off signals for automations, alerts, and security systems.

| Entity ID | Device Class | State (`on`/`off`) | Description |
|---|---|---|---|
| `binary_sensor.{area}_presence` | `presence` | `on` = Presence detected | Fused presence indicator. Fires if any WiFi client is associated to this area's AP or if CSI nodes in this area detect motion. |
| `binary_sensor.{floor}_csi_motion` | `motion` | `on` = Motion detected | Aggregated ESP32 CSI motion for the entire floor. Triggers on Doppler / subcarrier disruptions. |
| `binary_sensor.{floor}_object_anomaly` | `problem` | `on` = Anomaly active | Triggers when the spatial anomaly z-score exceeds the configured threshold vs. the learned baseline. |

### Presence Binary Sensor Attributes
```yaml
area_id: living_room
device_count: 3
devices:
  - "iPhone-15"
  - "MacBook-Pro"
  - "aa:bb:cc:dd:ee:ff"
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
| `sensor.{floor}_wifi_client_count` | `clients` | `measurement` | Total count of associated WiFi client devices on this floor. |
| `sensor.{ap_name}_average_rssi` | `dBm` | `measurement` | Average signal strength across all clients connected to this specific AP. |
| `sensor.{node}_motion_score` | `score` | `measurement` | Real-time CSI motion score reported by an ESPectre or TOMMY node. |
| `sensor.{floor}_anomaly_score` | `σ` (z-score) | `measurement` | Maximum statistical deviation score across all grid cells on this floor. |

---

## 3. Image Entities (`image.*`)

Heatmap layers are rendered as 2D PNG images and exposed as standard Home Assistant `ImageEntity` objects.

| Entity ID | Layer | Description |
|---|---|---|
| `image.{floor}_signal_heatmap` | `signal` | WiFi signal strength (RSSI) interpolated across the floor. |
| `image.{floor}_variance_heatmap` | `variance` | Signal variance identifying RF shadows and structural obstructions. |
| `image.{floor}_motion_heatmap` | `motion` | Multi-node CSI motion intensity. |
| `image.{floor}_anomaly_heatmap` | `anomaly` | Heatmap of anomalous cells deviating from learned baseline. |

### Accessing Image Streams:
You can directly use the image URL inside Lovelace cards:
```
/api/image_proxy/image.ground_floor_signal_heatmap
```

---

## 4. Device Trackers (`device_tracker.*`)

WiFiSense Mapper creates room-level device trackers for connected WiFi devices (up to 50 active clients):

| Entity ID | State | Source Type | Description |
|---|---|---|---|
| `device_tracker.{hostname_or_mac}` | `{area_name}` / `not_home` | `router` | Position of the device based on its connected Access Point and area assignment. |

> [!NOTE]
> Device tracking operates at **room-level** granularity (AP proximity), not GPS accuracy. Devices in deep sleep may report stale locations until active WiFi traffic resumes.
