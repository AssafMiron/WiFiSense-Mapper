# Spatial Engine Plan — Obstacle-Aware Grid & Multi-AP Fusion

## 1. Roborock S8 MaxV Map Image & Obstacle Parsing

### 1.1 Image Processing in Executor (`engine/vacuum_map_parser.py`)
- Reads PNG bytes from `image.*_roborock_map`.
- Roborock S8 MaxV maps contain:
  - Background (transparent or dark gray: `#000000` / `#121212`).
  - Room fills (distinct pastel RGB hues per segment).
  - Physical Walls & Obstacles (white / high-contrast boundary pixels).
  - Furniture icons/contours (identified beds, sofas, tables).
- Extracts:
  - `walkable_mask`: 2D binary grid indicating walkable space.
  - `room_bounds`: Bounding boxes and convex hulls for each identified area.
  - `furniture_clusters`: Centroid and bounding radii of detected furniture pieces.

---

## 2. Multi-AP Triangulation & Clamping

### 2.1 Fixed Deco Anchors
Each of the 3 Deco units has a calibrated physical position:
$$P_i = (x_i, y_i) \quad \text{for } i \in \{1, 2, 3\}$$

### 2.2 Path Loss with Obstacle Attenuation Model (OAM)
$$RSSI(d) = P_{tx} - 10 \cdot n \cdot \log_{10}(d) - \sum_{k=1}^M W_k$$
where:
- $W_k$ is the wall attenuation factor (typically 3 to 6 dB per wall crossed).
- Distance $d_i$ from Deco $i$ is calculated using the inverted formula with obstacle compensation.

### 2.3 Constrained Kalman Filter & Snapping
1. **Trilateration / Weighted Centroid**:
   Using distance estimates from connected Deco (primary) and neighbor Decos:
   $$z = \arg\min_{(x,y) \in \text{Room}} \sum_{i} w_i \left(\sqrt{(x-x_i)^2 + (y-y_i)^2} - d_i\right)^2$$
2. **Boundary Clamping**:
   Projects $(x, y)$ inside the `walkable_mask` of the connected room so coordinates never land in exterior walls.
3. **Micro-Zone Snapping**:
   If distance to any furniture cluster (e.g. Sofa) is within the snapping radius $R_{snap} \approx 1.5\text{m}$ and velocity is stationary, lock to that micro-zone.

---

## 3. Obstacle-Aware Heatmap Rendering
- The `HeatmapRenderer` combines RF signal decay with a 2D Bresenham raycaster.
- Walls cast realistic RF shadows, highlighting real dead spots and avoiding naive concentric circles.
