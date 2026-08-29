"""WiFiSense Mapper — Vacuum Map Aligner.

Computes an affine transform (translation + rotation + scale) between:
  - Vacuum pixel coordinates: (px, py) in the vacuum's map image space
  - WiFi grid coordinates: (col, row) in SpatialGrid cell space

Calibration requires at least 3 non-collinear point correspondences.
The user provides these via the ``calibrate_vacuum_map`` service, either
manually (clicking corresponding points in both coordinate systems) or
via the config flow alignment helper.

Math:
  We use a 2D affine transform (6 degrees of freedom):
    [x']   [a  b  tx] [x]
    [y'] = [c  d  ty] [y]
    [1 ]   [0  0  1 ] [1]

  With ≥3 point pairs we solve for (a, b, c, d, tx, ty) using
  least-squares (pure Python, no numpy required).

Limitations:
  - Vacuum map pixel coordinates vary by manufacturer and firmware.
  - Some vacuums use a non-standard origin (bottom-left vs top-left).
  - Multi-floor vacuums require a separate calibration per floor.
  - Calibration drift: if the vacuum re-generates its map, pixel
    coordinates may shift and recalibration will be needed.
"""

from __future__ import annotations

import logging
import math
from dataclasses import dataclass
from typing import Any

_LOGGER = logging.getLogger(__name__)


@dataclass
class CalibrationPoint:
    """A single correspondence: vacuum pixel ↔ WiFi grid coordinate."""

    vac_px: float
    """X pixel in vacuum map image."""

    vac_py: float
    """Y pixel in vacuum map image."""

    grid_col: float
    """Column in WiFi SpatialGrid."""

    grid_row: float
    """Row in WiFi SpatialGrid."""


@dataclass
class AffineTransform:
    """6-parameter 2D affine transform."""

    a: float = 1.0
    b: float = 0.0
    tx: float = 0.0
    c: float = 0.0
    d: float = 1.0
    ty: float = 0.0

    def transform_point(self, px: float, py: float) -> tuple[float, float]:
        """Map a vacuum pixel (px, py) to WiFi grid (col, row)."""
        col = self.a * px + self.b * py + self.tx
        row = self.c * px + self.d * py + self.ty
        return (col, row)

    def to_dict(self) -> dict[str, float]:
        return {
            "a": self.a,
            "b": self.b,
            "tx": self.tx,
            "c": self.c,
            "d": self.d,
            "ty": self.ty,
        }

    @classmethod
    def from_dict(cls, data: dict[str, float]) -> AffineTransform:
        return cls(
            a=data.get("a", 1.0),
            b=data.get("b", 0.0),
            tx=data.get("tx", 0.0),
            c=data.get("c", 0.0),
            d=data.get("d", 1.0),
            ty=data.get("ty", 0.0),
        )


def _solve_affine(
    points: list[CalibrationPoint],
) -> AffineTransform | None:
    """Compute affine transform from ≥3 calibration points using least squares.

    Solves the over-determined system [px, py, 1] * [a, b, tx]^T = grid_col
    and the same for (c, d, ty) → grid_row.

    Pure-Python least squares using normal equations.
    """
    n = len(points)
    if n < 3:
        _LOGGER.warning("Cannot compute affine transform: need ≥3 points, got %d", n)
        return None

    # Build matrix A (n × 3) and vectors bx, by
    A: list[list[float]] = [[p.vac_px, p.vac_py, 1.0] for p in points]
    bx = [p.grid_col for p in points]
    by = [p.grid_row for p in points]

    def dot(u: list[float], v: list[float]) -> float:
        return sum(ui * vi for ui, vi in zip(u, v))

    def mat_vec_dot(M: list[list[float]], v: list[float]) -> list[float]:
        return [dot(row, v) for row in M]

    def mat_T(M: list[list[float]]) -> list[list[float]]:
        rows = len(M)
        cols = len(M[0])
        return [[M[r][c] for r in range(rows)] for c in range(cols)]

    def mat_mul(M: list[list[float]], N: list[list[float]]) -> list[list[float]]:
        rows = len(M)
        inner = len(N)
        cols = len(N[0])
        result = [[0.0] * cols for _ in range(rows)]
        for i in range(rows):
            for k in range(inner):
                for j in range(cols):
                    result[i][j] += M[i][k] * N[k][j]
        return result

    def solve_3x3(M: list[list[float]], b: list[float]) -> list[float] | None:
        """Gaussian elimination for 3×3 system Mx = b."""
        # Augmented matrix
        aug = [row[:] + [b[i]] for i, row in enumerate(M)]
        for col in range(3):
            # Pivot
            max_row = max(range(col, 3), key=lambda r: abs(aug[r][col]))
            aug[col], aug[max_row] = aug[max_row], aug[col]
            if abs(aug[col][col]) < 1e-12:
                return None
            pivot = aug[col][col]
            for i in range(col + 1, 3):
                factor = aug[i][col] / pivot
                for j in range(col, 4):
                    aug[i][j] -= factor * aug[col][j]
        # Back substitution
        x = [0.0, 0.0, 0.0]
        for i in range(2, -1, -1):
            x[i] = aug[i][3]
            for j in range(i + 1, 3):
                x[i] -= aug[i][j] * x[j]
            x[i] /= aug[i][i]
        return x

    AT = mat_T(A)
    ATA = mat_mul(AT, A)  # 3×3
    ATbx = mat_vec_dot(AT, bx)
    ATby = mat_vec_dot(AT, by)

    params_x = solve_3x3(ATA, ATbx)
    params_y = solve_3x3(ATA, ATby)

    if params_x is None or params_y is None:
        _LOGGER.error(
            "Affine transform: singular matrix — collinear calibration points?"
        )
        return None

    return AffineTransform(
        a=params_x[0],
        b=params_x[1],
        tx=params_x[2],
        c=params_y[0],
        d=params_y[1],
        ty=params_y[2],
    )


class VacuumMapAligner:
    """Manages calibration between vacuum pixel space and WiFi grid coordinates."""

    def __init__(self, floor_id: str) -> None:
        self.floor_id = floor_id
        self._calibration_points: list[CalibrationPoint] = []
        self._transform: AffineTransform | None = None

    @property
    def is_calibrated(self) -> bool:
        """True if a valid transform has been computed."""
        return self._transform is not None

    def add_calibration_point(
        self, vac_px: float, vac_py: float, grid_col: float, grid_row: float
    ) -> None:
        """Add a correspondence point and recompute the transform."""
        self._calibration_points.append(
            CalibrationPoint(
                vac_px=vac_px, vac_py=vac_py, grid_col=grid_col, grid_row=grid_row
            )
        )
        if len(self._calibration_points) >= 3:
            self._transform = _solve_affine(self._calibration_points)
            if self._transform:
                _LOGGER.info(
                    "Vacuum aligner for floor %s calibrated with %d points",
                    self.floor_id,
                    len(self._calibration_points),
                )

    def transform_point(
        self, vac_px: float, vac_py: float
    ) -> tuple[float, float] | None:
        """Map vacuum pixel → WiFi grid (col, row). Returns None if not calibrated."""
        if self._transform is None:
            return None
        return self._transform.transform_point(vac_px, vac_py)

    def calibration_residual(self) -> float:
        """Return mean reprojection error over calibration points (in grid cells)."""
        if self._transform is None or not self._calibration_points:
            return float("inf")
        errors = []
        for pt in self._calibration_points:
            ec, er = self._transform.transform_point(pt.vac_px, pt.vac_py)
            errors.append(math.sqrt((ec - pt.grid_col) ** 2 + (er - pt.grid_row) ** 2))
        return sum(errors) / len(errors)

    def reset_calibration(self) -> None:
        """Clear all calibration points and transform."""
        self._calibration_points.clear()
        self._transform = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "floor_id": self.floor_id,
            "calibration_points": [
                {
                    "vac_px": p.vac_px,
                    "vac_py": p.vac_py,
                    "grid_col": p.grid_col,
                    "grid_row": p.grid_row,
                }
                for p in self._calibration_points
            ],
            "transform": self._transform.to_dict() if self._transform else None,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> VacuumMapAligner:
        aligner = cls(floor_id=data["floor_id"])
        for pt in data.get("calibration_points", []):
            aligner._calibration_points.append(CalibrationPoint(**pt))
        if data.get("transform"):
            aligner._transform = AffineTransform.from_dict(data["transform"])
        return aligner
