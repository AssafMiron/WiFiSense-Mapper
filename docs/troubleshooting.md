# Troubleshooting & Calibration Guide

Use this guide to diagnose and resolve common setup, connection, calibration, and accuracy issues.

---

## 🔍 Quick Diagnostics: Enable Verbose Logs

To see detailed internal logs from WiFiSense Mapper, add the following to your Home Assistant `configuration.yaml` and restart Home Assistant:

```yaml
logger:
  default: info
  logs:
    custom_components.wifisense_mapper: debug
```

View live logs via **Settings → System → Logs** or search for `wifisense_mapper` in your HA log file.

---

## Common Issues & Solutions

### 1. "Cannot connect" or Router Authentication Errors (TP-Link Deco)
* **Symptom**: Config flow reports `cannot_connect` or coordinator shows `Router poll failed`.
* **Causes & Fixes**:
  * **Incorrect Password**: Make sure you are using the Deco web admin password (used when browsing to the router IP), not your WiFi SSID network password or TP-Link Cloud password.
  * **Router IP Changed**: Ensure your Deco primary unit has a static DHCP reservation or fixed IP.
  * **Concurrent Logins**: Some Deco firmware versions only allow one active web session. If logged into the web UI in a browser, log out and retry.

---

### 2. CSI Nodes Not Discovered (ESPectre / TOMMY)
* **Symptom**: `coordinator.py` reports `Found 0 CSI node(s)`.
* **Causes & Fixes**:
  * **Entity Naming**: Ensure your ESPHome or MQTT nodes expose entities with names or unique IDs containing `motion_score`, `motion_detected`, `espectre`, or `csi`.
  * **Unassigned Areas**: Ensure the ESP32 devices are assigned to an **Area** in HA (*Settings → Devices & Services → ESPHome/MQTT → click Device → Assign Area*).
  * **Manual Override**: You can link any node manually using the service:
    ```yaml
    service: wifisense_mapper.link_node_to_area
    data:
      node_id: "my_esp32_device"
      area_id: "living_room"
    ```

---

### 3. Heatmaps Are Blank or Not Updating
* **Symptom**: `image.*` entities show empty or static placeholders.
* **Causes & Fixes**:
  * **Heatmap Disabled**: Verify in **Configure** that *Heatmap generation* is toggled ON.
  * **No Telemetry Yet**: The integration requires at least one active router client or CSI node to start drawing signal cells.
  * **Pillow Library**: Check logs for `Pillow` errors. The integration automatically falls back to standard BMP rendering if Pillow is unavailable, but installing Pillow is recommended.

---

### 4. Frequent False Anomaly Alerts (`binary_sensor.*_object_anomaly`)
* **Symptom**: Anomaly sensor frequently turns `on` when nothing in the room has changed.
* **Causes & Fixes**:
  * **Baseline Not Warmed Up**: The statistical learner needs 48 hours to 7 days to learn daily ambient variance. Check the `baseline_warmed_up` attribute on the sensor.
  * **Threshold Too Low**: Open **Configure** and raise the **Anomaly Threshold** from `3.0` to `4.0` or `5.0`.
  * **WiFi Channel Switching**: If your router frequently auto-switches channels, signal baselines shift. Lock your 2.4 GHz and 5 GHz channels to fixed frequencies in your router settings.
  * **Resetting Baseline**: If you recently rearranged furniture, reset the baseline:
    ```yaml
    service: wifisense_mapper.learn_baseline
    data:
      floor_id: ground_floor
    ```

---

### 5. Inaccurate Room Tracking for Devices
* **Symptom**: Phone is reported in the bedroom while you are in the kitchen.
* **Causes & Fixes**:
  * **Mesh Roaming Delay**: Phones often stay connected to a distant AP until the signal drops below a roaming threshold (-70 to -75 dBm). This is standard WiFi client roaming behavior.
  * **Device Standby / Deep Sleep**: iPhones and Android devices turn off active WiFi polling in standby to save battery. Location updates when the screen wakes or background data syncs.
  * **Access Point Area Alignment**: Ensure each mesh AP device in HA is assigned to its exact physical room Area in Home Assistant.

---

### 6. Vacuum Map Calibration Error: "Singular Matrix / Collinear Points"
* **Symptom**: Calling `calibrate_vacuum_map` logs an error about collinear points.
* **Fix**: Provide at least 3 points that form a **triangle** across the floorplan. Do NOT place all 3 calibration points along the same straight wall or hallway line.

---

## 🛠️ Calibration Guide

### ESP32 CSI Node Placement Best Practices
* **Height**: Place nodes at **chest height (~1.0–1.2 m)** for optimal human body reflection.
* **Density**: 1–2 nodes per room is ideal.
* **Avoid Shielding**: Do not place nodes directly behind metal appliances, TVs, or inside metal enclosures.
* **Frequency Bands**:
  * **2.4 GHz**: Longer range, penetrates walls better, covers wider rooms.
  * **5 GHz**: Higher resolution Doppler sensing, more confined to a single room.

### Step-by-Step Vacuum Map Alignment
To overlay WiFiSense heatmaps onto robot vacuum maps:
1. Run your vacuum (Roborock, Valetudo, Dreame) so a full map is loaded.
2. Pick 3 distinct landmarks visible in both the vacuum map and your HA floorplan (e.g. 3 room corners).
3. Find the pixel coordinates $(X, Y)$ in the vacuum map image.
4. Estimate the grid coordinates (col, row) on the 0.5 m WiFi grid.
5. Execute the calibration service:
   ```yaml
   service: wifisense_mapper.calibrate_vacuum_map
   data:
     floor_id: ground_floor
     calibration_points:
       - {vac_px: 120, vac_py: 80, grid_col: 4, grid_row: 3}
       - {vac_px: 300, vac_py: 80, grid_col: 10, grid_row: 3}
       - {vac_px: 120, vac_py: 250, grid_col: 4, grid_row: 8}
   ```
6. Check your HA logs: a residual value `< 1.0` confirms excellent spatial alignment.
