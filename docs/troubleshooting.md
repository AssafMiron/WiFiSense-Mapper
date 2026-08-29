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

### 3. Heatmaps Are Blank, Broken, or Not Loading
* **Symptom**: `image.*` entities show broken image icons or fail to load.
* **Causes & Fixes**:
  * **Pillow Requirement & Installation**:
    * **Home Assistant OS / Supervised / Container**: Home Assistant automatically installs Pillow when WiFiSense Mapper is loaded. If it failed to install during startup, restart Home Assistant.
    * **Home Assistant Core (Python venv)**: If running HA Core directly in a virtual environment, install Pillow manually:
      ```bash
      source /path/to/homeassistant/bin/activate
      pip install Pillow>=10.0.0
      ```
    * **Pure Python Fallback**: WiFiSense Mapper includes a zero-dependency pure-Python PNG encoder and baseline grid generator so heatmaps remain valid PNG images even without Pillow.
  * **Heatmap Disabled**: Verify in **Configure** that *Enable heatmap generation* is toggled ON.
  * **Warming-Up Floor**: Floors without telemetry show a clean neutral blueprint baseline grid until devices or CSI nodes transmit signals.

---

### 4. Deco Hub Nearby but Area Presence is "Away"
* **Symptom**: You are in a room (e.g. Office) with a Deco hub, but the presence sensor stays `Away`.
* **Causes & Fixes**:
  * **Hub Area Placement Not Configured**:
    1. Go to **Settings → Devices & Services → WiFiSense Mapper → Configure**.
    2. Select **Access Points & Deco Hub Placements**.
    3. Match your Deco hub's MAC address or name to the **Office** area in Home Assistant.
    4. Alternatively, assign the Deco device directly to the Area in Home Assistant (**Settings → Devices → TP-Link Deco → Area**). WiFiSense will automatically auto-link it.
  * **Client Roaming**: Check `devices_detail` attribute on the Presence sensor to see which AP your phone/laptop is currently connected to and its estimated distance.

---

### 5. Roborock / Vacuum Room Alignment
* **Symptom**: How to align Roborock vacuum rooms with WiFiSense areas.
* **Guide**:
  1. Ensure your vacuum integration (Roborock / Valetudo / Dreame) is loaded in Home Assistant.
  2. Open **Settings → Devices & Services → WiFiSense Mapper → Configure**.
  3. Select **Vacuum Room Alignment (Roborock / Valetudo)**.
  4. Match each vacuum room segment (e.g., "Office", "Kitchen") to your Home Assistant Area.
  5. Room boundaries and cleaning zones will be used to validate spatial anomalies and occupancy.

---

### 6. Frequent False Anomaly Alerts (`binary_sensor.*_object_anomaly`)
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
