# Unit Test Plan — Vacuum Spatial Fusion & Dual-Interval Engine

## 1. Test Modules to Add / Update
1. `tests/test_fast_event_push.py`:
   - Tests fast-path event triggering on `device_tracker` state changes.
   - Tests debounce handling.
   - Tests non-blocking execution (no executor calls in fast-path).
2. `tests/test_vacuum_map_parser.py`:
   - Tests parsing Roborock map image into walkable masks and obstacle bounds.
   - Tests micro-zone extraction from furniture clusters.
3. `tests/test_constrained_localization.py`:
   - Tests multi-AP triangulation with fixed Deco anchors.
   - Tests room boundary clamping.
   - Tests micro-zone snapping.
4. `tests/test_config_flow_anchors.py`:
   - Tests Deco anchor placement step in Options Flow.
