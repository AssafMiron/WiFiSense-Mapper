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


def calculate_distance_from_rssi(
    rssi: float | None,
    band: str | None = None,
    tx_power: float | None = None,
    path_loss_n: float = 2.4,
    wall_crossings: int = 0,
    wall_attenuation_db: float = 4.0,
) -> float | None:
    """Calculate distance in meters using IEEE/ITU-R log-distance path loss model with obstacle compensation.

    d = 10 ^ ((tx_power - (rssi + wall_loss)) / (10 * n))
    """
    if rssi is None or float(rssi) >= 0:
        return None
    ref_power = tx_power
    if ref_power is None:
        if band and "2" in str(band):
            ref_power = -40.0
            path_loss_n = 2.2
        else:
            ref_power = -42.0
            path_loss_n = 2.4
    try:
        # Effective RSSI compensated for wall crossings
        effective_rssi = float(rssi) + min(16.0, wall_crossings * wall_attenuation_db)
        val = 10.0 ** ((ref_power - effective_rssi) / (10.0 * path_loss_n))
        return round(max(0.1, min(val, 50.0)), 1)
    except (ValueError, TypeError, OverflowError):
        return None


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

    def update(
        self, z_x: float, z_y: float, ts: float | None = None
    ) -> tuple[float, float, float]:
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
        self.cov[0][0] += (
            dt * (self.cov[2][0] + self.cov[0][2] + dt * self.cov[2][2]) + self.q * dt
        )
        self.cov[1][1] += (
            dt * (self.cov[3][1] + self.cov[1][3] + dt * self.cov[3][3]) + self.q * dt
        )
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
    connected_ap_name: str | None = None
    band: str | None = None
    rssi: int | None = None
    distance_m: float | None = None
    distances_to_aps: dict[str, float] = field(default_factory=dict)
    speed_mps: float = 0.0

    @property
    def current_area_id(self) -> str | None:
        """Alias for area_id."""
        return self.area_id

    @property
    def is_home(self) -> bool:
        """Return True if person is home (not away)."""
        return self.activity != STATE_AWAY

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
            "connected_ap_name": self.connected_ap_name,
            "band": self.band,
            "rssi": self.rssi,
            "distance_m": self.distance_m,
            "distances_to_aps": self.distances_to_aps,
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
        self.smoothed_distance: float | None = None

        self.latest_state = PersonTrackingState(
            mac=self.mac,
            person_entity_id=self.person_entity_id,
            person_name=self.person_name,
        )

    @property
    def state(self) -> PersonTrackingState:
        """Return the latest tracking state."""
        return self.latest_state

    @property
    def is_home(self) -> bool:
        """Return True if the tracked person is home (not away)."""
        return self.latest_state.is_home

    def update(
        self,
        *,
        ap_mac: str | None,
        ap_name: str | None = None,
        band: str | None = None,
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
        is_connected: bool = True,
        ap_distances: dict[str, float] | None = None,
        all_ap_positions: dict[str, tuple[float, float]] | None = None,
        vacuum_features: Any | None = None,
        now_ts: float | None = None,
    ) -> PersonTrackingState:
        """Update tracker with fresh telemetry."""
        now = now_ts if now_ts is not None else time.time()

        # Check if device is completely offline/disconnected or in standby
        if (not is_connected) or (ap_mac is None and rssi is None):
            # Device not reporting / away or asleep
            if (
                area_name
                and area_name != "Unknown Room"
                and self.latest_state.area_name == "Unknown Room"
            ):
                self.latest_state.area_name = area_name
                self.latest_state.area_id = area_id
            if floor_id:
                self.latest_state.floor_id = floor_id
                self.latest_state.floor_name = floor_name

            age = now - self.latest_state.last_seen_ts
            if age > STANDBY_MAX_AGE_S:
                self.latest_state.activity = STATE_AWAY
                self.latest_state.confidence = 0.0
                self.latest_state.distance_m = None
            else:
                # Decaying confidence, hold last location
                decay_factor = max(
                    0.0,
                    1.0
                    - (age - STANDBY_DECAY_START_S)
                    / (STANDBY_MAX_AGE_S - STANDBY_DECAY_START_S),
                )
                if csi_motion_score > CSI_MOTION_MOVEMENT_THRESHOLD:
                    decay_factor = min(1.0, decay_factor + 0.3)
                self.latest_state.confidence = round(max(0.1, decay_factor), 2)
                self.latest_state.activity = STATE_STATIONARY
            return self.latest_state

        # Update last seen and signal telemetry
        self.latest_state.last_seen_ts = now
        self.latest_state.rssi = rssi
        self.latest_state.ap_mac = ap_mac
        self.latest_state.connected_ap_name = ap_name
        self.latest_state.band = band

        # Calculate real physical distance from connected Deco hub using path loss model
        wall_crossings = 0
        if (
            vacuum_features
            and ap_pos_m
            and hasattr(vacuum_features, "count_wall_crossings")
        ):
            # Estimate wall crossings from AP to previous smoothed location
            wall_crossings = vacuum_features.count_wall_crossings(
                ap_pos_m[0], ap_pos_m[1], self.latest_state.x_m, self.latest_state.y_m
            )

        if rssi is not None:
            dist = calculate_distance_from_rssi(
                rssi, band=band, wall_crossings=wall_crossings
            )
            if dist is not None:
                if self.smoothed_distance is None:
                    self.smoothed_distance = dist
                else:
                    self.smoothed_distance = round(
                        0.35 * dist + 0.65 * self.smoothed_distance, 1
                    )
                self.latest_state.distance_m = self.smoothed_distance
        if ap_distances:
            self.latest_state.distances_to_aps = dict(ap_distances)

        # 1. Floor Transition Feasibility Guard
        if floor_id != self.current_floor:
            elapsed_since_last_floor = now - self.last_floor_change_ts
            if (
                self.last_floor_change_ts > 0
                and elapsed_since_last_floor < MIN_FLOOR_TRANSITION_TIME_S
            ):
                floor_id = self.current_floor
            else:
                self.current_floor = floor_id
                self.last_floor_change_ts = now

        self.latest_state.floor_id = floor_id
        self.latest_state.floor_name = floor_name

        # 2. Raw Position Estimation via Fixed Deco Anchors & Triangulation
        target_x: float
        target_y: float

        if ap_pos_m is not None:
            raw_x, raw_y = ap_pos_m
            dist_est = (
                self.smoothed_distance
                if self.smoothed_distance is not None
                else max(0.5, (-40 - (rssi or -60)) / 10.0)
            )

            # Check if we have multiple AP positions and distance estimates for multi-AP triangulation
            triangulated_vector: tuple[float, float] | None = None
            if all_ap_positions and ap_distances and len(all_ap_positions) > 1:
                sum_dx = 0.0
                sum_dy = 0.0
                total_weight = 0.0

                for other_name, other_pos in all_ap_positions.items():
                    if other_name == ap_name or other_pos == ap_pos_m:
                        continue
                    ap_sep = math.hypot(other_pos[0] - raw_x, other_pos[1] - raw_y)
                    if ap_sep < 0.5:
                        continue

                    unit_x = (other_pos[0] - raw_x) / ap_sep
                    unit_y = (other_pos[1] - raw_y) / ap_sep

                    other_d = ap_distances.get(other_name)
                    if other_d is not None and other_d > 0:
                        proj_d = max(
                            0.0,
                            min(
                                dist_est,
                                (ap_sep**2 + dist_est**2 - other_d**2) / (2.0 * ap_sep),
                            ),
                        )
                        weight = 1.0 / max(0.5, other_d)
                    else:
                        proj_d = min(dist_est, ap_sep * 0.4)
                        weight = 0.5

                    sum_dx += unit_x * proj_d * weight
                    sum_dy += unit_y * proj_d * weight
                    total_weight += weight

                if total_weight > 0:
                    triangulated_vector = (sum_dx / total_weight, sum_dy / total_weight)

            if triangulated_vector is not None:
                target_x = raw_x + triangulated_vector[0]
                target_y = raw_y + triangulated_vector[1]
            else:
                # Direction towards room centroid or nearest micro-zone attractor
                room = (
                    vacuum_features.get_room_for_area(area_id)
                    if (
                        vacuum_features
                        and hasattr(vacuum_features, "get_room_for_area")
                    )
                    else None
                )
                if room is not None:
                    vec_x = room.centroid_x_m - raw_x
                    vec_y = room.centroid_y_m - raw_y
                    vec_len = math.hypot(vec_x, vec_y)
                    if vec_len > 0.3:
                        scale = min(dist_est, vec_len)
                        target_x = raw_x + (vec_x / vec_len) * scale
                        target_y = raw_y + (vec_y / vec_len) * scale
                    else:
                        target_x = room.centroid_x_m
                        target_y = room.centroid_y_m
                elif micro_zones:
                    best_mz = None
                    best_diff = float("inf")
                    for mz in micro_zones:
                        if mz.area_id == area_id or not mz.area_id:
                            mz_d = math.hypot(mz.x_m - raw_x, mz.y_m - raw_y)
                            diff = abs(mz_d - dist_est)
                            if diff < best_diff:
                                best_diff = diff
                                best_mz = mz
                    if best_mz:
                        vec_x = best_mz.x_m - raw_x
                        vec_y = best_mz.y_m - raw_y
                        vec_len = math.hypot(vec_x, vec_y)
                        scale = min(dist_est, vec_len) if vec_len > 0 else 0
                        target_x = raw_x + (vec_x / max(0.1, vec_len)) * scale
                        target_y = raw_y + (vec_y / max(0.1, vec_len)) * scale
                    else:
                        center_x = grid_width_m / 2.0
                        center_y = grid_height_m / 2.0
                        vec_x = center_x - raw_x
                        vec_y = center_y - raw_y
                        vec_len = max(0.1, math.hypot(vec_x, vec_y))
                        target_x = raw_x + (vec_x / vec_len) * min(dist_est, vec_len)
                        target_y = raw_y + (vec_y / vec_len) * min(dist_est, vec_len)
                else:
                    center_x = grid_width_m / 2.0
                    center_y = grid_height_m / 2.0
                    vec_x = center_x - raw_x
                    vec_y = center_y - raw_y
                    vec_len = max(0.1, math.hypot(vec_x, vec_y))
                    target_x = raw_x + (vec_x / vec_len) * min(dist_est, vec_len)
                    target_y = raw_y + (vec_y / vec_len) * min(dist_est, vec_len)
        else:
            room = (
                vacuum_features.get_room_for_area(area_id)
                if (vacuum_features and hasattr(vacuum_features, "get_room_for_area"))
                else None
            )
            if room is not None:
                target_x = room.centroid_x_m
                target_y = room.centroid_y_m
            else:
                target_x = grid_width_m / 2.0
                target_y = grid_height_m / 2.0

        # Physical Walkable Boundary Clamping
        if vacuum_features and hasattr(vacuum_features, "clamp_to_area"):
            target_x, target_y = vacuum_features.clamp_to_area(
                target_x, target_y, area_id
            )
        else:
            target_x = max(0.0, min(grid_width_m, target_x))
            target_y = max(0.0, min(grid_height_m, target_y))

        # 3. Kalman Filter Smoothing
        prev_x = self.latest_state.x_m
        prev_y = self.latest_state.y_m
        smooth_x, smooth_y, speed = self.filter.update(target_x, target_y, ts=now)
        smooth_x = max(0.0, min(grid_width_m, smooth_x))
        smooth_y = max(0.0, min(grid_height_m, smooth_y))

        dt = max(0.1, min(60.0, now - self.latest_state.last_seen_ts))
        if prev_x > 0 and prev_y > 0 and dt > 0:
            instant_speed = math.hypot(smooth_x - prev_x, smooth_y - prev_y) / dt
            speed = max(speed, instant_speed)

        self.latest_state.x_m = smooth_x
        self.latest_state.y_m = smooth_y
        self.latest_state.speed_mps = speed
        self.latest_state.x_pct = (smooth_x / max(1.0, grid_width_m)) * 100.0
        self.latest_state.y_pct = (smooth_y / max(1.0, grid_height_m)) * 100.0

        # 4. Area & Dwell Time Calculation
        effective_area = (
            area_name
            if (area_name and area_name != "Unknown Room")
            else (self.current_area_name or "Home")
        )
        if self.current_area_name is None:
            self.current_area_name = effective_area
            self.current_area_id = area_id
            self.area_enter_ts = now
            self.latest_state.dwell_time_s = 0.0
        elif effective_area != self.current_area_name:
            self.last_area_name = self.current_area_name
            self.current_area_name = effective_area
            self.current_area_id = area_id
            self.area_enter_ts = now
            self.latest_state.dwell_time_s = 0.0
        else:
            self.latest_state.dwell_time_s = now - self.area_enter_ts

        self.latest_state.area_id = self.current_area_id
        self.latest_state.area_name = self.current_area_name or "Home"
        self.latest_state.last_area_name = self.last_area_name

        # 5. Micro-Zone (Furniture) Matching & Snapping
        matched_zone: str | None = None
        all_candidate_zones: list[MicroZone] = list(micro_zones) if micro_zones else []
        if vacuum_features and getattr(vacuum_features, "furniture", None):
            for f in vacuum_features.furniture:
                all_candidate_zones.append(MicroZone.from_dict(f.to_micro_zone_dict()))

        if all_candidate_zones:
            for mz in all_candidate_zones:
                if mz.is_inside(smooth_x, smooth_y, floor_id):
                    matched_zone = mz.name
                    # If stationary and very close, snap coordinates to furniture center for stable positioning
                    if speed < WALKING_VELOCITY_THRESHOLD and math.hypot(
                        smooth_x - mz.x_m, smooth_y - mz.y_m
                    ) <= (mz.radius_m * 0.7):
                        smooth_x = round(0.7 * mz.x_m + 0.3 * smooth_x, 2)
                        smooth_y = round(0.7 * mz.y_m + 0.3 * smooth_y, 2)
                        self.latest_state.x_m = smooth_x
                        self.latest_state.y_m = smooth_y
                        self.latest_state.x_pct = (
                            smooth_x / max(1.0, grid_width_m)
                        ) * 100.0
                        self.latest_state.y_pct = (
                            smooth_y / max(1.0, grid_height_m)
                        ) * 100.0
                    break

        self.latest_state.micro_zone = matched_zone

        # 6. Physical Activity Classification
        if (
            csi_motion_score < 5.0
            and self.latest_state.dwell_time_s >= 10.0
            and (
                prev_x == 0.0 or math.hypot(smooth_x - prev_x, smooth_y - prev_y) < 1.0
            )
        ):
            self.latest_state.activity = STATE_STATIONARY
            self.filter.state[2] = 0.0
            self.filter.state[3] = 0.0
        elif (
            self.latest_state.dwell_time_s < 10.0
            and self.last_area_name
            and self.last_area_name != self.current_area_name
        ):
            self.latest_state.activity = STATE_TRANSITIONING
        elif (
            speed > WALKING_VELOCITY_THRESHOLD
            or csi_motion_score > CSI_MOTION_MOVEMENT_THRESHOLD
        ):
            self.latest_state.activity = STATE_WALKING
        else:
            self.latest_state.activity = STATE_STATIONARY

        self.latest_state.confidence = 1.0 if ap_pos_m is not None else 0.8
        return self.latest_state


class PersonLocalizationEngine:
    """Central engine managing all person trackers and micro-zones."""

    def __init__(self) -> None:
        self.trackers: dict[str, PersonTracker] = {}
        self.micro_zones: list[MicroZone] = []
        self.vacuum_features: dict[str, Any] = {}  # floor_id -> VacuumMapFeatures

    def set_vacuum_features(self, floor_id: str, features: Any) -> None:
        """Store semantic vacuum map features for a floor."""
        self.vacuum_features[floor_id] = features

    def get_vacuum_features(self, floor_id: str) -> Any | None:
        """Retrieve semantic vacuum map features for a floor."""
        return self.vacuum_features.get(floor_id)

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
