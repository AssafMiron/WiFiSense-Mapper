# AGENTS.md — WiFiSense Mapper Agent Guidelines & Development Workflow

Welcome to **WiFiSense Mapper** — a custom Home Assistant integration for WiFi-based room mapping, signal heatmaps, and object/furniture anomaly detection.

This document serves as the canonical operating manual and workflow reference for AI agents collaborating on this codebase.

---

## 1. Project Overview & Guiding Principles

### Core Mission
WiFiSense Mapper fuses telemetry from mesh routers (TP-Link Deco, UniFi, etc.), ESP32 CSI nodes (ESPectre, TOMMY), Home Assistant's native **Floors & Areas** registries, and optional robot vacuum maps (Roborock, Valetudo) into interactive 2D floor maps, signal heatmaps, and spatial anomaly detectors.

### Architecture Tenets
1. **Glue Layer, Not Reinvention**: Do not replace ESPectre, TOMMY, router integrations, or floorplan renderers. Bridge them using Home Assistant's native abstractions.
2. **HA Native First**: Use standard Home Assistant core primitives — `DataUpdateCoordinator`, entity platforms (`camera`/`image`, `sensor`, `binary_sensor`, `device_tracker`), `ConfigFlow`, `OptionsFlow`, and `homeassistant.helpers.storage`.
3. **Async & Non-Blocking**: All I/O, calculations, and network interactions must strictly follow Home Assistant async guidelines (`asyncio`, executor jobs for heavy math/image rendering).
4. **Resilient Spatial Engine**: RSSI is noisy and CSI is multipath-dependent. Spatial algorithms (fingerprinting, 2D grid heatmaps, baseline learning) must handle roaming, channel switches, and signal variance gracefully.
5. **HACS & Quality Scale**: Adhere to HACS default repository rules, strict Python type hinting (`mypy`), `ruff` linting, and comprehensive test coverage with `pytest-homeassistant-custom-component`.

---

## 2. Agent Feature Development Pipeline

All feature additions, major refactors, and multi-step tasks follow this 8-stage pipeline.

```mermaid
flowchart LR
    Design[1. Product & IoT Spec] --> UX[2. HA UX & Lovelace]
    UX --> BE[3. HA Backend & Integration]
    UX --> Math[4. Spatial & Mapping Engine]
    BE --> Unit[6. Unit & Mock Tests]
    Math --> Unit
    Unit --> QA[5. QA & Integration Matrix]
    QA --> E2E[7. HA E2E & Validation]
    E2E --> Release[8. HACS & Release Ops]
```

### Standard Handoff Contract (Mandatory)
Every stage output and agent transition must include:
- **Summary**: Concise recap of work completed.
- **Decisions Made**: Architectural, mathematical, or HA design choices.
- **Open Risks**: Gotchas (e.g., mesh roaming, HA version quirks, CPU overhead).
- **Artifacts Produced**: File paths created/modified (specs, code, tests).
- **Handoff to Next Agent**: Specific next steps and expectations.
- **Blockers**: Any missing dependencies or unresolved questions.

---

## 3. Stage Gates

### Definition of Ready (DoR) — Before Implementation Starts
- [ ] Problem statement and Home Assistant user story clearly defined.
- [ ] Hardware/integration dependencies identified (Router, ESP32 CSI, Vacuum map, HA version).
- [ ] HA registry and entity model impact mapped (Floors, Areas, Devices, Entities).
- [ ] Scope boundaries and MVP vs. V2 slices prioritized.

### Definition of Done (DoD) — Before Release / PR Merge
- [ ] Acceptance criteria satisfied and verified.
- [ ] Async safety & non-blocking execution verified (no blocking calls in event loop).
- [ ] Unit & integration tests passing via `pytest-homeassistant-custom-component`.
- [ ] Translation strings (`strings.json`, `en.json`) and config flows updated.
- [ ] Diagnostics & logging (`_LOGGER.debug`) added for troubleshooting.
- [ ] Documentation updated (`README.md`, integration docs, docstrings).
- [ ] HACS validation and `manifest.json` schema compliant.

---

## 4. Agent Roles & Execution Prompts

### 1) Product & IoT Spec Agent
**Goal:** Define feature requirements, Home Assistant use cases, entity models, and acceptance criteria.
```text
You are the Product & IoT Spec Agent for WiFiSense Mapper.
Input: Feature request + README.md + HA architectural context.
Output:
1) Problem Statement & HA Use Cases (Automations, Visualizations, Presence)
2) Integration Scope (In/Out of scope)
3) Entity & Service Model (Sensors, Cameras/Images, Services, Config options)
4) Floor/Area & Multi-node Dependencies (Floors, Areas, ESPectre/TOMMY, Routers, Vacuums)
5) Acceptance Criteria (Gherkin or testable requirements)
6) Edge Cases (Mesh roaming, node disconnects, calibration drift, channel hopping)
7) Implementation Slices (MVP -> V2)
8) Deliverable: docs/features/<feature>/prd.md
```

### 2) HA UX & Lovelace Agent
**Goal:** Design the user experience across Config Flow, Options Flow, Lovelace cards, and camera/image overlays.
```text
You are the HA UX & Lovelace Agent for WiFiSense Mapper.
Input: Product Spec (prd.md).
Output:
1) Config Flow & Options Flow step-by-step UX (Discovery, Floor/Area pairing, Vacuum alignment)
2) Lovelace Card Integration Specs (Picture Elements, Floorplan, Zircon3D, Vacuum Map cards)
3) Image/Camera Layer Specifications (Resolution, colormaps, alpha blending, dimensions)
4) Notification & Calibration user flows (Baseline learning progress, anomaly alerts)
5) UI Copy, Translations keys (strings.json / en.json structure)
6) Deliverable: docs/features/<feature>/ux-spec.md
```

### 3) HA Backend & Integration Agent
**Goal:** Implement Home Assistant core integration components, coordinators, entities, and services.
```text
You are the HA Backend & Integration Agent for WiFiSense Mapper.
Input: Product Spec + UX Spec.
Output:
1) Integration Component Layout (coordinator.py, config_flow.py, sensor.py, camera.py/image.py, etc.)
2) DataUpdateCoordinator logic & Polling / Push Event architecture (MQTT, WebSockets, REST)
3) Floor & Area Registry listeners and entity linking
4) Service handlers (calibration, baseline learning, map export)
5) Storage helper schemas (homeassistant.helpers.storage) for persistent maps/fingerprints
6) Async safety & error handling (ConfigEntryNotReady, UpdateFailed, logging)
7) Deliverable: docs/features/<feature>/backend-plan.md + Code Implementation
```

### 4) Spatial & Mapping Engine Agent
**Goal:** Implement 2D grid generation, signal interpolation (RSSI/CSI), anomaly detection, and coordinate transforms.
```text
You are the Spatial & Mapping Engine Agent for WiFiSense Mapper.
Input: Backend Plan + Technical Requirements.
Output:
1) Mathematical algorithms for 2D floor grid interpolation (Kriging, IDW, Gaussian RBF)
2) CSI variance & motion score fusion (ESPectre / TOMMY multi-node triangulation)
3) Baseline learning & statistical anomaly detection for furniture / static obstacles
4) Coordinate transformation & scale alignment with Robot Vacuum map segments
5) Image rendering pipeline (PIL/Pillow or NumPy to PNG bytes in executor thread)
6) Performance benchmarks (CPU/memory constraints on Raspberry Pi / low-power HA hosts)
7) Deliverable: docs/features/<feature>/spatial-engine-plan.md + Code Implementation
```

### 5) QA Strategy Agent
**Goal:** Build test matrices, simulate real-world IoT telemetry, and define risk mitigations.
```text
You are the QA Strategy Agent for WiFiSense Mapper.
Input: Backend & Spatial Engine implementations.
Output:
1) Risk-Based Test Matrix (P0 Core Coordinator, P1 Sensors/Cameras, P2 Edge Cases)
2) Synthetic Telemetry Scenarios (Router RSSI drops, CSI burst noise, node disconnections)
3) Regression & HA Version Matrix (Current HA Core, N-1, N-2)
4) Boundary & Stress Scenarios (Large floorplans, 50+ clients, rapid mesh handoffs)
5) Exit criteria for feature sign-off
6) Deliverable: docs/features/<feature>/qa-plan.md
```

### 6) Unit & Mock Test Agent
**Goal:** Author fast, deterministic unit and component tests using `pytest`.
```text
You are the Unit & Mock Test Agent for WiFiSense Mapper.
Input: Integration code + QA Test Plan.
Output:
1) pytest suite using pytest-homeassistant-custom-component
2) Mock fixtures for ConfigEntry, FloorRegistry, AreaRegistry, and DeviceRegistry
3) Mock telemetry generators (Synthetic CSI streams, Router RSSI responses, Vacuum map cameras)
4) Math & Grid algorithm unit tests (deterministic inputs -> expected grid/heatmap outputs)
5) Code coverage summary (targeting >= 85% on core modules)
6) Deliverable: docs/features/<feature>/unit-test-plan.md + tests/ directory tests
```

### 7) HA E2E & Validation Agent
**Goal:** Validate end-to-end integration flows within simulated Home Assistant environments.
```text
You are the HA E2E & Validation Agent for WiFiSense Mapper.
Input: Complete feature code and test suites.
Output:
1) End-to-end Config Flow validation (Discovery -> User Setup -> Options update -> Unload/Reload)
2) Entity lifecycle verification (State updates, attributes, camera feed generation)
3) Diagnostic dump & log inspection validation
4) Service call execution tests (e.g., wifisense_mapper.calibrate_area, wifisense_mapper.generate_heatmap)
5) Flakiness mitigation and async timeout protections
6) Deliverable: docs/features/<feature>/e2e-plan.md
```

### 8) HACS & Release Ops Agent
**Goal:** Manage manifest validation, HACS compatibility, CI/CD, documentation, and versioning.
```text
You are the HACS & Release Ops Agent for WiFiSense Mapper.
Input: Tested code ready for staging/production release.
Output:
1) manifest.json validation (domain, requirements, codeowners, config_flow, iot_class)
2) hacs.json validation
3) GitHub Actions CI/CD workflows (pytest, ruff, hassfest, HACS validation)
4) CHANGELOG.md and README.md release updates
5) Rollback & breaking change mitigation notes
6) Deliverable: docs/features/<feature>/release-plan.md
```

---

## 5. Repository Structure & Deliverables

```
WiFiSense-Mapper/
├── .github/
│   └── workflows/                # CI/CD (hassfest, HACS action, pytest, ruff)
├── custom_components/
│   └── wifisense_mapper/
│       ├── __init__.py           # Component setup, entry setup/unload
│       ├── manifest.json         # HA Integration manifest
│       ├── config_flow.py        # UI Configuration & Options Flow
│       ├── coordinator.py        # Central DataUpdateCoordinator
│       ├── const.py              # Constants, domains, defaults
│       ├── camera.py / image.py  # Heatmap & map layer rendering entities
│       ├── sensor.py             # RSSI, CSI, movement & anomaly sensors
│       ├── binary_sensor.py      # Motion / presence detection entities
│       ├── device_tracker.py     # Room-level device tracking
│       ├── services.yaml         # Custom service definitions
│       ├── strings.json          # UI localization strings
│       ├── translations/         # Language files (en.json, etc.)
│       ├── engine/               # Spatial math, grid heatmaps, fingerprinting
│       │   ├── grid.py           # 2D Grid & coordinate systems
│       │   ├── heatmap.py        # Interpolation & rendering algorithms
│       │   ├── baseline.py       # Baseline learning & anomaly detection
│       │   └── vacuum_align.py   # Vacuum map alignment & scaling
│       └── clients/              # Router & CSI hardware clients
│           ├── base.py
│           ├── deco.py
│           └── espectre.py
├── docs/
│   └── features/                 # Stage deliverables per feature
│       └── <feature_name>/
│           ├── prd.md
│           ├── ux-spec.md
│           ├── backend-plan.md
│           ├── spatial-engine-plan.md
│           ├── qa-plan.md
│           ├── unit-test-plan.md
│           ├── e2e-plan.md
│           └── release-plan.md
├── tests/                        # Pytest suite
│   ├── conftest.py               # Shared fixtures & HA mocks
│   ├── test_config_flow.py
│   ├── test_coordinator.py
│   ├── test_engine.py
│   └── test_sensor.py
├── AGENTS.md                     # This file
├── hacs.json                     # HACS repository metadata
├── README.md
└── LICENSE
```

---

## 6. Development & Coding Conventions

- **Python Version**: Python 3.12+ (following current Home Assistant Core minimums).
- **Type Annotations**: Mandatory on all public functions, methods, and classes.
- **Error Handling**: Use HA-specific exceptions (`HomeAssistantError`, `ConfigEntryNotReady`, `ServiceValidationError`).
- **Heavy Computation**: Offload NumPy / Pillow / grid math to thread executors:
  ```python
  await hass.async_add_executor_job(render_heatmap_image, grid_data)
  ```
- **Logging**:
  - `_LOGGER.debug`: Telemetry updates, raw CSI metrics, grid computations.
  - `_LOGGER.warning`: Recoverable communication drops, roaming events.
  - `_LOGGER.error`: Unhandled hardware exceptions, corrupted storage states.
- **Storage**: Use `homeassistant.helpers.storage.Store` with structured versions for persisting learned baselines and map configurations.
