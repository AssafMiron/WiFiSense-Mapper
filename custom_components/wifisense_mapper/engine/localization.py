"""WiFiSense Mapper — Indoor Person Localization & Activity Engine.

Provides multi-AP trilateration, 2D Kalman filtering, floor transition
feasibility checking, micro-zone (furniture) matching, and physical activity
classification (Stationary, Walking, Transitioning, Away).
"""

from __future__ import annotations

import logging
import math
import time
from dataclasses import dataclass, field
from typing import Any

from ..const import (
    STATE_AWAY,
    STATE_STATIONARY,
    STATE_TRANSITIONING,
    STATE_WALKING,
)

_LOGGER = logging.getLogger(__name__)

# Minimum time in seconds to physically traverse between floors
MIN_FLOOR_TRANSITION_TIME_S = 15.0

# Velocity threshold (m/s) to classify walking vs stationary
WALKING_VELOCITY_THRESHOLD = 0.25

# CSI motion threshold to confirm local physical movement
CSI_MOTION_MOVEMENT_THRESHOLD = 15.0

# Maximum sample age before confidence begins decaying
STANDBY_DECAY_START_S = 45.0
STANDBY_MAX_AGE_S = 600.0  # 10 minutes


@dataclass
class MicroZone:
    """A configured furniture or sub-room micro zone."""

    name: str
    area_id: str
    floor_id: str
    x_m: float
    y_m: float
    radius_m: float = 1.5

    def is_inside(self, x_m: float, y_m: float, floor_id: str) -> bool:
        """Return True if the given coordinates fall within this micro-zone."""
        if self.floor_id != floor_id:
            return False
        dist = math.sqrt((x_m - self.x_m) ** 2 + (y_m - self.y_m) ** 2)
        return dist <= self.radius_m

    def to_dict(self) -> dict[str, Any]:
        """Convert micro-zone to dictionary."""
        return {
            "name": self.name,
            "area_id": self.area_id,
            "floor_id": self.floor_id,
            "x_m": self.x_m,
            "y_m": self.y_m,
            "radius_m": self.radius_m,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> MicroZone:
        """Create MicroZone from dictionary."""
        return cls(
            name=data.get("name", "Zone"),
            area_id=data.get("area_id", ""),
            floor_id=data.get("floor_id", "default"),
            x_m=float(data.get("x_m", 0.0)),
            y_m=float(data.get("y_m", 0.0)),
            radius_m=float(data.get("radius_m", 1.5)),
        )


class KalmanFilter2D:
    """2D Constant-Velocity Kalman Filter for position and velocity smoothing."""

    def __init__(
        self,
        x: float = 0.0,
        y: float = 0.0,
        process_noise: float = 0.2,
        measurement_noise: float = 1.5,
    ) -> None:
        # State: [x, y, vx, vy]
        self.state = [x, y, 0.0, 0.0]
        # Covariance matrix (4x4 diagonal initialized)
        self.cov = [
            [10.0, 0.0, 0.0, 0.0],
            [0.0, 10.0, 0.0, 0.0],
            [0.0, 0.0, 1.0, 0.0],
            [0.0, 0.0, 0.0, 1.0],
        ]
        self.q = process_noise
        self.r = measurement_noise
        self.last_ts: float = time.time()

    def update(self, z_x: float, z_y: float, ts: float | None = None) -> tuple[float, float, float]:
        """Predict and update filter with observation (z_x, z_y).

        Returns (filtered_x, filtered_y, estimated_speed).
        """
        now = ts if ts is not None else time.time()
        dt = max(0.1, min(10.0, now - self.last_ts))
        self.last_ts = now

        # 1. Prediction step: x = F * x
        self.state[0] += self.state[2] * dt
        self.state[1] += self.state[3] * dt

        # Update covariance with process noise: P = F * P * F^T + Q
        self.cov[0][0] += dt * (self.cov[2][0] + self.cov[0][2] + dt * self.cov[2][2]) + self.q * dt
        self.cov[1][1] += dt * (self.cov[3][1] + self.cov[1][3] + dt * self.cov[3][3]) + self.q * dt
        self.cov[2][2] += self.q * dt
        self.cov[3][3] += self.q * dt

        # 2. Measurement update: K = P * H^T * (H * P * H^T + R)^-1
        # H is [1 0 0 0; 0 1 0 0]
        s_x = self.cov[0][0] + self.r
        s_y = self.cov[1][1] + self.r

        k_x0 = self.cov[0][0] / s_x
        k_x2 = self.cov[2][0] / s_x
        k_y1 = self.cov[1][1] / s_y
        k_y3 = self.cov[3][1] / s_y

        # Innovation: y = z - H * x
        y_x = z_x - self.state[0]
        y_y = z_y - self.state[1]

        # Update state: x = x + K * y
        self.state[0] += k_x0 * y_x
        self.state[1] += k_y1 * y_y
        self.state[2] += k_x2 * y_x
        self.state[3] += k_y3 * y_y

        # Update covariance: P = (I - K * H) * P
        self.cov[0][0] *= 1.0 - k_x0
        self.cov[1][1] *= 1.0 - k_y1
        self.cov[2][2] -= k_x2 * self.cov[0][2]
        self.cov[3][3] -= k_y3 * self.cov[1][3]

        speed = math.sqrt(self.state[2] ** 2 + self.state[3] ** 2)
        return self.state[0], self.state[1], speed


@dataclass
class PersonTrackingState:
    """Consolidated indoor tracking status for a person."""

    mac: str
    person_entity_id: str | None = None
    person_name: str = "Unknown Person"
    floor_id: str = "default"
    floor_name: str = "Ground Floor"
    area_id: str | None = None
    area_name: str = "Unknown Room"
    micro_zone: str | None = None
    activity: str = STATE_STATIONARY
    x_m: float = 0.0
    y_m: float = 0.0
    x_pct: float = 50.0  # 0 to 100% for CSS / Lovelace
    y_pct: float = 50.0  # 0 to 100% for CSS / Lovelace
    confidence: float = 1.0
    dwell_time_s: float = 0.0
    last_seen_ts: float = field(default_factory=time.time)
    last_area_name: str | None = None
    ap_mac: str | None = None
    rssi: int | None = None
    speed_mps: float = 0.0

    def to_dict(self) -> dict[str, Any]:
        """Convert state to dict for entity attributes."""
        return {
            "mac": self.mac,
            "person_entity_id": self.person_entity_id,
            "person_name": self.person_name,
            "floor_id": self.floor_id,
            "floor_name": self.floor_name,
            "area_id": self.area_id,
            "area_name": self.area_name,
            "micro_zone": self.micro_zone,
            "activity": self.activity,
            "x_m": round(self.x_m, 2),
            "y_m": round(self.y_m, 2),
            "x_pct": round(self.x_pct, 1),
            "y_pct": round(self.y_pct, 1),
            "confidence": round(self.confidence, 2),
            "dwell_time_s": int(self.dwell_time_s),
            "last_seen_ts": self.last_seen_ts,
            "last_area_name": self.last_area_name,
            "ap_mac": self.ap_mac,
            "rssi": self.rssi,
            "speed_mps": round(self.speed_mps, 2),
        }


class PersonTracker:
    """Tracks position and activity for an individual person tag."""

    def __init__(
        self,
        mac: str,
        person_entity_id: str | None = None,
        person_name: str | None = None,
    ) -> None:
        self.mac = mac.lower()
        self.person_entity_id = person_entity_id
        self.person_name = person_name or f"Person {mac[-5:]}"
        self.filter = KalmanFilter2D()

        self.current_floor: str = "default"
        self.current_area_id: str | None = None
        self.current_area_name: str | None = None
        self.last_area_name: str | None = None
        self.area_enter_ts: float = time.time()
        self.last_floor_change_ts: float = 0.0

        self.latest_state = PersonTrackingState(
            mac=self.mac,
            person_entity_id=self.person_entity_id,
            person_name=self.person_name,
        )

    def update(
        self,
        *,
        ap_mac: str | None,
        rssi: int | None,
        floor_id: str,
        floor_name: str,
        area_id: str | None,
        area_name: str,
        ap_pos_m: tuple[float, float] | None,
        grid_width_m: float,
        grid_height_m: float,
        csi_motion_score: float = 0.0,
        micro_zones: list[MicroZone] | None = None,
        now_ts: float | None = None,
    ) -> PersonTrackingState:
        """Update tracker with fresh telemetry."""
        now = now_ts if now_ts is not None else time.time()

        if ap_mac is None:
            # Device not reporting / away or asleep
            if area_name and area_name != "Unknown Room" and self.latest_state.area_name == "Unknown Room":
                self.latest_state.area_name = area_name
                self.latest_state.area_id = area_id
            if floor_id:
                self.latest_state.floor_id = floor_id
                self.latest_state.floor_name = floor_name

            age = now - self.latest_state.last_seen_ts
            if age > STANDBY_MAX_AGE_S:
                self.latest_state.activity = STATE_AWAY
                self.latest_state.confidence = 0.0
            else:
                # Decaying confidence, hold last location
                decay_factor = max(
                    0.0,
                    1.0
                    - (age - STANDBY_DECAY_START_S)
                    / (STANDBY_MAX_AGE_S - STANDBY_DECAY_START_S),
                )
                # Boost if CSI motion is active in current area
                if csi_motion_score > CSI_MOTION_MOVEMENT_THRESHOLD:
                    decay_factor = min(1.0, decay_factor + 0.3)
                self.latest_state.confidence = round(max(0.1, decay_factor), 2)
                self.latest_state.activity = STATE_STATIONARY
            return self.latest_state

        # Update last seen
        self.latest_state.last_seen_ts = now
        self.latest_state.rssi = rssi
        self.latest_state.ap_mac = ap_mac

        # 1. Floor Transition Feasibility Guard
        if floor_id != self.current_floor:
            elapsed_since_last_floor = now - self.last_floor_change_ts
            if (
                self.last_floor_change_ts > 0
                and elapsed_since_last_floor < MIN_FLOOR_TRANSITION_TIME_S
            ):
                # Feasibility violation (too fast vertical hop without transition time)
                floor_id = self.current_floor
            else:
                self.current_floor = floor_id
                self.last_floor_change_ts = now

        self.latest_state.floor_id = floor_id
        self.latest_state.floor_name = floor_name

        # 2. Raw Position Estimation (AP Position + Path Loss)
        if ap_pos_m is not None:
            raw_x, raw_y = ap_pos_m
            if rssi is not None:
                # Simple path loss offset approximation: ~1m per 5 dB drop below -40 dBm
                dist_est = max(0.0, (-40 - rssi) / 10.0)
                # Add minor deterministic angle dispersion based on MAC
                angle = (int(self.mac.replace(":", "")[-2:], 16) % 360) * (math.pi / 180.0)
                target_x = max(0.0, min(grid_width_m, raw_x + dist_est * math.cos(angle)))
                target_y = max(0.0, min(grid_height_m, raw_y + dist_est * math.sin(angle)))
            else:
                # Coarse signal: center around the AP with slight dispersion
                angle = (int(self.mac.replace(":", "")[-2:], 16) % 360) * (math.pi / 180.0)
                target_x = max(0.0, min(grid_width_m, raw_x + 0.5 * math.cos(angle)))
                target_y = max(0.0, min(grid_height_m, raw_y + 0.5 * math.sin(angle)))
        else:
            target_x = grid_width_m / 2.0
            target_y = grid_height_m / 2.0

        # 3. Kalman Filter Smoothing
        smooth_x, smooth_y, speed = self.filter.update(target_x, target_y, ts=now)
        smooth_x = max(0.0, min(grid_width_m, smooth_x))
        smooth_y = max(0.0, min(grid_height_m, smooth_y))

        self.latest_state.x_m = smooth_x
        self.latest_state.y_m = smooth_y
        self.latest_state.speed_mps = speed
        self.latest_state.x_pct = (smooth_x / max(1.0, grid_width_m)) * 100.0
        self.latest_state.y_pct = (smooth_y / max(1.0, grid_height_m)) * 100.0

        # 4. Area & Dwell Time Calculation
        if self.current_area_name is None:
            self.current_area_name = area_name
            self.current_area_id = area_id
            self.area_enter_ts = now
            self.latest_state.dwell_time_s = 0.0
        elif area_name != self.current_area_name:
            self.last_area_name = self.current_area_name
            self.current_area_name = area_name
            self.current_area_id = area_id
            self.area_enter_ts = now
            self.latest_state.dwell_time_s = 0.0
        else:
            self.latest_state.dwell_time_s = now - self.area_enter_ts

        self.latest_state.area_id = self.current_area_id
        self.latest_state.area_name = self.current_area_name or "Home"
        self.latest_state.last_area_name = self.last_area_name

        # 5. Micro-Zone (Furniture) Matching
        matched_zone: str | None = None
        if micro_zones:
            for mz in micro_zones:
                if mz.is_inside(smooth_x, smooth_y, floor_id):
                    matched_zone = mz.name
                    break
        self.latest_state.micro_zone = matched_zone

        # 6. Physical Activity Classification
        if self.latest_state.dwell_time_s < 10.0 and self.last_area_name and self.last_area_name != self.current_area_name:
            self.latest_state.activity = STATE_TRANSITIONING
        elif speed > WALKING_VELOCITY_THRESHOLD or csi_motion_score > CSI_MOTION_MOVEMENT_THRESHOLD:
            self.latest_state.activity = STATE_WALKING
        else:
            self.latest_state.activity = STATE_STATIONARY

        self.latest_state.confidence = 1.0 if ap_pos_m is not None else 0.7
        return self.latest_state


class PersonLocalizationEngine:
    """Central engine managing all person trackers and micro-zones."""

    def __init__(self) -> None:
        self.trackers: dict[str, PersonTracker] = {}
        self.micro_zones: list[MicroZone] = []

    def configure_person(
        self,
        mac: str,
        person_entity_id: str | None = None,
        person_name: str | None = None,
    ) -> PersonTracker:
        """Register or update a person tracker for a given MAC."""
        mac_lower = mac.lower()
        if mac_lower not in self.trackers:
            self.trackers[mac_lower] = PersonTracker(
                mac=mac_lower,
                person_entity_id=person_entity_id,
                person_name=person_name,
            )
        else:
            tracker = self.trackers[mac_lower]
            if person_entity_id:
                tracker.person_entity_id = person_entity_id
            if person_name:
                tracker.person_name = person_name
        return self.trackers[mac_lower]

    def set_micro_zones(self, zones: list[dict[str, Any]]) -> None:
        """Load micro-zones from configuration."""
        self.micro_zones = [MicroZone.from_dict(z) for z in zones]

    def get_tracker(self, mac: str) -> PersonTracker | None:
        """Get tracker for a MAC."""
        return self.trackers.get(mac.lower())

    def all_states(self) -> dict[str, PersonTrackingState]:
        """Return dict of mac -> PersonTrackingState."""
        return {mac: tracker.latest_state for mac, tracker in self.trackers.items()}
