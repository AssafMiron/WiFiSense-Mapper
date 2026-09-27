# QA Plan — Vacuum Spatial Fusion & Dual-Interval Engine

## 1. Risk-Based Test Matrix

| Area | Priority | Scenarios |
| :--- | :--- | :--- |
| **Fast Event Push** | P0 | Device roams from Deco 1 to Deco 2; state change event triggers < 500ms; entity state updates without running executor jobs. |
| **Debounce & Throttling**| P0 | High-frequency RSSI jitter does not flood HA event loop (debounced at 250ms). |
| **Boundary Clamping** | P1 | Position calculation near room edge is strictly clamped within Roborock room polygon. |
| **Micro-Zone Snapping** | P1 | Stationary person within 1.5m of sofa snaps to 'Living Room Sofa'; walking away clears micro-zone. |
| **Wall Raycasting** | P2 | Raycaster properly calculates wall crossing attenuation without lagging CPU. |
| **Graceful Degradation**| P1 | Vacuum entity unavailable or map unparsed: falls back gracefully to standard grid without errors. |
