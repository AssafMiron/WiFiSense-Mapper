"""Constants for the WiFiSense Mapper integration."""

from __future__ import annotations

from typing import Final

# Integration domain & version
DOMAIN: Final = "wifisense_mapper"
VERSION: Final = "0.3.0"

# Platforms to set up
PLATFORMS: Final = [
    "binary_sensor",
    "device_tracker",
    "image",
    "sensor",
]

# ─── Config entry keys ────────────────────────────────────────────────────────

# Router type selection
CONF_ROUTER_TYPE: Final = "router_type"
CONF_ROUTER_HOST: Final = "router_host"
CONF_ROUTER_USERNAME: Final = "router_username"
CONF_ROUTER_PASSWORD: Final = "router_password"

# CSI / ESPectre
CONF_CSI_NODES: Final = "csi_nodes"  # list[str] entity ids

# Vacuum
CONF_VACUUM_ENTITIES: Final = "vacuum_entities"  # list[str] entity ids

# Floor/Area links: dict[node_unique_id → area_id]
CONF_NODE_AREA_LINKS: Final = "node_area_links"

# Person tracking & micro-zone configs
CONF_PERSON_TAGS: Final = (
    "person_tags"  # dict[mac -> dict[person_entity_id, custom_name]]
)
CONF_MICRO_ZONES: Final = (
    "micro_zones"  # list[dict[name, area_id, floor_id, x_m, y_m, radius_m]]
)

# Options
CONF_POLL_INTERVAL: Final = "poll_interval"
CONF_HEATMAP_ENABLED: Final = "heatmap_enabled"
CONF_ANOMALY_THRESHOLD: Final = "anomaly_threshold"
CONF_BASELINE_DAYS: Final = "baseline_days"
CONF_FAST_EVENT_PUSH: Final = "fast_event_push"
CONF_DECO_ANCHORS: Final = (
    "deco_anchors"  # dict[ap_mac/name -> dict[area_id, floor_id, x_pct, y_pct]]
)
CONF_RF_SENSING_ENABLED: Final = "rf_sensing_enabled"
CONF_RF_SENSITIVITY: Final = "rf_sensitivity"  # "low", "medium", "high"
CONF_RF_OFF_DELAY: Final = "rf_off_delay"  # seconds
CONF_ADAPTIVE_POLLING: Final = "adaptive_polling"
CONF_STATIONARY_DEVICES: Final = (
    "stationary_devices"  # list of MACs to treat as stationary
)
CONF_RF_PROXIMITY_THRESHOLD_M: Final = "rf_proximity_threshold_m"
CONF_RF_COINCIDENCE_WINDOW_S: Final = "rf_coincidence_window_sec"
CONF_RF_WALL_BLEED_SUPPRESSION: Final = "rf_wall_bleed_suppression"

# ─── Activity states ──────────────────────────────────────────────────────────

STATE_STATIONARY: Final = "Stationary / Sitting"
STATE_WALKING: Final = "Walking / Moving"
STATE_TRANSITIONING: Final = "Room Transitioning"
STATE_AWAY: Final = "Away"

# ─── Defaults ─────────────────────────────────────────────────────────────────

DEFAULT_POLL_INTERVAL: Final = 60  # seconds (slow spatial loop)
DEFAULT_BACKGROUND_INTERVAL: Final = 60  # seconds
DEFAULT_FAST_EVENT_PUSH: Final = True
DEFAULT_ANOMALY_THRESHOLD: Final = 3.0  # z-score
DEFAULT_BASELINE_DAYS: Final = 7
DEFAULT_GRID_RESOLUTION: Final = 0.5  # meters per cell
DEFAULT_HEATMAP_ENABLED: Final = True
DEFAULT_RF_SENSING_ENABLED: Final = True
DEFAULT_RF_SENSITIVITY: Final = "medium"
DEFAULT_RF_OFF_DELAY: Final = 30  # seconds
DEFAULT_ADAPTIVE_POLLING: Final = True
DEFAULT_RF_PROXIMITY_THRESHOLD_M: Final = 3.5  # meters
DEFAULT_RF_COINCIDENCE_WINDOW_S: Final = 15.0  # seconds
DEFAULT_RF_WALL_BLEED_SUPPRESSION: Final = True

# ─── Router types ─────────────────────────────────────────────────────────────

ROUTER_TYPE_DECO: Final = "tplink_deco"
ROUTER_TYPE_UNIFI: Final = "unifi"
ROUTER_TYPE_NONE: Final = "none"

ROUTER_TYPES: Final = [ROUTER_TYPE_DECO, ROUTER_TYPE_UNIFI, ROUTER_TYPE_NONE]

# ─── CSI entity name patterns (regex) ─────────────────────────────────────────
# Used to identify ESPectre / TOMMY entities via entity_registry scan.

CSI_MOTION_SCORE_PATTERNS: Final = [
    r"motion_score",
    r"movement_score",
    r"csi_score",
]
CSI_MOTION_DETECTED_PATTERNS: Final = [
    r"motion_detected",
    r"presence_detected",
    r"presence",
]
CSI_MQTT_TOPIC_PATTERNS: Final = [
    r"espectre",
    r"tommy",
    r"espwifi",
    r"wifisense",
]

# ─── Vacuum map platforms ─────────────────────────────────────────────────────

VACUUM_PLATFORMS: Final = [
    "roborock",
    "mqtt_vacuum_camera",
    "valetudo",
    "valetudo_vacuum_camera",
    "dreame",
    "dreame_vacuum",
    "xiaomi_miio",
    "roomba",
    "ecovacs",
    "neato",
    "vacuum",
]

# ─── Storage keys ─────────────────────────────────────────────────────────────

STORAGE_KEY_GRIDS: Final = f"{DOMAIN}.grids"
STORAGE_KEY_BASELINES: Final = f"{DOMAIN}.baselines"
STORAGE_KEY_CALIBRATION: Final = f"{DOMAIN}.calibration"
STORAGE_KEY_CONFIG: Final = f"{DOMAIN}.config"
STORAGE_VERSION: Final = 1

# ─── Heatmap layers ───────────────────────────────────────────────────────────

LAYER_SIGNAL: Final = "signal"
LAYER_VARIANCE: Final = "variance"
LAYER_MOTION: Final = "motion"
LAYER_ANOMALY: Final = "anomaly"
LAYER_COVERAGE: Final = "coverage"
LAYER_RF_LINKS: Final = "rf_links"

HEATMAP_LAYERS: Final = [
    LAYER_SIGNAL,
    LAYER_VARIANCE,
    LAYER_MOTION,
    LAYER_ANOMALY,
    LAYER_COVERAGE,
    LAYER_RF_LINKS,
]

# ─── Service names ────────────────────────────────────────────────────────────

SERVICE_START_SCAN: Final = "start_scan"
SERVICE_STOP_SCAN: Final = "stop_scan"
SERVICE_GENERATE_HEATMAP: Final = "generate_heatmap"
SERVICE_LEARN_BASELINE: Final = "learn_baseline"
SERVICE_CALIBRATE_VACUUM: Final = "calibrate_vacuum_map"
SERVICE_EXPORT_MAP: Final = "export_map"
SERVICE_LINK_NODE_AREA: Final = "link_node_to_area"

# ─── Entity/device naming ─────────────────────────────────────────────────────

MANUFACTURER: Final = "WiFiSense Mapper"
MODEL: Final = "Virtual WiFi Sensor Hub"
