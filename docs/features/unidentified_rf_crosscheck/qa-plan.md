# QA Plan — Unidentified RF Cross-Checking & Presence Triage

## 1. Test Matrix

| ID | Scenario | Inputs | Expected Output | Priority |
| :--- | :--- | :--- | :--- | :--- |
| TC-01 | Intruder in empty house | RF motion in Living Room; 0 connected persons in house | `unidentified_presence` is ON; `presence.occupant_type == "unidentified"`; `unidentified_motion == True` | P0 |
| TC-02 | Known person walking near router | RF motion in Bedroom; Alice connected to Bedroom AP, RSSI -55dBm (dist 2.1m) | `unidentified_presence` is OFF; `presence.occupant_type == "verified"`; Alice activity -> `walking` | P0 |
| TC-03 | Sitting person stands up | Alice in Living Room, activity `stationary`, sitting for 5 min; RF motion triggers | Alice activity transitions to `walking`; no intruder alert triggered | P0 |
| TC-04 | Asynchronous coincidence window | RF motion triggers at t=100s; Alice's phone polls at t=108s with strong RSSI | Alert retroactively suppressed / matched within 15s window | P1 |
| TC-05 | Wall-bleed cross-room suppression | RF motion on Hallway Deco; Bob is in Study adjacent to Hallway, dist 2.8m from Hallway Deco | Wall-bleed detected; no intruder alert | P1 |
| TC-06 | Mixed occupancy | Alice in Living Room far away (12m); RF motion at entrance link with no person nearby | `presence.occupant_type == "mixed"`; `unidentified_presence` is ON | P1 |
| TC-07 | Quiet house (no RF motion, no persons) | All links quiet, 0 persons | Both presence and unidentified presence are OFF; `occupant_type == "none"` | P0 |
