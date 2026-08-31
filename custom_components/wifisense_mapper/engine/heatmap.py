"""WiFiSense Mapper — Heatmap Renderer.

Generates PNG heatmap images from SpatialGrid data for each floor.
Supports four layers:
  - signal:   Mean RSSI per cell (dBm, −100 to −30)
  - variance: RSSI variance per cell (higher = more signal instability,
              indicating a possible obstacle or high-traffic zone)
  - motion:   Mean CSI motion score per cell
  - anomaly:  Anomaly z-score per cell (from BaselineLearner)

Implementation strategy:
  1. Extract grid values as a rows×cols matrix.
  2. Interpolate missing cells using Inverse Distance Weighting (IDW).
     IDW is chosen over Kriging because it requires no assumptions about
     spatial covariance and runs in pure Python without heavy dependencies.
  3. Normalize values to 0–255 range.
  4. Apply a colormap (blue→green→red by default).
  5. Render to PNG bytes using Pillow (if installed) or a minimal BMP
     fallback if Pillow is unavailable.

All rendering runs in an executor thread — never blocks the event loop.
"""

from __future__ import annotations

import logging
import math
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .grid import SpatialGrid

_LOGGER = logging.getLogger(__name__)

# Colormaps: name → list of (r, g, b) anchor points (0–255)
COLORMAPS: dict[str, list[tuple[int, int, int]]] = {
    "thermal": [
        (0, 0, 131),  # deep blue  (0.0)
        (0, 60, 255),  # blue       (0.2)
        (0, 255, 255),  # cyan       (0.4)
        (0, 200, 0),  # green      (0.5)
        (255, 255, 0),  # yellow     (0.7)
        (255, 60, 0),  # orange     (0.85)
        (128, 0, 0),  # dark red   (1.0)
    ],
    "motion": [
        (10, 10, 30),  # dark navy  (0.0 = no motion)
        (0, 80, 180),  # blue       (0.3)
        (0, 200, 200),  # teal       (0.6)
        (255, 200, 0),  # yellow     (0.8)
        (255, 50, 0),  # red-orange (1.0 = high motion)
    ],
    "anomaly": [
        (30, 30, 30),  # dark gray  (0.0 = normal)
        (0, 100, 0),  # dark green (0.3)
        (255, 200, 0),  # yellow     (0.7 = moderate anomaly)
        (255, 0, 0),  # red        (1.0 = high anomaly)
    ],
}
DEFAULT_COLORMAP = "thermal"

# RSSI range for normalization (dBm)
RSSI_MIN = -100
RSSI_MAX = -30


def _interpolate_idw(
    matrix: list[list[float | None]],
    rows: int,
    cols: int,
    power: float = 2.0,
    max_dist: float = 5.0,
) -> list[list[float]]:
    """Inverse Distance Weighting interpolation over a 2D grid.

    Fills None cells using weighted averages from nearby known cells.
    power controls how fast influence drops with distance (higher = more local).
    max_dist is the maximum cell-distance to consider.

    Time complexity: O(rows × cols × known_cells). For typical home-sized
    grids (20×20 = 400 cells) this is negligible even in pure Python.
    """
    # Collect known (col, row, value) triples
    known: list[tuple[int, int, float]] = []
    for r in range(rows):
        for c in range(cols):
            val = matrix[r][c]
            if val is not None:
                known.append((c, r, float(val)))

    result: list[list[float]] = [[0.0] * cols for _ in range(rows)]

    if not known:
        return result

    global_mean = sum(v for _, _, v in known) / len(known)

    for r in range(rows):
        for c in range(cols):
            val = matrix[r][c]
            if val is not None:
                result[r][c] = float(val)
                continue

            # IDW interpolation
            weight_sum = 0.0
            value_sum = 0.0
            for kc, kr, kv in known:
                dist = math.sqrt((c - kc) ** 2 + (r - kr) ** 2)
                if dist == 0:
                    value_sum = kv
                    weight_sum = 1.0
                    break
                if dist > max_dist:
                    continue
                w = 1.0 / (dist**power)
                weight_sum += w
                value_sum += w * kv

            result[r][c] = value_sum / weight_sum if weight_sum > 0 else global_mean

    return result


def _normalize(
    matrix: list[list[float]],
    rows: int,
    cols: int,
    vmin: float | None = None,
    vmax: float | None = None,
) -> list[list[float]]:
    """Normalize matrix values to 0.0–1.0 range."""
    all_vals = [matrix[r][c] for r in range(rows) for c in range(cols)]
    lo = vmin if vmin is not None else min(all_vals, default=0.0)
    hi = vmax if vmax is not None else max(all_vals, default=1.0)
    span = hi - lo or 1.0
    return [[(matrix[r][c] - lo) / span for c in range(cols)] for r in range(rows)]


def _colormap_lookup(
    t: float,
    cmap: list[tuple[int, int, int]],
) -> tuple[int, int, int]:
    """Linear interpolation through colormap anchors for value t in [0, 1]."""
    t = max(0.0, min(1.0, t))
    if len(cmap) == 1:
        return cmap[0]
    n = len(cmap) - 1
    idx = t * n
    lo = int(idx)
    hi = min(lo + 1, n)
    frac = idx - lo
    r = int(cmap[lo][0] + frac * (cmap[hi][0] - cmap[lo][0]))
    g = int(cmap[lo][1] + frac * (cmap[hi][1] - cmap[lo][1]))
    b = int(cmap[lo][2] + frac * (cmap[hi][2] - cmap[lo][2]))
    return (r, g, b)


def _render_png_pillow(
    pixel_data: list[list[tuple[int, int, int]]],
    rows: int,
    cols: int,
    scale: int = 10,
) -> bytes:
    """Render pixel data to PNG bytes using Pillow."""
    from PIL import Image  # type: ignore[import]

    img = Image.new("RGB", (cols * scale, rows * scale))
    for r in range(rows):
        for c in range(cols):
            color = pixel_data[r][c]
            for dy in range(scale):
                for dx in range(scale):
                    img.putpixel((c * scale + dx, r * scale + dy), color)

    import io

    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


def _render_png_pure_python(
    pixel_data: list[list[tuple[int, int, int]]],
    rows: int,
    cols: int,
    scale: int = 10,
) -> bytes:
    """Pure-Python standard-compliant PNG encoder (zero external dependencies).

    Encodes RGB pixel matrix directly into valid PNG bytes using standard zlib and struct.
    Ensures ImageEntity Content-Type: image/png is ALWAYS valid even without Pillow.
    """
    import struct
    import zlib

    w = cols * scale
    h = rows * scale

    # Build raw scanlines with filter byte (0x00 = None) at the start of each row
    raw_scanlines = bytearray()
    for r in range(rows):
        row_bytes = bytearray()
        for c in range(cols):
            color = pixel_data[r][c]
            rgb = bytes([color[0], color[1], color[2]])
            row_bytes.extend(rgb * scale)
        for _ in range(scale):
            raw_scanlines.append(0)  # Filter type 0 (None)
            raw_scanlines.extend(row_bytes)

    # Helper to construct PNG chunks: length (4B) + type (4B) + data + crc32 (4B)
    def png_chunk(chunk_type: bytes, data: bytes) -> bytes:
        length = struct.pack(">I", len(data))
        crc = struct.pack(">I", zlib.crc32(chunk_type + data) & 0xFFFFFFFF)
        return length + chunk_type + data + crc

    # PNG Signature
    png_bytes = bytearray(b"\x89PNG\r\n\x1a\n")

    # IHDR Chunk: width(4), height(4), bit_depth(1), color_type(1)=2 (RGB), comp(1), filter(1), interlace(1)
    ihdr_data = struct.pack(">IIBBBBB", w, h, 8, 2, 0, 0, 0)
    png_bytes.extend(png_chunk(b"IHDR", ihdr_data))

    # IDAT Chunk: compressed image stream
    compressed = zlib.compress(bytes(raw_scanlines), level=6)
    png_bytes.extend(png_chunk(b"IDAT", compressed))

    # IEND Chunk
    png_bytes.extend(png_chunk(b"IEND", b""))

    return bytes(png_bytes)


class HeatmapRenderer:
    """Renders heatmap images from SpatialGrid data.

    All public render methods are designed to be called via
    ``hass.async_add_executor_job(renderer.render_sync, ...)``
    to keep HA's event loop non-blocking.
    """

    def __init__(self, colormap: str = DEFAULT_COLORMAP, cell_scale: int = 10) -> None:
        self.colormap_name = colormap if colormap in COLORMAPS else DEFAULT_COLORMAP
        self.cell_scale = cell_scale
        self._pillow_available: bool | None = None

    @property
    def pillow_available(self) -> bool:
        """Detect Pillow availability (cached after first check)."""
        if self._pillow_available is None:
            try:
                import PIL  # noqa: F401

                self._pillow_available = True
            except ImportError:
                _LOGGER.info(
                    "Pillow (PIL) not installed. Heatmaps will use BMP fallback. "
                    "Install 'Pillow' for better image quality."
                )
                self._pillow_available = False
        return self._pillow_available

    def render_signal(self, grid: SpatialGrid) -> bytes:
        """Render mean RSSI heatmap. Call in executor thread."""
        matrix = grid.to_rssi_matrix()
        return self._render(
            matrix,
            grid.rows,
            grid.cols,
            vmin=RSSI_MIN,
            vmax=RSSI_MAX,
            colormap_name=self.colormap_name,
        )

    def render_variance(self, grid: SpatialGrid) -> bytes:
        """Render RSSI variance heatmap (obstacle shadows). Call in executor."""
        matrix: list[list[float | None]] = [
            [
                (
                    cell.rssi_variance
                    if (cell := grid.get_cell(c, r)) is not None
                    else None
                )
                for c in range(grid.cols)
            ]
            for r in range(grid.rows)
        ]
        return self._render(
            matrix, grid.rows, grid.cols, colormap_name=self.colormap_name
        )

    def render_motion(self, grid: SpatialGrid) -> bytes:
        """Render CSI motion score heatmap. Call in executor."""
        matrix = grid.to_csi_matrix()
        return self._render(matrix, grid.rows, grid.cols, colormap_name="motion")

    def render_anomaly(
        self,
        grid: SpatialGrid,
        anomaly_scores: dict[tuple[int, int], float],
    ) -> bytes:
        """Render anomaly z-score heatmap. Call in executor.

        anomaly_scores: dict mapping (col, row) → z-score from BaselineLearner.
        """
        matrix: list[list[float | None]] = [
            [anomaly_scores.get((c, r)) for c in range(grid.cols)]
            for r in range(grid.rows)
        ]
        return self._render(matrix, grid.rows, grid.cols, colormap_name="anomaly")

    def _render(
        self,
        matrix: list[list[float | None]],
        rows: int,
        cols: int,
        vmin: float | None = None,
        vmax: float | None = None,
        colormap_name: str = DEFAULT_COLORMAP,
    ) -> bytes:
        """Internal render pipeline: IDW → normalize → colormap → PNG."""
        has_known = any(
            matrix[r][c] is not None for r in range(rows) for c in range(cols)
        )
        if not has_known:
            # Neutral dark gray placeholder for unpopulated grids
            neutral_color = (24, 26, 32)
            pixel_data = [[neutral_color for _ in range(cols)] for _ in range(rows)]
            if self.pillow_available:
                return _render_png_pillow(pixel_data, rows, cols, scale=self.cell_scale)
            return _render_png_pure_python(
                pixel_data, rows, cols, scale=self.cell_scale
            )

        interpolated = _interpolate_idw(matrix, rows, cols)
        normalized = _normalize(interpolated, rows, cols, vmin=vmin, vmax=vmax)
        cmap = COLORMAPS[
            colormap_name if colormap_name in COLORMAPS else DEFAULT_COLORMAP
        ]

        pixel_data = [
            [_colormap_lookup(normalized[r][c], cmap) for c in range(cols)]
            for r in range(rows)
        ]

        if self.pillow_available:
            return _render_png_pillow(pixel_data, rows, cols, scale=self.cell_scale)
        return _render_png_pure_python(pixel_data, rows, cols, scale=self.cell_scale)

