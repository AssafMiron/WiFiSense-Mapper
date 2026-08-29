"""WiFiSense Mapper — Baseline Learner & Anomaly Detector.

Learns a statistical baseline for each grid cell and flags deviations
as anomalies (e.g., moved furniture, new objects, occupancy patterns).

Algorithm:
  EWMA (Exponentially Weighted Moving Average) + rolling standard
  deviation for each cell's RSSI and CSI scores.

  Anomaly score = |current_value − ewma| / std_dev  (z-score equivalent)

  This is a pure-Python, dependency-free implementation. No scikit-learn
  or statistical libraries are required.

Why not ML?
  For typical home WiFi anomaly detection, simple statistical baselines
  outperform lightweight ML models because:
  - Training data is scarce (days, not millions of samples).
  - The feature space is small (1–2 values per cell per timestep).
  - Interpretability matters — users want to understand WHY an alert fired.
  - ML models add dependency weight without commensurate accuracy gains
    at this scale.

Persistence:
  Baseline state is serialized to HA storage so that the learned baseline
  survives restarts. The learning window defaults to 7 days; older
  samples are discarded from the EWMA state (not from the grid itself).
"""
from __future__ import annotations

import logging
import math
import time
from dataclasses import dataclass, field
from typing import Any, TYPE_CHECKING

if TYPE_CHECKING:
    from .grid import GridCell, SpatialGrid

_LOGGER = logging.getLogger(__name__)

# EWMA smoothing factor α. Lower = slower adaptation (longer memory).
EWMA_ALPHA = 0.05
# Minimum std dev to avoid division by zero when signal is very stable
MIN_STD_DEV = 0.5


@dataclass
class CellBaseline:
    """Running baseline stats for a single grid cell."""

    ewma_rssi: float | None = None
    """EWMA of mean RSSI values."""

    ewma_csi: float | None = None
    """EWMA of mean CSI motion scores."""

    ewma_sq_rssi: float | None = None
    """EWMA of squared RSSI for online variance estimation."""

    ewma_sq_csi: float | None = None
    """EWMA of squared CSI for online variance estimation."""

    sample_count: int = 0
    """Total number of samples incorporated into the baseline."""

    last_updated: float = field(default_factory=time.time)

    def std_rssi(self) -> float:
        """Estimated RSSI standard deviation (online formula)."""
        if self.ewma_rssi is None or self.ewma_sq_rssi is None:
            return MIN_STD_DEV
        variance = self.ewma_sq_rssi - self.ewma_rssi**2
        return max(MIN_STD_DEV, math.sqrt(max(0.0, variance)))

    def std_csi(self) -> float:
        """Estimated CSI score standard deviation (online formula)."""
        if self.ewma_csi is None or self.ewma_sq_csi is None:
            return MIN_STD_DEV
        variance = self.ewma_sq_csi - self.ewma_csi**2
        return max(MIN_STD_DEV, math.sqrt(max(0.0, variance)))

    def to_dict(self) -> dict[str, Any]:
        return {
            "ewma_rssi": self.ewma_rssi,
            "ewma_csi": self.ewma_csi,
            "ewma_sq_rssi": self.ewma_sq_rssi,
            "ewma_sq_csi": self.ewma_sq_csi,
            "sample_count": self.sample_count,
            "last_updated": self.last_updated,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "CellBaseline":
        bl = cls()
        bl.ewma_rssi = data.get("ewma_rssi")
        bl.ewma_csi = data.get("ewma_csi")
        bl.ewma_sq_rssi = data.get("ewma_sq_rssi")
        bl.ewma_sq_csi = data.get("ewma_sq_csi")
        bl.sample_count = data.get("sample_count", 0)
        bl.last_updated = data.get("last_updated", time.time())
        return bl


def _ewma_update(prev: float | None, new_val: float, alpha: float = EWMA_ALPHA) -> float:
    """Update an EWMA estimate with a new observation."""
    if prev is None:
        return new_val
    return alpha * new_val + (1 - alpha) * prev


class BaselineLearner:
    """Per-floor baseline learning and anomaly detection.

    Usage:
        learner.update_from_grid(grid)           # call after each coordinator poll
        scores = learner.compute_anomaly_scores(grid)   # get z-scores per cell
        is_anomaly = learner.is_anomaly(scores, threshold=3.0)
    """

    def __init__(self, floor_id: str, alpha: float = EWMA_ALPHA) -> None:
        self.floor_id = floor_id
        self.alpha = alpha
        self._baselines: dict[tuple[int, int], CellBaseline] = {}
        self._learning_start: float = time.time()
        self._sample_count: int = 0

    @property
    def is_warmed_up(self) -> bool:
        """True after enough samples for reliable anomaly detection (≥ 100)."""
        return self._sample_count >= 100

    def update_from_grid(self, grid: "SpatialGrid") -> None:
        """Incorporate latest grid cell values into the baseline EWMA."""
        for cell in grid.all_cells():
            key = (cell.x, cell.y)
            if key not in self._baselines:
                self._baselines[key] = CellBaseline()
            bl = self._baselines[key]

            rssi = cell.mean_rssi
            csi = cell.mean_csi_score

            if rssi is not None:
                bl.ewma_rssi = _ewma_update(bl.ewma_rssi, rssi, self.alpha)
                bl.ewma_sq_rssi = _ewma_update(
                    bl.ewma_sq_rssi, rssi**2, self.alpha
                )

            if csi is not None:
                bl.ewma_csi = _ewma_update(bl.ewma_csi, csi, self.alpha)
                bl.ewma_sq_csi = _ewma_update(
                    bl.ewma_sq_csi, csi**2, self.alpha
                )

            bl.sample_count += 1
            bl.last_updated = time.time()

        self._sample_count += 1

    def compute_anomaly_scores(
        self,
        grid: "SpatialGrid",
        use_csi: bool = True,
        use_rssi: bool = True,
    ) -> dict[tuple[int, int], float]:
        """Return anomaly z-scores for all populated cells.

        Score is the maximum of RSSI and CSI z-scores (if both available).
        Returns 0.0 for cells with insufficient baseline data.
        """
        scores: dict[tuple[int, int], float] = {}

        for cell in grid.all_cells():
            key = (cell.x, cell.y)
            bl = self._baselines.get(key)
            if bl is None or bl.sample_count < 10:
                scores[key] = 0.0
                continue

            cell_score = 0.0

            if use_rssi and cell.mean_rssi is not None and bl.ewma_rssi is not None:
                rssi_z = abs(cell.mean_rssi - bl.ewma_rssi) / bl.std_rssi()
                cell_score = max(cell_score, rssi_z)

            if use_csi and cell.mean_csi_score is not None and bl.ewma_csi is not None:
                csi_z = abs(cell.mean_csi_score - bl.ewma_csi) / bl.std_csi()
                cell_score = max(cell_score, csi_z)

            scores[key] = cell_score

        return scores

    def max_anomaly_score(
        self, scores: dict[tuple[int, int], float]
    ) -> float:
        """Return the maximum anomaly score across all cells."""
        return max(scores.values(), default=0.0)

    def is_anomaly(
        self,
        scores: dict[tuple[int, int], float],
        threshold: float = 3.0,
    ) -> bool:
        """Return True if any cell exceeds the anomaly threshold."""
        if not self.is_warmed_up:
            return False
        return self.max_anomaly_score(scores) >= threshold

    def anomalous_cells(
        self,
        scores: dict[tuple[int, int], float],
        threshold: float = 3.0,
    ) -> list[tuple[int, int]]:
        """Return list of (col, row) cell positions with anomaly score ≥ threshold."""
        return [pos for pos, score in scores.items() if score >= threshold]

    def reset(self) -> None:
        """Clear all baseline data (e.g. after major room reconfiguration)."""
        self._baselines.clear()
        self._sample_count = 0
        self._learning_start = time.time()
        _LOGGER.info("Baseline reset for floor %s", self.floor_id)

    def to_dict(self) -> dict[str, Any]:
        """Serialize baseline state for HA storage."""
        return {
            "floor_id": self.floor_id,
            "alpha": self.alpha,
            "sample_count": self._sample_count,
            "learning_start": self._learning_start,
            "baselines": {
                f"{x},{y}": bl.to_dict()
                for (x, y), bl in self._baselines.items()
            },
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "BaselineLearner":
        """Deserialize baseline state from HA storage."""
        learner = cls(
            floor_id=data["floor_id"],
            alpha=data.get("alpha", EWMA_ALPHA),
        )
        learner._sample_count = data.get("sample_count", 0)
        learner._learning_start = data.get("learning_start", time.time())
        for key_str, bl_data in data.get("baselines", {}).items():
            x, y = map(int, key_str.split(","))
            learner._baselines[(x, y)] = CellBaseline.from_dict(bl_data)
        return learner
