# E2E & Validation Plan — Unidentified RF Cross-Checking

## 1. End-to-End Workflow Validation
1. **Initial Setup & Discovery**:
   - Integration initializes with TP-Link Deco and simulated mesh backhaul links.
   - Floor and Area Registries populated with "Living Room" and "Bedroom".
   - Deco nodes mapped to respective areas.

2. **Presence & Occupancy State Progression**:
   - **Step 1 (Quiet State)**:
     - `binary_sensor.presence_living_room`: `off`, `occupant_type: "none"`, `unidentified_motion: false`.
     - `binary_sensor.living_room_unidentified_presence`: `off`.
   - **Step 2 (Unidentified Intruder Enters)**:
     - RF disturbance simulated on Living Room backhaul link (disturbance score = 85%).
     - Zero connected Wi-Fi devices in proximity.
     - `binary_sensor.presence_living_room`: `on`, `occupant_type: "unidentified"`, `unidentified_motion: true`.
     - `binary_sensor.living_room_unidentified_presence`: `on`.
   - **Step 3 (Family Member Enters & Connected Phone Corroborates)**:
     - Alice's phone associates to Living Room Deco with strong RSSI (-55 dBm, 2.1m).
     - Cross-checking engine correlates Alice's presence with the RF perturbation.
     - `binary_sensor.living_room_unidentified_presence`: turns `off`.
     - `binary_sensor.presence_living_room`: remains `on`, updates `occupant_type: "verified"`, `verified_occupants: ["Alice"]`, `rf_corroborated_occupant: "Alice"`.
     - Alice's `PersonTracker`: activity transitions to `"Walking / Moving"`, confidence = 1.0, sleep decay timer reset.
   - **Step 4 (Sitting Person Behavior)**:
     - Alice sits down on sofa (`activity: "Stationary / Sitting"`).
     - Alice stands up and walks, generating new RF disturbance.
     - Engine recognizes Alice is in close proximity ($\le 3.5$m), transitions her activity back to `"Walking / Moving"` without triggering `unidentified_presence`.
   - **Step 5 (Adjacent Room Wall-Bleed)**:
     - Bob walks in Bedroom near the party wall shared with Living Room Deco.
     - Grid coordinates verify Bob is physically within 2.8m of the Deco.
     - Wall-bleed suppression attributes disturbance to Bob; no intruder alert triggered in Living Room.
