"""WiFiSense Mapper — 2D Spatial Grid.

Represents a per-floor 2D grid of cells where each cell accumulates
RSSI samples from router clients and CSI motion/variance scores from
ESP32 nodes.

Grid coordinate system:
  - Origin (0, 0) is the top-left corner of the floor bounding box.
  - X increases to the right (east), Y increases downward (south).
  - Default resolution: 0.5 meters per cell.
  - Grid dimensions are set by the user (floor plan dimensions) or
    estimated from node positions.

Position estimation:
  - When a client's AP association is known (from router polling),
    we place the client's RSSI sample at the AP's grid position with
    a Gaussian decay applied to neighboring cells.
  - When CSI node positions are known, motion scores propagate to
    surrounding cells weighted by inverse distance.
  - Actual room-level accuracy is ~2–5 meters, depending on node
    density and building materials. This is NOT precision positioning.
"""

from __future__ import annotations

import logging
import math
import time
from dataclasses import dataclass, field
from typing import Any

_LOGGER = logging.getLogger(__name__)

# Maximum number of RSSI samples kept per (ap_mac, client_mac) pair per cell
MAX_RSSI_SAMPLES = 20
# Maximum age of samples to keep (seconds)
MAX_SAMPLE_AGE_S = 3600  # 1 hour


@dataclass
class GridCell:
    """A single cell in the spatial grid."""

    x: int
    """Column index."""

    y: int
    """Row index."""

    # RSSI samples: list of (timestamp, rssi_dbm) tuples
    rssi_samples: list[tuple[float, int]] = field(default_factory=list)

    # CSI motion scores: list of (timestamp, score) tuples
    csi_scores: list[tuple[float, float]] = field(default_factory=list)

    # Source attribution: which AP MACs contributed RSSI to this cell
    rssi_sources: set[str] = field(default_factory=set)

    # Which CSI node IDs contributed scores to this cell
    csi_sources: set[str] = field(default_factory=set)

    @property
    def mean_rssi(self) -> float | None:
        """Return mean RSSI over recent samples, or None if no samples."""
        if not self.rssi_samples:
            return None
        return sum(r for _, r in self.rssi_samples) / len(self.rssi_samples)

    @property
    def rssi_variance(self) -> float:
        """Return RSSI variance over recent samples (0.0 if < 2 samples)."""
        if len(self.rssi_samples) < 2:
            return 0.0
        mean = self.mean_rssi or 0.0
        return sum((r - mean) ** 2 for _, r in self.rssi_samples) / len(
            self.rssi_samples
        )

    @property
    def mean_csi_score(self) -> float | None:
        """Return mean CSI motion score, or None if no samples."""
        if not self.csi_scores:
            return None
        return sum(s for _, s in self.csi_scores) / len(self.csi_scores)

    def prune_old_samples(self, max_age_s: float = MAX_SAMPLE_AGE_S) -> None:
        """Remove samples older than max_age_s from this cell."""
        cutoff = time.time() - max_age_s
        self.rssi_samples = [(t, r) for t, r in self.rssi_samples if t >= cutoff]
        self.csi_scores = [(t, s) for t, s in self.csi_scores if t >= cutoff]

    def to_dict(self) -> dict[str, Any]:
        """Serialize cell to dict for storage."""
        return {
            "x": self.x,
            "y": self.y,
            "rssi_samples": self.rssi_samples,
            "csi_scores": self.csi_scores,
            "rssi_sources": list(self.rssi_sources),
            "csi_sources": list(self.csi_sources),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> GridCell:
        """Deserialize cell from dict."""
        cell = cls(x=data["x"], y=data["y"])
        cell.rssi_samples = [tuple(s) for s in data.get("rssi_samples", [])]  # type: ignore[misc]
        cell.csi_scores = [tuple(s) for s in data.get("csi_scores", [])]  # type: ignore[misc]
        cell.rssi_sources = set(data.get("rssi_sources", []))
        cell.csi_sources = set(data.get("csi_sources", []))
        return cell


class SpatialGrid:
    """Per-floor 2D grid accumulating WiFi sensing samples.

    The grid is sparse — only cells that have received at least one
    sample are instantiated. This keeps memory usage bounded even for
    large floor plans with few sensors.
    """

    def __init__(
        self,
        floor_id: str,
        width_m: float = 10.0,
        height_m: float = 10.0,
        resolution_m: float = 0.5,
    ) -> None:
        self.floor_id = floor_id
        self.width_m = width_m
        self.height_m = height_m
        self.resolution_m = resolution_m

        self.cols = max(1, int(width_m / resolution_m))
        self.rows = max(1, int(height_m / resolution_m))

        # Sparse cell storage: (col, row) → GridCell
        self._cells: dict[tuple[int, int], GridCell] = {}

        # AP positions (ap_mac → (col, row)) set by node configuration
        self._ap_positions: dict[str, tuple[int, int]] = {}

        # CSI node positions (device_id → (col, row))
        self._csi_positions: dict[str, tuple[int, int]] = {}

        # Room label positions: area_id → dict(name, x_m, y_m, segment_id)
        self.room_labels: dict[str, dict[str, Any]] = {}

        # AP metadata markers: ap_mac → dict(name, x_m, y_m, area_id)
        self.ap_markers: dict[str, dict[str, Any]] = {}

    def set_room_label(
        self,
        area_id: str,
        name: str,
        x_m: float,
        y_m: float,
        segment_id: str | None = None,
    ) -> None:
        """Register or update a room label position on the floor map."""
        self.room_labels[area_id] = {
            "name": name,
            "x_m": float(x_m),
            "y_m": float(y_m),
            "segment_id": str(segment_id) if segment_id is not None else None,
        }

    def set_ap_marker(
        self,
        ap_mac: str,
        name: str,
        x_m: float,
        y_m: float,
        area_id: str | None = None,
    ) -> None:
        """Register or update an AP marker position with real coordinates."""
        norm_mac = ap_mac.lower()
        self.set_ap_position(norm_mac, x_m, y_m)
        self.ap_markers[norm_mac] = {
            "name": name,
            "x_m": float(x_m),
            "y_m": float(y_m),
            "area_id": area_id,
        }

    def set_ap_position(self, ap_mac: str, x_m: float, y_m: float) -> None:
        """Register the physical position of an AP/mesh node."""
        col = min(int(x_m / self.resolution_m), self.cols - 1)
        row = min(int(y_m / self.resolution_m), self.rows - 1)
        self._ap_positions[ap_mac.lower()] = (col, row)

    def get_ap_position_m(self, ap_mac: str) -> tuple[float, float] | None:
        """Return the physical position of an AP in meters (x_m, y_m)."""
        pos = self._ap_positions.get(ap_mac.lower())
        if pos is None:
            return None
        return (pos[0] * self.resolution_m, pos[1] * self.resolution_m)

    def compute_multi_ap_coverage(
        self, ap_coverage_radius_m: float = 6.0
    ) -> list[list[float | None]]:
        """Compute multi-AP coverage matrix for the floor.

        Returns 2D matrix of values:
          - 0.0: Dead zone / unmonitored
          - 1.0: Covered by 1 AP
          - 2.0: Multi-AP cross-covered (overlap zone)
        """
        matrix: list[list[float | None]] = [
            [0.0] * self.cols for _ in range(self.rows)
        ]
        if not self._ap_positions:
            return matrix

        radius_cells = max(1.0, ap_coverage_radius_m / self.resolution_m)

        for r in range(self.rows):
            for c in range(self.cols):
                ap_count = 0
                for ac, ar in self._ap_positions.values():
                    dist = math.sqrt((c - ac) ** 2 + (r - ar) ** 2)
                    if dist <= radius_cells:
                        ap_count += 1
                matrix[r][c] = 2.0 if ap_count >= 2 else (1.0 if ap_count == 1 else 0.0)

        return matrix

    def set_csi_position(self, device_id: str, x_m: float, y_m: float) -> None:
        """Register the physical position of a CSI sensor node."""
        col = min(int(x_m / self.resolution_m), self.cols - 1)
        row = min(int(y_m / self.resolution_m), self.rows - 1)
        self._csi_positions[device_id] = (col, row)

    def get_csi_position_m(self, device_id: str) -> tuple[float, float] | None:
        """Return the physical position of a CSI node in meters (x_m, y_m)."""
        pos = self._csi_positions.get(device_id)
        if pos is None:
            return None
        return (pos[0] * self.resolution_m, pos[1] * self.resolution_m)

    def update_rssi(
        self,
        ap_mac: str,
        client_mac: str,
        rssi: int,
        spread_cells: int = 2,
    ) -> None:
        """Record a new RSSI sample, spreading it to neighboring cells.

        RSSI is placed at the AP's registered position with a simple
        Gaussian decay to adjacent cells. If the AP position is not
        configured, the sample is placed at the grid center as a fallback.

        Note: RSSI placement is approximate — it indicates proximity to
        the AP, NOT the exact client location. Use CSI scores for finer
        spatial resolution.
        """
        ap_pos = self._ap_positions.get(ap_mac.lower())
        if ap_pos is None:
            # Fallback to grid center when AP position is unknown
            ap_pos = (self.cols // 2, self.rows // 2)

        cx, cy = ap_pos
        now = time.time()

        for dx in range(-spread_cells, spread_cells + 1):
            for dy in range(-spread_cells, spread_cells + 1):
                col = cx + dx
                row = cy + dy
                if not (0 <= col < self.cols and 0 <= row < self.rows):
                    continue

                # Distance-based RSSI decay (path loss approximation)
                dist = math.sqrt(dx**2 + dy**2)
                decay_db = dist * 3  # ~3 dB per cell-length (approximate)
                adjusted_rssi = max(-100, rssi - int(decay_db))

                cell = self._get_or_create_cell(col, row)
                cell.rssi_samples.append((now, adjusted_rssi))
                cell.rssi_sources.add(ap_mac.lower())

                # Trim to max samples
                if len(cell.rssi_samples) > MAX_RSSI_SAMPLES:
                    cell.rssi_samples = cell.rssi_samples[-MAX_RSSI_SAMPLES:]

    def update_csi_score(
        self,
        device_id: str,
        score: float,
        spread_cells: int = 3,
    ) -> None:
        """Record a new CSI motion score, spreading to neighboring cells.

        CSI motion scores represent the degree of channel variation
        due to movement in the sensor's field. Higher scores indicate
        more motion. The score decays with distance from the sensor.
        """
        csi_pos = self._csi_positions.get(device_id)
        if csi_pos is None:
            csi_pos = (self.cols // 2, self.rows // 2)

        cx, cy = csi_pos
        now = time.time()

        for dx in range(-spread_cells, spread_cells + 1):
            for dy in range(-spread_cells, spread_cells + 1):
                col = cx + dx
                row = cy + dy
                if not (0 <= col < self.cols and 0 <= row < self.rows):
                    continue

                dist = math.sqrt(dx**2 + dy**2)
                # CSI score decays linearly with distance
                decay = 1.0 / (1.0 + dist)
                adjusted_score = score * decay

                cell = self._get_or_create_cell(col, row)
                cell.csi_scores.append((now, adjusted_score))
                cell.csi_sources.add(device_id)

                if len(cell.csi_scores) > MAX_RSSI_SAMPLES:
                    cell.csi_scores = cell.csi_scores[-MAX_RSSI_SAMPLES:]

    def get_cell(self, col: int, row: int) -> GridCell | None:
        """Return the cell at (col, row) or None if not yet populated."""
        return self._cells.get((col, row))

    def all_cells(self) -> list[GridCell]:
        """Return all populated cells."""
        return list(self._cells.values())

    def prune_old_samples(self) -> None:
        """Remove old samples from all cells."""
        for cell in self._cells.values():
            cell.prune_old_samples()

    def to_rssi_matrix(self) -> list[list[float | None]]:
        """Return full grid as rows×cols matrix of mean RSSI values (pure Python)."""
        matrix: list[list[float | None]] = [
            [None] * self.cols for _ in range(self.rows)
        ]
        for (col, row), cell in self._cells.items():
            matrix[row][col] = cell.mean_rssi
        return matrix

    def to_csi_matrix(self) -> list[list[float | None]]:
        """Return full grid as rows×cols matrix of mean CSI scores (pure Python)."""
        matrix: list[list[float | None]] = [
            [None] * self.cols for _ in range(self.rows)
        ]
        for (col, row), cell in self._cells.items():
            matrix[row][col] = cell.mean_csi_score
        return matrix

    def to_dict(self) -> dict[str, Any]:
        """Serialize grid to dict for HA storage."""
        return {
            "floor_id": self.floor_id,
            "width_m": self.width_m,
            "height_m": self.height_m,
            "resolution_m": self.resolution_m,
            "ap_positions": {k: list(v) for k, v in self._ap_positions.items()},
            "csi_positions": {k: list(v) for k, v in self._csi_positions.items()},
            "room_labels": self.room_labels,
            "ap_markers": self.ap_markers,
            "cells": [cell.to_dict() for cell in self._cells.values()],
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> SpatialGrid:
        """Deserialize grid from storage dict."""
        grid = cls(
            floor_id=data["floor_id"],
            width_m=data.get("width_m", 10.0),
            height_m=data.get("height_m", 10.0),
            resolution_m=data.get("resolution_m", 0.5),
        )
        for ap_mac, pos in data.get("ap_positions", {}).items():
            grid._ap_positions[ap_mac] = tuple(pos)  # type: ignore[assignment]
        for dev_id, pos in data.get("csi_positions", {}).items():
            grid._csi_positions[dev_id] = tuple(pos)  # type: ignore[assignment]
        grid.room_labels = data.get("room_labels", {})
        grid.ap_markers = data.get("ap_markers", {})
        for cell_data in data.get("cells", []):
            cell = GridCell.from_dict(cell_data)
            grid._cells[(cell.x, cell.y)] = cell
        return grid

    def _get_or_create_cell(self, col: int, row: int) -> GridCell:
        key = (col, row)
        if key not in self._cells:
            self._cells[key] = GridCell(x=col, y=row)
        return self._cells[key]
