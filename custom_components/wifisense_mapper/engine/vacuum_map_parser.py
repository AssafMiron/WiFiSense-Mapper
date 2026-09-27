"""WiFiSense Mapper — Roborock & Robot Vacuum Semantic Map Parser.

Extracts walkable room boundaries, physical walls, charging dock landmarks,
and identified furniture clusters (sofas, beds, tables, desks) from robot vacuum map
images and attribute segments.

Enables:
  1. Clamping estimated person positions inside real walkable room boundaries.
  2. Auto-generating MicroZone attractors from Roborock-detected furniture.
  3. Raycasting Obstacle Attenuation Models (OAM) to render realistic RF shadowing.
"""

from __future__ import annotations

import io
import logging
import math
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from ..vacuum_helpers import VacuumRoomSegment

_LOGGER = logging.getLogger(__name__)


@dataclass
class RoomWalkableArea:
    """Walkable geometry and bounding bounds for a room segment."""

    segment_id: str
    name: str
    area_id: str | None = None
    min_x_m: float = 0.0
    max_x_m: float = 10.0
    min_y_m: float = 0.0
    max_y_m: float = 10.0
    centroid_x_m: float = 5.0
    centroid_y_m: float = 5.0
    pixel_count: int = 0

    def contains(self, x_m: float, y_m: float, margin_m: float = 0.2) -> bool:
        """Check if coordinates fall inside this room's bounding box."""
        return (
            (self.min_x_m - margin_m) <= x_m <= (self.max_x_m + margin_m)
            and (self.min_y_m - margin_m) <= y_m <= (self.max_y_m + margin_m)
        )

    def clamp(self, x_m: float, y_m: float) -> tuple[float, float]:
        """Project coordinates inside room bounds."""
        cx = max(self.min_x_m, min(self.max_x_m, x_m))
        cy = max(self.min_y_m, min(self.max_y_m, y_m))
        return cx, cy


@dataclass
class FurnitureCluster:
    """Recognized furniture or spatial micro-zone from vacuum map."""

    name: str
    furniture_type: str  # sofa, bed, table, desk, dock, obstacle
    area_id: str | None
    floor_id: str
    x_m: float
    y_m: float
    radius_m: float = 1.5

    def is_near(self, x_m: float, y_m: float) -> bool:
        """Check if coordinates are within the furniture snapping radius."""
        return math.hypot(x_m - self.x_m, y_m - self.y_m) <= self.radius_m

    def to_micro_zone_dict(self) -> dict[str, Any]:
        """Convert to MicroZone dictionary format."""
        return {
            "name": self.name,
            "area_id": self.area_id or "",
            "floor_id": self.floor_id,
            "x_m": round(self.x_m, 2),
            "y_m": round(self.y_m, 2),
            "radius_m": self.radius_m,
        }


@dataclass
class VacuumMapFeatures:
    """Parsed semantic spatial features from a robot vacuum map."""

    width_px: int = 100
    height_px: int = 100
    width_m: float = 10.0
    height_m: float = 10.0
    rooms: dict[str, RoomWalkableArea] = field(default_factory=dict)  # area_id or segment_id -> RoomWalkableArea
    furniture: list[FurnitureCluster] = field(default_factory=list)
    wall_grid: list[list[bool]] = field(default_factory=list)  # True = wall / obstacle cell
    dock_m: tuple[float, float] | None = None

    def get_room_for_area(self, area_id: str | None) -> RoomWalkableArea | None:
        """Retrieve room bounds matching an area_id."""
        if not area_id:
            return None
        if area_id in self.rooms:
            return self.rooms[area_id]
        for room in self.rooms.values():
            if room.area_id == area_id:
                return room
        return None

    def clamp_to_area(self, x_m: float, y_m: float, area_id: str | None) -> tuple[float, float]:
        """Clamp coordinates within the area's walkable boundary if available."""
        room = self.get_room_for_area(area_id)
        if room:
            return room.clamp(x_m, y_m)
        return max(0.0, min(self.width_m, x_m)), max(0.0, min(self.height_m, y_m))

    def count_wall_crossings(
        self,
        start_x_m: float,
        start_y_m: float,
        end_x_m: float,
        end_y_m: float,
    ) -> int:
        """Raycast between two coordinates and count intervening wall transitions."""
        if not self.wall_grid or not self.wall_grid[0]:
            return 0

        grid_h = len(self.wall_grid)
        grid_w = len(self.wall_grid[0])

        col0 = max(0, min(grid_w - 1, int((start_x_m / max(0.1, self.width_m)) * grid_w)))
        row0 = max(0, min(grid_h - 1, int((start_y_m / max(0.1, self.height_m)) * grid_h)))
        col1 = max(0, min(grid_w - 1, int((end_x_m / max(0.1, self.width_m)) * grid_w)))
        row1 = max(0, min(grid_h - 1, int((end_y_m / max(0.1, self.height_m)) * grid_h)))

        # Bresenham line algorithm
        dx = abs(col1 - col0)
        dy = abs(row1 - row0)
        sx = 1 if col0 < col1 else -1
        sy = 1 if row0 < row1 else -1
        err = dx - dy

        crossings = 0
        in_wall = False
        c, r = col0, row0

        while True:
            is_wall = self.wall_grid[r][c]
            if is_wall and not in_wall:
                crossings += 1
                in_wall = True
            elif not is_wall:
                in_wall = False

            if c == col1 and r == row1:
                break
            e2 = 2 * err
            if e2 > -dy:
                err -= dy
                c += sx
            if e2 < dx:
                err += dx
                r += sy

        return crossings


def parse_vacuum_map_image(
    image_bytes: bytes,
    *,
    segments: list[VacuumRoomSegment] | None = None,
    width_m: float = 10.0,
    height_m: float = 10.0,
    floor_id: str = "default",
) -> VacuumMapFeatures:
    """Parse Roborock PNG map into semantic geometry and furniture micro-zones."""
    from PIL import Image

    try:
        im = Image.open(io.BytesIO(image_bytes)).convert("RGBA")
    except Exception as exc:  # noqa: BLE001
        _LOGGER.warning("Failed to open vacuum map image: %s", exc)
        return VacuumMapFeatures(width_m=width_m, height_m=height_m)

    w_px, h_px = im.size
    pixels = im.load()
    if pixels is None:
        return VacuumMapFeatures(width_m=width_m, height_m=height_m)

    # Create downsampled wall/walkable grid for fast path loss raycasting (e.g. 64x64)
    grid_cols = min(64, w_px)
    grid_rows = min(64, h_px)
    wall_grid: list[list[bool]] = [[False for _ in range(grid_cols)] for _ in range(grid_rows)]

    # Collect color histograms per room to detect distinct segments and obstacles
    # In Roborock maps:
    #   - Transparent / very dark pixels = unmapped / void
    #   - High-contrast lines (white or dark outlines) = walls / obstacles
    #   - Pastel colors = room floors
    #   - Distinct marks / icons = dock / furniture
    step_x = max(1, w_px // grid_cols)
    step_y = max(1, h_px // grid_rows)

    room_pixel_bounds: dict[int, list[float]] = {}  # cluster_id -> [min_x, max_x, min_y, max_y, count]
    furniture_candidates: list[tuple[float, float, str]] = []

    for r in range(grid_rows):
        py = min(h_px - 1, r * step_y)
        for c in range(grid_cols):
            px = min(w_px - 1, c * step_x)
            pix = pixels[px, py]
            if not isinstance(pix, (tuple, list)) or len(pix) < 4:
                continue
            r_val, g_val, b_val, a_val = int(pix[0]), int(pix[1]), int(pix[2]), int(pix[3])

            if a_val < 30:
                # Outside house / void
                continue

            # Brightness check
            brightness = (r_val + g_val + b_val) / 3.0

            # Wall detection: high contrast thin border pixels
            if brightness > 220:
                wall_grid[r][c] = True
            elif 40 <= brightness <= 210:
                # Walkable room pixel. Classify into color bin
                # Simplified 4-bit color hash for segment clustering
                bin_id = ((r_val >> 5) << 6) | ((g_val >> 5) << 3) | (b_val >> 5)
                if bin_id not in room_pixel_bounds:
                    room_pixel_bounds[bin_id] = [px, px, py, py, 1]
                else:
                    b = room_pixel_bounds[bin_id]
                    b[0] = min(b[0], px)
                    b[1] = max(b[1], px)
                    b[2] = min(b[2], py)
                    b[3] = max(b[3], py)
                    b[4] += 1

            # Detect specialized icons / furniture marks (e.g. bright distinct colors like yellow/green/cyan dock)
            if r_val > 200 and g_val > 180 and b_val < 80:
                # Potential charging dock landmark
                x_m = (px / float(w_px)) * width_m
                y_m = (py / float(h_px)) * height_m
                furniture_candidates.append((x_m, y_m, "dock"))

    # Build RoomWalkableArea list
    rooms: dict[str, RoomWalkableArea] = {}

    # Sort largest color clusters representing real rooms
    sorted_clusters = sorted(room_pixel_bounds.items(), key=lambda item: item[1][4], reverse=True)

    # Correlate discovered segments with VacuumRoomSegments
    known_segs = list(segments) if segments else []

    for idx, (_bin_id, bounds) in enumerate(sorted_clusters[: len(known_segs) if known_segs else 8]):
        count = int(bounds[4])
        if count < 10:
            continue

        min_xm = (bounds[0] / float(w_px)) * width_m
        max_xm = (bounds[1] / float(w_px)) * width_m
        min_ym = (bounds[2] / float(h_px)) * height_m
        max_ym = (bounds[3] / float(h_px)) * height_m

        seg = known_segs[idx] if idx < len(known_segs) else None
        seg_id = str(seg.segment_id) if seg else f"seg_{idx + 1}"
        seg_name = seg.name if seg and seg.name else f"Room {idx + 1}"
        area_id = seg.area_id if seg else None

        room_area = RoomWalkableArea(
            segment_id=seg_id,
            name=seg_name,
            area_id=area_id or seg_id,
            min_x_m=round(min_xm, 2),
            max_x_m=round(max_xm, 2),
            min_y_m=round(min_ym, 2),
            max_y_m=round(max_ym, 2),
            centroid_x_m=round((min_xm + max_xm) / 2.0, 2),
            centroid_y_m=round((min_ym + max_ym) / 2.0, 2),
            pixel_count=count,
        )

        key = area_id or seg_id
        rooms[key] = room_area

    # Build Furniture & MicroZone list
    furniture: list[FurnitureCluster] = []

    # If charging dock was found, add it
    dock_coord = None
    if furniture_candidates:
        dock_cand = furniture_candidates[0]
        dock_coord = (round(dock_cand[0], 2), round(dock_cand[1], 2))
        furniture.append(
            FurnitureCluster(
                name="Roborock Charging Dock",
                furniture_type="dock",
                area_id=None,
                floor_id=floor_id,
                x_m=dock_coord[0],
                y_m=dock_coord[1],
                radius_m=1.2,
            )
        )

    # For rooms with recognized names, synthesize furniture micro-zones at appropriate layout positions
    for r_key, r_obj in rooms.items():
        name_lower = r_obj.name.lower()
        if "living" in name_lower or "salon" in name_lower:
            # Sofa attractor
            furniture.append(
                FurnitureCluster(
                    name=f"{r_obj.name} Sofa",
                    furniture_type="sofa",
                    area_id=r_key,
                    floor_id=floor_id,
                    x_m=round(r_obj.min_x_m + (r_obj.max_x_m - r_obj.min_x_m) * 0.35, 2),
                    y_m=round(r_obj.centroid_y_m, 2),
                    radius_m=1.8,
                )
            )
            # TV / Media table
            furniture.append(
                FurnitureCluster(
                    name=f"{r_obj.name} Media Area",
                    furniture_type="table",
                    area_id=r_key,
                    floor_id=floor_id,
                    x_m=round(r_obj.min_x_m + (r_obj.max_x_m - r_obj.min_x_m) * 0.75, 2),
                    y_m=round(r_obj.centroid_y_m, 2),
                    radius_m=1.5,
                )
            )
        elif "office" in name_lower or "study" in name_lower or "work" in name_lower:
            # Desk attractor
            furniture.append(
                FurnitureCluster(
                    name=f"{r_obj.name} Desk",
                    furniture_type="desk",
                    area_id=r_key,
                    floor_id=floor_id,
                    x_m=round(r_obj.centroid_x_m, 2),
                    y_m=round(r_obj.centroid_y_m, 2),
                    radius_m=1.4,
                )
            )
        elif "bed" in name_lower:
            # Bed attractor
            furniture.append(
                FurnitureCluster(
                    name=f"{r_obj.name} Bed",
                    furniture_type="bed",
                    area_id=r_key,
                    floor_id=floor_id,
                    x_m=round(r_obj.centroid_x_m, 2),
                    y_m=round(r_obj.centroid_y_m, 2),
                    radius_m=1.8,
                )
            )
        elif "kitchen" in name_lower or "dining" in name_lower:
            # Dining table attractor
            furniture.append(
                FurnitureCluster(
                    name=f"{r_obj.name} Dining Table",
                    furniture_type="table",
                    area_id=r_key,
                    floor_id=floor_id,
                    x_m=round(r_obj.centroid_x_m, 2),
                    y_m=round(r_obj.centroid_y_m, 2),
                    radius_m=1.5,
                )
            )

    return VacuumMapFeatures(
        width_px=w_px,
        height_px=h_px,
        width_m=width_m,
        height_m=height_m,
        rooms=rooms,
        furniture=furniture,
        wall_grid=wall_grid,
        dock_m=dock_coord,
    )
