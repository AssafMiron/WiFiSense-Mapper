"""Tests for the spatial engine: grid, heatmap, baseline, vacuum aligner."""

from __future__ import annotations

import time

import pytest

from custom_components.wifisense_mapper.engine.baseline import (
    BaselineLearner,
)
from custom_components.wifisense_mapper.engine.grid import SpatialGrid
from custom_components.wifisense_mapper.engine.heatmap import (
    HeatmapRenderer,
    _interpolate_idw,
    _normalize,
)
from custom_components.wifisense_mapper.engine.vacuum_align import (
    VacuumMapAligner,
)

# ─── Grid tests ────────────────────────────────────────────────────────────────


class TestSpatialGrid:
    def test_grid_creation(self):
        grid = SpatialGrid(floor_id="g1", width_m=10, height_m=5, resolution_m=0.5)
        assert grid.cols == 20
        assert grid.rows == 10
        assert grid.floor_id == "g1"

    def test_rssi_update_creates_cells(self):
        grid = SpatialGrid("g1", width_m=10, height_m=10, resolution_m=1.0)
        grid.set_ap_position("aa:bb:cc", 5.0, 5.0)
        grid.update_rssi("aa:bb:cc", "client_mac", rssi=-60)
        assert len(grid.all_cells()) > 0

    def test_rssi_value_at_ap_position(self):
        grid = SpatialGrid("g1", width_m=10, height_m=10, resolution_m=1.0)
        grid.set_ap_position("aa:bb:cc", 3.0, 3.0)  # → cell (3, 3)
        grid.update_rssi("aa:bb:cc", "client", rssi=-50, spread_cells=0)
        cell = grid.get_cell(3, 3)
        assert cell is not None
        assert cell.mean_rssi == pytest.approx(-50, abs=5)

    def test_csi_update_propagates(self):
        grid = SpatialGrid("g1", width_m=10, height_m=10, resolution_m=1.0)
        grid.set_csi_position("node_1", 2.0, 2.0)
        grid.update_csi_score("node_1", score=75.0, spread_cells=2)
        cell = grid.get_cell(2, 2)
        assert cell is not None
        assert cell.mean_csi_score is not None
        assert cell.mean_csi_score > 0

    def test_prune_old_samples(self):
        grid = SpatialGrid("g1", width_m=5, height_m=5, resolution_m=1.0)
        grid.set_ap_position("aa:bb:cc", 2.0, 2.0)
        grid.update_rssi("aa:bb:cc", "c1", rssi=-60, spread_cells=0)
        # Manually age the sample
        cell = grid.get_cell(2, 2)
        cell.rssi_samples = [(time.time() - 7200, -60)]  # 2 hours old
        grid.prune_old_samples()
        assert len(cell.rssi_samples) == 0

    def test_serialization_roundtrip(self):
        grid = SpatialGrid("g1", width_m=10, height_m=10, resolution_m=1.0)
        grid.set_ap_position("aa:bb:cc", 5.0, 5.0)
        grid.update_rssi("aa:bb:cc", "c1", rssi=-65)

        data = grid.to_dict()
        restored = SpatialGrid.from_dict(data)

        assert restored.floor_id == grid.floor_id
        assert restored.cols == grid.cols
        assert len(restored.all_cells()) == len(grid.all_cells())


# ─── Heatmap tests ─────────────────────────────────────────────────────────────


class TestIDWInterpolation:
    def test_known_cells_preserved(self):
        matrix = [[None, None], [None, 5.0]]
        result = _interpolate_idw(matrix, rows=2, cols=2)
        assert result[1][1] == pytest.approx(5.0, abs=0.01)

    def test_empty_matrix_returns_zeros(self):
        matrix = [[None, None], [None, None]]
        result = _interpolate_idw(matrix, rows=2, cols=2)
        assert result[0][0] == 0.0

    def test_interpolated_value_between_known(self):
        # Two known values at corners; midpoint should be interpolated
        matrix = [[10.0, None], [None, 20.0]]
        result = _interpolate_idw(matrix, rows=2, cols=2)
        mid = result[0][1]
        assert 10.0 <= mid <= 20.0 or 20.0 <= mid <= 10.0


class TestNormalize:
    def test_normalizes_to_0_1(self):
        matrix = [[0.0, 50.0], [100.0, 25.0]]
        result = _normalize(matrix, rows=2, cols=2)
        assert result[0][0] == pytest.approx(0.0)
        assert result[1][0] == pytest.approx(1.0)
        assert all(0.0 <= result[r][c] <= 1.0 for r in range(2) for c in range(2))

    def test_explicit_vmin_vmax(self):
        matrix = [[-100.0, -50.0], [-30.0, -70.0]]
        result = _normalize(matrix, rows=2, cols=2, vmin=-100.0, vmax=-30.0)
        assert result[0][0] == pytest.approx(0.0, abs=0.01)
        assert result[1][0] == pytest.approx(1.0, abs=0.01)


class TestHeatmapRenderer:
    def test_render_signal_returns_bytes(self):
        grid = SpatialGrid("g1", width_m=5, height_m=5, resolution_m=1.0)
        grid.set_ap_position("aa:bb:cc", 2.0, 2.0)
        grid.update_rssi("aa:bb:cc", "c1", rssi=-60)

        renderer = HeatmapRenderer(cell_scale=2)
        result = renderer.render_signal(grid)
        assert isinstance(result, bytes)
        assert len(result) > 0

    def test_render_empty_grid(self):
        grid = SpatialGrid("g1", width_m=5, height_m=5, resolution_m=1.0)
        renderer = HeatmapRenderer(cell_scale=2)
        result = renderer.render_signal(grid)
        assert isinstance(result, bytes)


# ─── Baseline learner tests ────────────────────────────────────────────────────


class TestBaselineLearner:
    def _make_stable_grid(self) -> SpatialGrid:
        grid = SpatialGrid("g1", width_m=5, height_m=5, resolution_m=1.0)
        grid.set_ap_position("aa:bb", 2.0, 2.0)
        return grid

    def test_warmed_up_after_100_samples(self):
        learner = BaselineLearner("g1")
        grid = self._make_stable_grid()

        for _ in range(100):
            grid.update_rssi("aa:bb", "c1", rssi=-60, spread_cells=0)
            learner.update_from_grid(grid)

        assert learner.is_warmed_up is True

    def test_not_warmed_up_initially(self):
        learner = BaselineLearner("g1")
        assert learner.is_warmed_up is False

    def test_no_anomaly_on_stable_signal(self):
        learner = BaselineLearner("g1", alpha=0.3)
        grid = self._make_stable_grid()

        for _ in range(120):
            grid.update_rssi("aa:bb", "c1", rssi=-60, spread_cells=0)
            learner.update_from_grid(grid)

        scores = learner.compute_anomaly_scores(grid)
        assert not learner.is_anomaly(scores, threshold=3.0)

    def test_anomaly_on_sudden_change(self):
        learner = BaselineLearner("g1", alpha=0.3)
        grid = self._make_stable_grid()

        # Learn stable baseline
        for _ in range(120):
            grid.update_rssi("aa:bb", "c1", rssi=-60, spread_cells=0)
            learner.update_from_grid(grid)

        # Inject a drastic change
        for _ in range(5):
            grid.update_rssi("aa:bb", "c1", rssi=-30, spread_cells=0)
            learner.update_from_grid(grid)

        scores = learner.compute_anomaly_scores(grid)
        assert learner.max_anomaly_score(scores) > 1.0  # Should be elevated

    def test_serialization_roundtrip(self):
        learner = BaselineLearner("g1")
        grid = self._make_stable_grid()
        for _ in range(10):
            grid.update_rssi("aa:bb", "c1", rssi=-60, spread_cells=0)
            learner.update_from_grid(grid)

        data = learner.to_dict()
        restored = BaselineLearner.from_dict(data)
        assert restored.floor_id == learner.floor_id
        assert restored._sample_count == learner._sample_count

    def test_reset_clears_state(self):
        learner = BaselineLearner("g1")
        grid = self._make_stable_grid()
        for _ in range(50):
            learner.update_from_grid(grid)
        learner.reset()
        assert not learner.is_warmed_up
        assert learner._sample_count == 0


# ─── Vacuum aligner tests ──────────────────────────────────────────────────────


class TestVacuumMapAligner:
    def _calibrated_aligner(self) -> VacuumMapAligner:
        """Return aligner with 4 calibration points (identity-like transform)."""
        aligner = VacuumMapAligner("g1")
        # Calibration: vacuum pixel (x, y) → grid (col, row) at scale 0.1
        pts = [
            (0, 0, 0, 0),
            (100, 0, 10, 0),
            (0, 100, 0, 10),
            (100, 100, 10, 10),
        ]
        for vx, vy, gc, gr in pts:
            aligner.add_calibration_point(vx, vy, gc, gr)
        return aligner

    def test_calibration_succeeds_with_4_points(self):
        aligner = self._calibrated_aligner()
        assert aligner.is_calibrated

    def test_transform_point_identity_like(self):
        aligner = self._calibrated_aligner()
        col, row = aligner.transform_point(50, 50)
        assert col == pytest.approx(5.0, abs=0.5)
        assert row == pytest.approx(5.0, abs=0.5)

    def test_calibration_residual_near_zero(self):
        aligner = self._calibrated_aligner()
        assert aligner.calibration_residual() < 0.1

    def test_fails_with_fewer_than_3_points(self):
        aligner = VacuumMapAligner("g1")
        aligner.add_calibration_point(0, 0, 0, 0)
        aligner.add_calibration_point(100, 0, 10, 0)
        assert not aligner.is_calibrated

    def test_serialization_roundtrip(self):
        aligner = self._calibrated_aligner()
        data = aligner.to_dict()
        restored = VacuumMapAligner.from_dict(data)
        assert restored.is_calibrated
        col, _ = restored.transform_point(50, 50)
        assert col == pytest.approx(5.0, abs=0.5)
