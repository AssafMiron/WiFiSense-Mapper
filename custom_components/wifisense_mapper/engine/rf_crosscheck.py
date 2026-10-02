"""WiFiSense Mapper — RF Motion & Person Presence Cross-Checking Engine.

Cross-checks device-free RF disturbances against connected Wi-Fi person tracking to:
1. Distinguish verified family members from unidentified intruders.
2. Triage physical proximity and activity (e.g. sitting person standing up).
3. Prevent duplicate and false presence alarms across room walls (wall-bleed).
4. Bridge asynchronous router polling and mesh roaming via a sliding coincidence window.
"""

from __future__ import annotations

import logging
import math
import time
from collections import deque
from dataclasses import dataclass, field
from typing import Any

from ..const import (
    DEFAULT_RF_COINCIDENCE_WINDOW_S,
    DEFAULT_RF_PROXIMITY_THRESHOLD_M,
    DEFAULT_RF_WALL_BLEED_SUPPRESSION,
    STATE_AWAY,
)
from .localization import PersonTracker
from .rf_sensing import RFSensingSnapshot

_LOGGER = logging.getLogger(__name__)


@dataclass
class RFCrossCheckResult:
    """Consolidated cross-checking evaluation result for an area."""

    area_id: str
    unidentified_motion: bool = False
    occupant_type: str = "none"  # "none" | "verified" | "unidentified" | "mixed"
    verified_occupants: list[str] = field(default_factory=list)
    unidentified_count: int = 0
    rf_corroborated_occupant: str | None = None
    disturbance_score: float = 0.0
    active_links_count: int = 0
    last_unidentified_ts: float | None = None

    def to_dict(self) -> dict[str, Any]:
        """Convert result to dictionary for entity attributes."""
        return {
            "area_id": self.area_id,
            "unidentified_motion": self.unidentified_motion,
            "occupant_type": self.occupant_type,
            "verified_occupants": list(self.verified_occupants),
            "unidentified_count": self.unidentified_count,
            "rf_corroborated_occupant": self.rf_corroborated_occupant,
            "disturbance_score": self.disturbance_score,
            "active_links_count": self.active_links_count,
            "last_unidentified_ts": self.last_unidentified_ts,
        }


@dataclass
class PersonObservation:
    """Historical observation of a person's location for sliding window matching."""

    mac: str
    name: str
    area_id: str | None
    floor_id: str | None
    ap_mac: str | None
    distance_m: float | None
    rssi: int | None
    x_m: float
    y_m: float
    timestamp: float


class RFCrossCheckEngine:
    """Engine that correlates RF disturbance events with connected person tracking."""

    def __init__(
        self,
        proximity_threshold_m: float = DEFAULT_RF_PROXIMITY_THRESHOLD_M,
        coincidence_window_s: float = DEFAULT_RF_COINCIDENCE_WINDOW_S,
        wall_bleed_suppression: bool = DEFAULT_RF_WALL_BLEED_SUPPRESSION,
    ) -> None:
        self.proximity_threshold_m = proximity_threshold_m
        self.coincidence_window_s = coincidence_window_s
        self.wall_bleed_suppression = wall_bleed_suppression

        # Area last unidentified motion timestamp
        self._last_unidentified: dict[str, float] = {}

        # Rolling history of person observations for sliding window matching
        self._person_history: deque[PersonObservation] = deque(maxlen=200)

    def record_person_observation(
        self,
        tracker: PersonTracker,
        now: float | None = None,
    ) -> None:
        """Record a person observation into the sliding coincidence history."""
        ts = now if now is not None else time.time()
        st = tracker.latest_state
        if st.activity == STATE_AWAY:
            return

        obs = PersonObservation(
            mac=tracker.mac,
            name=tracker.person_name,
            area_id=st.area_id,
            floor_id=st.floor_id,
            ap_mac=st.ap_mac,
            distance_m=st.distance_m,
            rssi=st.rssi,
            x_m=st.x_m,
            y_m=st.y_m,
            timestamp=ts,
        )
        self._person_history.append(obs)

    def _prune_history(self, now: float) -> None:
        """Prune observations older than 2x the coincidence window."""
        cutoff = now - max(30.0, self.coincidence_window_s * 2)
        while self._person_history and self._person_history[0].timestamp < cutoff:
            self._person_history.popleft()

    def evaluate(
        self,
        rf_snapshot: RFSensingSnapshot,
        trackers: dict[str, PersonTracker],
        ap_stats: dict[str, Any],
        grids: dict[str, Any],
        all_area_ids: set[str],
        now: float | None = None,
    ) -> tuple[dict[str, RFCrossCheckResult], list[PersonTracker]]:
        """Evaluate all areas and return results per area plus trackers to corroborate."""
        ts = now if now is not None else time.time()
        self._prune_history(ts)

        # 1. Record current state of all active trackers into sliding history
        for tracker in trackers.values():
            self.record_person_observation(tracker, now=ts)

        # 2. Index active persons by area
        area_occupants: dict[str, list[PersonTracker]] = {}
        for tracker in trackers.values():
            st = tracker.latest_state
            if st.activity != STATE_AWAY and st.area_id:
                area_occupants.setdefault(st.area_id, []).append(tracker)

        # 3. Index RF link states by area
        area_links: dict[str, list[dict[str, Any]]] = {}
        area_perturbed_links: dict[str, list[dict[str, Any]]] = {}
        for link_dict in rf_snapshot.link_states.values():
            aid = link_dict.get("area_id")
            if aid:
                area_links.setdefault(aid, []).append(link_dict)
                if link_dict.get("is_perturbed"):
                    area_perturbed_links.setdefault(aid, []).append(link_dict)

        results: dict[str, RFCrossCheckResult] = {}
        corroborated_trackers: list[PersonTracker] = []

        # 4. Evaluate each area
        for area_id in all_area_ids:
            is_rf_active = bool(rf_snapshot.area_motion.get(area_id, False))
            score = rf_snapshot.area_scores.get(area_id, 0.0)
            active_links = rf_snapshot.area_active_links.get(area_id, 0)

            occupants = area_occupants.get(area_id, [])
            verified_names = [t.person_name for t in occupants]

            # If no RF motion is active in this area
            if not is_rf_active:
                results[area_id] = RFCrossCheckResult(
                    area_id=area_id,
                    unidentified_motion=False,
                    occupant_type="verified" if occupants else "none",
                    verified_occupants=verified_names,
                    unidentified_count=0,
                    rf_corroborated_occupant=None,
                    disturbance_score=score,
                    active_links_count=active_links,
                    last_unidentified_ts=self._last_unidentified.get(area_id),
                )
                continue

            # RF motion IS active in this area. Attempt to match with occupants.
            perturbed_links = area_perturbed_links.get(area_id, [])
            matched_tracker: PersonTracker | None = None

            # Strategy A: Check current occupants in this area against perturbed links
            for tracker in occupants:
                if self._is_person_matching_links(
                    tracker, perturbed_links, area_links.get(area_id, []), grids
                ):
                    matched_tracker = tracker
                    break

            # Strategy B: Sliding coincidence window for this area (within ± coincidence_window_s)
            if matched_tracker is None:
                matched_tracker = self._check_coincidence_history(
                    area_id, perturbed_links, trackers, ts
                )

            # Strategy C: Wall-bleed suppression from adjacent rooms
            if matched_tracker is None and self.wall_bleed_suppression:
                matched_tracker = self._check_wall_bleed(
                    perturbed_links, trackers, grids, ap_stats
                )

            # 5. Classify presence and trigger feedback
            if matched_tracker is not None:
                # Corroborate person activity & reset decay
                matched_tracker.corroborate_rf_motion(ts=ts)
                if matched_tracker not in corroborated_trackers:
                    corroborated_trackers.append(matched_tracker)

                results[area_id] = RFCrossCheckResult(
                    area_id=area_id,
                    unidentified_motion=False,
                    occupant_type="verified",
                    verified_occupants=verified_names
                    if verified_names
                    else [matched_tracker.person_name],
                    unidentified_count=0,
                    rf_corroborated_occupant=matched_tracker.person_name,
                    disturbance_score=score,
                    active_links_count=active_links,
                    last_unidentified_ts=self._last_unidentified.get(area_id),
                )
            else:
                # No known person matched the RF disturbance
                self._last_unidentified[area_id] = ts
                occ_type = "mixed" if occupants else "unidentified"

                results[area_id] = RFCrossCheckResult(
                    area_id=area_id,
                    unidentified_motion=True,
                    occupant_type=occ_type,
                    verified_occupants=verified_names,
                    unidentified_count=1,
                    rf_corroborated_occupant=None,
                    disturbance_score=score,
                    active_links_count=active_links,
                    last_unidentified_ts=ts,
                )

        return results, corroborated_trackers

    def _is_person_matching_links(
        self,
        tracker: PersonTracker,
        perturbed_links: list[dict[str, Any]],
        all_area_links: list[dict[str, Any]],
        grids: dict[str, Any],
    ) -> bool:
        """Check if a person is in immediate proximity to the perturbed link(s)."""
        st = tracker.latest_state
        dist = st.distance_m
        rssi = st.rssi

        # If estimated distance is within proximity threshold or strong RSSI
        if dist is not None and dist <= self.proximity_threshold_m:
            return True
        if rssi is not None and rssi >= -60:
            return True

        # Check AP MAC association match
        target_links = perturbed_links if perturbed_links else all_area_links
        for link in target_links:
            link_ap = (link.get("ap_mac") or "").lower()
            if (st.ap_mac and st.ap_mac.lower() == link_ap) and (
                dist is None or dist <= (self.proximity_threshold_m * 1.2)
            ):
                return True

            # Geometric check on floor grid
            floor_id = st.floor_id or "default"
            grid = grids.get(floor_id)
            if grid and link_ap and st.x_m > 0 and st.y_m > 0:
                ap_pos = grid.get_ap_position_m(link_ap)
                if ap_pos:
                    geom_dist = math.hypot(st.x_m - ap_pos[0], st.y_m - ap_pos[1])
                    if geom_dist <= self.proximity_threshold_m:
                        return True

        return False

    def _check_coincidence_history(
        self,
        area_id: str,
        perturbed_links: list[dict[str, Any]],
        trackers: dict[str, PersonTracker],
        now: float,
    ) -> PersonTracker | None:
        """Check if any person was in this area within the sliding coincidence window."""
        window_start = now - self.coincidence_window_s
        for obs in reversed(self._person_history):
            if obs.timestamp < window_start:
                break
            if obs.area_id == area_id and (
                obs.distance_m is None
                or obs.distance_m <= (self.proximity_threshold_m * 1.5)
            ):
                return trackers.get(obs.mac)
        return None

    def _check_wall_bleed(
        self,
        perturbed_links: list[dict[str, Any]],
        trackers: dict[str, PersonTracker],
        grids: dict[str, Any],
        ap_stats: dict[str, Any],
    ) -> PersonTracker | None:
        """Check if an occupant in an adjacent room is within physical proximity of the router."""
        for link in perturbed_links:
            ap_mac = (link.get("ap_mac") or "").lower()
            if not ap_mac:
                continue

            floor_id = link.get("floor_id") or "default"
            grid = grids.get(floor_id)
            if not grid:
                continue

            ap_pos = grid.get_ap_position_m(ap_mac)
            if not ap_pos:
                continue

            # Check all active trackers regardless of area
            for tracker in trackers.values():
                st = tracker.latest_state
                if st.activity == STATE_AWAY:
                    continue
                if (st.floor_id or "default") != floor_id:
                    continue

                if st.x_m > 0 and st.y_m > 0:
                    geom_dist = math.hypot(st.x_m - ap_pos[0], st.y_m - ap_pos[1])
                    if geom_dist <= self.proximity_threshold_m:
                        _LOGGER.debug(
                            "Wall-bleed match: person %s in area %s is %.1fm from AP %s across wall",
                            tracker.person_name,
                            st.area_name,
                            geom_dist,
                            ap_mac,
                        )
                        return tracker

        return None
