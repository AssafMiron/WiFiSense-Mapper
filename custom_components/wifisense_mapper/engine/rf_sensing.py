"""WiFiSense Mapper — RF Disturbance and Motion Sensing Engine.

Provides device-free human motion and presence detection by monitoring RF signal
perturbations across:
1. Wireless mesh backhaul links between Deco router nodes.
2. Direct Wi-Fi links to stationary IoT devices (smart plugs, smart speakers, TVs).

When a human body traverses an RF line-of-sight path, scattering and multipath
fading cause transient variance spikes and Z-score shifts away from the learned
baseline.
"""

from __future__ import annotations

import logging
import math
import time
from collections import deque
from dataclasses import dataclass, field
from typing import Any

_LOGGER = logging.getLogger(__name__)

# Sensitivity profiles: (z_score_weight, variance_weight, trigger_threshold, min_variance)
SENSITIVITY_PROFILES: dict[str, tuple[float, float, float, float]] = {
    "low": (0.4, 0.6, 70.0, 10.0),      # Stricter, requires larger movement
    "medium": (0.5, 0.5, 55.0, 6.0),    # Balanced for everyday walking
    "high": (0.6, 0.4, 40.0, 3.5),      # Sensitive, catches smaller movements
}


@dataclass
class RFLinkState:
    """State and statistical metrics for a single monitored RF link."""

    link_id: str
    link_type: str  # "backhaul" | "client"
    ap_mac: str
    peer_mac: str
    peer_name: str | None = None
    area_id: str | None = None
    floor_id: str | None = None

    # History of recent RSSI samples: deque of (timestamp, rssi)
    recent_samples: deque[tuple[float, int]] = field(
        default_factory=lambda: deque(maxlen=20)
    )

    # Rolling baseline estimation
    baseline_samples: deque[int] = field(
        default_factory=lambda: deque(maxlen=100)
    )
    baseline_mean: float = -65.0
    baseline_std: float = 2.0

    # Latest computation
    last_rssi: int | None = None
    last_variance: float = 0.0
    last_z_score: float = 0.0
    disturbance_score: float = 0.0  # 0.0 to 100.0%
    is_perturbed: bool = False
    consecutive_hits: int = 0
    last_seen: float = 0.0


@dataclass
class RFSensingSnapshot:
    """Point-in-time snapshot of the RF sensing engine state."""

    area_motion: dict[str, bool] = field(default_factory=dict)
    area_scores: dict[str, float] = field(default_factory=dict)
    area_active_links: dict[str, int] = field(default_factory=dict)
    link_states: dict[str, dict[str, Any]] = field(default_factory=dict)
    has_active_perturbation: bool = False
    burst_recommended: bool = False


class StationaryDeviceClassifier:
    """Automatically identifies stationary Wi-Fi devices to avoid mobile false alarms.

    Monitors client roaming behaviour and presence continuity. Devices that never
    roam across APs, maintain high uptime, and have steady signals are classified
    as stationary anchors (e.g. smart plugs, TVs, smart speakers).
    """

    def __init__(self, observation_window_sec: float = 1800.0) -> None:
        self.observation_window_sec = observation_window_sec
        # mac -> list of (timestamp, ap_mac)
        self._ap_history: dict[str, deque[tuple[float, str]]] = {}
        # mac -> set of AP MACs seen
        self._associated_aps: dict[str, set[str]] = {}
        # mac -> first seen timestamp
        self._first_seen: dict[str, float] = {}
        # User manual overrides / exclusions
        self._forced_stationary: set[str] = set()
        self._excluded_macs: set[str] = set()

    def set_forced_stationary(self, macs: list[str]) -> None:
        """Manually force specific MAC addresses to be stationary."""
        self._forced_stationary = {m.lower().strip() for m in macs}

    def set_excluded_macs(self, macs: list[str]) -> None:
        """Exclude mobile devices (e.g. tagged person phones) from being stationary."""
        self._excluded_macs = {m.lower().strip() for m in macs}

    def record_client(
        self,
        mac: str,
        ap_mac: str | None,
        now: float | None = None,
    ) -> None:
        """Record an observation of client association."""
        norm_mac = mac.lower().strip()
        if not ap_mac:
            return
        norm_ap = ap_mac.lower().strip()
        ts = now if now is not None else time.time()

        if norm_mac not in self._first_seen:
            self._first_seen[norm_mac] = ts
            self._ap_history[norm_mac] = deque(maxlen=50)
            self._associated_aps[norm_mac] = set()

        self._ap_history[norm_mac].append((ts, norm_ap))
        self._associated_aps[norm_mac].add(norm_ap)

        # Prune old history beyond observation window
        cutoff = ts - self.observation_window_sec
        while self._ap_history[norm_mac] and self._ap_history[norm_mac][0][0] < cutoff:
            self._ap_history[norm_mac].popleft()

    def is_stationary(self, mac: str) -> bool:
        """Return True if the client is classified as a stationary anchor."""
        norm_mac = mac.lower().strip()
        if norm_mac in self._excluded_macs:
            return False
        if norm_mac in self._forced_stationary:
            return True

        if norm_mac not in self._first_seen:
            return False

        # If it has associated with multiple different APs, it's roaming / mobile
        distinct_aps = {ap for _, ap in self._ap_history.get(norm_mac, [])}
        if len(distinct_aps) > 1:
            return False

        # Require at least 2 samples and steady single AP association
        history_len = len(self._ap_history.get(norm_mac, []))
        return history_len >= 2


class RollingBaselineTracker:
    """Tracks the undisturbed RF baseline mean and standard deviation per link.

    Uses a rolling window of quiet samples. When an anomaly / perturbation is
    detected, baseline updates are frozen or slowed to prevent the baseline
    from learning human presence as normal quiet state.
    """

    def __init__(self, window_size: int = 60, min_std: float = 1.2) -> None:
        self.window_size = window_size
        self.min_std = min_std

    def update_link_baseline(self, link: RFLinkState, rssi: int) -> None:
        """Update the baseline with a new sample, unless currently perturbed."""
        # Only add to baseline if not actively perturbed
        if not link.is_perturbed:
            link.baseline_samples.append(rssi)
            n = len(link.baseline_samples)
            if n >= 2:
                mean = sum(link.baseline_samples) / n
                variance = sum((x - mean) ** 2 for x in link.baseline_samples) / (n - 1)
                link.baseline_mean = mean
                link.baseline_std = max(math.sqrt(variance), self.min_std)
            elif n == 1:
                link.baseline_mean = float(rssi)
                link.baseline_std = self.min_std


class RFPerturbationDetector:
    """Analyzes RF links (mesh backhaul + stationary IoT) to detect motion and presence."""

    def __init__(
        self,
        sensitivity: str = "medium",
        off_delay_sec: float = 30.0,
        min_consecutive: int = 2,
    ) -> None:
        self.sensitivity = sensitivity
        self.off_delay_sec = off_delay_sec
        self.min_consecutive = min_consecutive

        self.classifier = StationaryDeviceClassifier()
        self.baseline_tracker = RollingBaselineTracker()
        self.links: dict[str, RFLinkState] = {}

        # Area state: area_id -> is_motion_active
        self._area_motion: dict[str, bool] = {}
        # Area last motion trigger timestamp
        self._area_last_motion_time: dict[str, float] = {}
        # Area consecutive trigger count
        self._area_consecutive_hits: dict[str, int] = {}
        # Area latest disturbance score
        self._area_scores: dict[str, float] = {}

    def set_sensitivity(self, sensitivity: str) -> None:
        """Update sensitivity profile."""
        if sensitivity in SENSITIVITY_PROFILES:
            self.sensitivity = sensitivity

    def set_off_delay(self, off_delay: float) -> None:
        """Update off delay in seconds."""
        self.off_delay_sec = max(5.0, off_delay)

    def feed_backhaul_sample(
        self,
        satellite_mac: str,
        parent_mac: str,
        rssi: int,
        area_id: str | None = None,
        floor_id: str | None = None,
        satellite_name: str | None = None,
        now: float | None = None,
    ) -> None:
        """Feed a wireless mesh backhaul link RSSI sample between two Deco nodes."""
        norm_sat = satellite_mac.lower().strip()
        norm_parent = parent_mac.lower().strip()
        link_id = f"backhaul:{norm_parent}->{norm_sat}"
        ts = now if now is not None else time.time()

        if link_id not in self.links:
            self.links[link_id] = RFLinkState(
                link_id=link_id,
                link_type="backhaul",
                ap_mac=norm_parent,
                peer_mac=norm_sat,
                peer_name=satellite_name,
                area_id=area_id,
                floor_id=floor_id,
            )

        link = self.links[link_id]
        if area_id:
            link.area_id = area_id
        if floor_id:
            link.floor_id = floor_id
        if satellite_name:
            link.peer_name = satellite_name

        self._process_sample(link, rssi, ts)

    def feed_client_sample(
        self,
        client_mac: str,
        ap_mac: str,
        rssi: int,
        client_name: str | None = None,
        area_id: str | None = None,
        floor_id: str | None = None,
        now: float | None = None,
    ) -> None:
        """Feed an RSSI sample for a Wi-Fi client associated with an AP."""
        norm_client = client_mac.lower().strip()
        norm_ap = ap_mac.lower().strip()
        ts = now if now is not None else time.time()

        # Update stationary device classifier
        self.classifier.record_client(norm_client, norm_ap, now=ts)
        if not self.classifier.is_stationary(norm_client):
            return

        link_id = f"client:{norm_ap}->{norm_client}"
        if link_id not in self.links:
            self.links[link_id] = RFLinkState(
                link_id=link_id,
                link_type="client",
                ap_mac=norm_ap,
                peer_mac=norm_client,
                peer_name=client_name,
                area_id=area_id,
                floor_id=floor_id,
            )

        link = self.links[link_id]
        if area_id:
            link.area_id = area_id
        if floor_id:
            link.floor_id = floor_id
        if client_name:
            link.peer_name = client_name

        self._process_sample(link, rssi, ts)

    def _process_sample(self, link: RFLinkState, rssi: int, ts: float) -> None:
        """Process a single RSSI sample for an RF link."""
        link.last_rssi = rssi
        link.last_seen = ts
        link.recent_samples.append((ts, rssi))

        # 1. Seed baseline if insufficient samples
        if len(link.baseline_samples) < 2:
            self.baseline_tracker.update_link_baseline(link, rssi)

        # 2. Compute sliding window variance (over last 4 to 8 samples)
        recent_rssis = [val for _, val in link.recent_samples]
        w_size = min(len(recent_rssis), 6)
        if w_size >= 2:
            window = recent_rssis[-w_size:]
            m = sum(window) / w_size
            link.last_variance = sum((x - m) ** 2 for x in window) / (w_size - 1)
        else:
            link.last_variance = 0.0

        # 3. Compute Z-score from baseline
        delta = abs(rssi - link.baseline_mean)
        link.last_z_score = delta / max(link.baseline_std, 1.0)

        # 4. Compute disturbance score (0 - 100%)
        z_w, v_w, trigger_thresh, min_var = SENSITIVITY_PROFILES.get(
            self.sensitivity, SENSITIVITY_PROFILES["medium"]
        )

        # Normalize components:
        # Z-score: 0 to 4 maps to 0-100%
        # Variance: 0 to 15 maps to 0-100%
        norm_z = min(100.0, (link.last_z_score / 4.0) * 100.0)
        norm_v = min(100.0, (link.last_variance / 15.0) * 100.0)
        score = (z_w * norm_z) + (v_w * norm_v)
        link.disturbance_score = round(score, 1)

        # Perturbation condition: score >= threshold AND minimum variance reached
        is_hit = (score >= trigger_thresh) and (link.last_variance >= min_var)
        if is_hit:
            link.consecutive_hits += 1
            if link.consecutive_hits >= self.min_consecutive:
                link.is_perturbed = True
        else:
            link.consecutive_hits = 0
            link.is_perturbed = False

        # 5. Update baseline only when link is quiet and not actively perturbed
        if len(link.baseline_samples) >= 2 and not is_hit and not link.is_perturbed:
            self.baseline_tracker.update_link_baseline(link, rssi)

    def evaluate_areas(self, now: float | None = None) -> RFSensingSnapshot:
        """Aggregate link states into area disturbance scores and presence flags."""
        ts = now if now is not None else time.time()
        area_max_scores: dict[str, float] = {}
        area_active_links: dict[str, int] = {}
        area_has_hit: dict[str, bool] = {}
        has_any_perturbation = False

        # Group links by area
        for link in self.links.values():
            if not link.area_id:
                continue
            # Ignore stale links not seen in last 120 seconds
            if ts - link.last_seen > 120.0:
                continue

            aid = link.area_id
            area_active_links[aid] = area_active_links.get(aid, 0) + 1
            if aid not in area_max_scores or link.disturbance_score > area_max_scores[aid]:
                area_max_scores[aid] = link.disturbance_score

            if link.is_perturbed:
                area_has_hit[aid] = True
                has_any_perturbation = True

        all_monitored_areas = set(area_max_scores.keys()) | set(self._area_motion.keys())

        # Update area presence with hysteresis and hold-down timer
        for aid in all_monitored_areas:
            score = area_max_scores.get(aid, 0.0)
            self._area_scores[aid] = score
            is_perturbed = area_has_hit.get(aid, False)

            if is_perturbed:
                self._area_motion[aid] = True
                self._area_last_motion_time[aid] = ts
            else:
                last_time = self._area_last_motion_time.get(aid, 0.0)
                if ts - last_time >= self.off_delay_sec:
                    self._area_motion[aid] = False

        # Build link state dictionary for diagnostic & visual rendering
        link_dict: dict[str, dict[str, Any]] = {}
        for lid, link in self.links.items():
            link_dict[lid] = {
                "link_id": link.link_id,
                "link_type": link.link_type,
                "ap_mac": link.ap_mac,
                "peer_mac": link.peer_mac,
                "peer_name": link.peer_name,
                "area_id": link.area_id,
                "floor_id": link.floor_id,
                "last_rssi": link.last_rssi,
                "baseline_mean": round(link.baseline_mean, 1),
                "baseline_std": round(link.baseline_std, 2),
                "last_variance": round(link.last_variance, 2),
                "last_z_score": round(link.last_z_score, 2),
                "disturbance_score": link.disturbance_score,
                "is_perturbed": link.is_perturbed,
            }

        return RFSensingSnapshot(
            area_motion=dict(self._area_motion),
            area_scores=dict(self._area_scores),
            area_active_links=area_active_links,
            link_states=link_dict,
            has_active_perturbation=has_any_perturbation,
            burst_recommended=has_any_perturbation,
        )
