"""Geometry checks against hand-computable answers, at knapsack scale."""

from __future__ import annotations

import numpy as np
import pytest

from spraycontrol.analyze import analyze
from spraycontrol.coverage import make_grid, stamp_polyline
from spraycontrol.demo import straight_pass, synthetic_run, to_gpx
from spraycontrol.geo import LocalPlane, haversine_m
from spraycontrol.models import SprayerConfig
from spraycontrol.parsers import parse_track
from spraycontrol.segment import segment_track

SWATH = 1.0
LENGTH = 40.0
CELL = 0.05  # fine enough to resolve a 1 m band in the geometry unit tests


def _plane():
    return LocalPlane(40.0, 32.5)


def test_projection_roundtrip():
    plane = _plane()
    lat, lon = plane.inverse(np.array([0.0, 1234.0]), np.array([0.0, -987.0]))
    x, y = plane.forward(lat, lon)
    assert np.allclose(x, [0.0, 1234.0], atol=1e-6)
    assert np.allclose(y, [0.0, -987.0], atol=1e-6)


def test_projection_matches_haversine():
    """Plane distances should agree with great-circle distance over a plot."""
    plane = _plane()
    lat, lon = plane.inverse(np.array([0.0, 800.0]), np.array([0.0, 600.0]))
    d = haversine_m(lat[0], lon[0], lat[1], lon[1])
    assert d == pytest.approx(1000.0, rel=1e-4)


def test_single_swath_area_is_length_times_width():
    """Square end caps mean one pass covers exactly length x spray width."""
    plane = _plane()
    cfg = SprayerConfig(swath_width_m=SWATH, cell_size_m=CELL)
    xs = np.array([0.0, 0.0])
    ys = np.array([0.0, LENGTH])
    grid = make_grid(xs, ys, plane, cfg)
    mask = np.zeros(grid.shape, dtype=bool)
    stamp_polyline(grid, xs, ys, SWATH / 2, mask)
    area = mask.sum() * grid.cell_area
    assert area == pytest.approx(LENGTH * SWATH, rel=0.06)


def test_adjacent_passes_tile_without_overlap():
    """Passes spaced exactly one spray width apart must not double-count."""
    plane = _plane()
    cfg = SprayerConfig(swath_width_m=SWATH, cell_size_m=CELL)
    grid = make_grid(np.array([0.0, SWATH]), np.array([0.0, LENGTH]), plane, cfg)

    for x in (0.0, SWATH):
        mask = np.zeros(grid.shape, dtype=bool)
        stamp_polyline(grid, np.array([x, x]), np.array([0.0, LENGTH]), SWATH / 2, mask)
        grid.counts += mask

    overlap_area = (grid.counts >= 2).sum() * grid.cell_area
    total_area = (grid.counts > 0).sum() * grid.cell_area
    assert total_area == pytest.approx(2 * LENGTH * SWATH, rel=0.06)
    assert overlap_area < 0.03 * total_area


def test_half_overlapped_passes_report_half_overlap():
    """Two passes half a width apart overlap over half their band."""
    plane = _plane()
    cfg = SprayerConfig(swath_width_m=SWATH, cell_size_m=0.025)
    grid = make_grid(np.array([0.0, SWATH / 2]), np.array([0.0, LENGTH]), plane, cfg)

    for x in (0.0, SWATH / 2):
        mask = np.zeros(grid.shape, dtype=bool)
        stamp_polyline(grid, np.array([x, x]), np.array([0.0, LENGTH]), SWATH / 2, mask)
        grid.counts += mask

    overlap_area = (grid.counts >= 2).sum() * grid.cell_area
    assert overlap_area == pytest.approx(LENGTH * SWATH / 2, rel=0.06)


def test_single_pass_run_end_to_end():
    track = straight_pass(length_m=LENGTH)
    result = analyze(track, SprayerConfig(swath_width_m=SWATH, cell_size_m=CELL), render=False)
    assert result.coverage.sprayed_area_m2 == pytest.approx(LENGTH * SWATH, rel=0.06)
    assert len(result.passes) == 1
    # One tank load, never completed, so the volume is the full-tank upper bound.
    assert len(result.loads) == 1
    assert result.total_volume_l == pytest.approx(18.0)


def test_gpx_roundtrip_preserves_geometry():
    track = straight_pass(length_m=LENGTH)
    reparsed = parse_track(to_gpx(track).encode(), "demo.gpx")
    assert len(reparsed) == len(track)
    assert np.allclose(reparsed.lat, track.lat, atol=1e-7)
    assert np.allclose(reparsed.t - reparsed.t[0], track.t - track.t[0], atol=1e-3)


class TestSyntheticRun:
    """A full walk with one skipped lane and one shifted lane built in."""

    @staticmethod
    def _run(**kwargs):
        track, base, cfg = synthetic_run(**kwargs)
        return analyze(track, cfg, base, render=False), base, cfg

    def test_refills_and_loads(self):
        # 24 lanes, one skipped, refilling every 8 -> 23 worked, 3 loads.
        result, _, _ = self._run()
        assert len(result.loads) == 3
        assert result.refill_count == 2
        # Four stops at the water point: the initial fill, two refills, and the end.
        assert len(result.visits) == 4

    def test_volume_from_refill_count(self):
        result, _, cfg = self._run()
        complete = [load for load in result.loads if load.is_complete]
        assert len(complete) == 2
        assert all(load.volume_l == pytest.approx(cfg.tank_capacity_l) for load in complete)
        # Two full 18 L tanks plus a scaled partial.
        assert 36.0 < result.total_volume_l <= 54.0

    def test_skipped_lane_shows_as_a_gap(self):
        result, _, _ = self._run(skip_passes=(13,), shift_passes={})
        assert result.gaps, "the skipped lane should be reported"
        biggest = result.gaps[0]
        # A whole missed lane is one spray width wide and one lane long.
        assert biggest.max_width_m == pytest.approx(SWATH, abs=0.4)
        assert biggest.area_m2 == pytest.approx(SWATH * 40.0, rel=0.3)

    def test_no_gap_when_nothing_is_skipped(self):
        result, _, _ = self._run(skip_passes=(), shift_passes={})
        gap_area = sum(g.area_m2 for g in result.gaps)
        assert gap_area < 0.02 * result.coverage.field_area_m2

    def test_shifted_lane_shows_as_overlap(self):
        """Shifting one lane 0.4 m into its neighbour overlaps ~0.4 m x 40 m."""
        clean, _, _ = self._run(skip_passes=(), shift_passes={})
        shifted, _, _ = self._run(skip_passes=(), shift_passes={5: -0.4})
        extra = shifted.coverage.overlap_area_m2 - clean.coverage.overlap_area_m2
        assert extra == pytest.approx(0.4 * 40.0, rel=0.4)

    def test_walking_between_lanes_is_not_over_counted(self):
        result, _, _ = self._run()
        # 23 lanes x 40 m x 1 m, plus the short steps across between lanes.
        assert result.coverage.sprayed_area_m2 < 23 * 40.0 * SWATH * 1.4

    def test_survives_gps_noise(self):
        result, _, _ = self._run(noise_m=0.15, skip_passes=(13,), shift_passes={})
        assert len(result.loads) == 3
        assert result.gaps
        assert result.gaps[0].area_m2 == pytest.approx(SWATH * 40.0, rel=0.5)

    def test_slower_fix_rate_still_works(self):
        """A phone logging every several seconds still recovers the structure."""
        result, _, _ = self._run(interval_s=6.0, skip_passes=(13,), shift_passes={})
        assert len(result.loads) == 3
        assert result.gaps


def test_base_radius_excludes_refill_walk():
    track, base, cfg = synthetic_run(interval_s=4.0)
    seg = segment_track(track, cfg, base)
    at_base = seg.seg_state == 3  # PointState.AT_BASE
    assert at_base.any()
    # No lane may start inside the refill radius.
    assert not any(seg.in_base[p.start_idx] for p in seg.passes)


def test_no_base_means_one_load():
    track, _, cfg = synthetic_run(interval_s=6.0)
    result = analyze(track, cfg, base=None, render=False)
    assert len(result.loads) == 1
    assert result.refill_count == 0
    assert any("No base location" in w for w in result.warnings)


def test_plot_polygon_finds_edge_miss():
    """An edge never walked to is invisible to inference but caught by a
    supplied boundary."""
    track, base, cfg = synthetic_run(n_passes=10, skip_passes=(), shift_passes={}, interval_s=4.0)
    plane = LocalPlane(base.lat, base.lon)

    # The worked block is x in ~12..22 m; claim the plot runs east to 30 m.
    ring_xy = [(11.5, -2.0), (30.0, -2.0), (30.0, 42.0), (11.5, 42.0), (11.5, -2.0)]
    ring = []
    for x, y in ring_xy:
        la, lo = plane.inverse(x, y)
        ring.append([float(lo), float(la)])

    inferred = analyze(track, cfg, base, render=False)
    bounded = analyze(track, cfg, base, field_polygons=[ring], render=False)

    assert bounded.coverage.gap_area_m2 > inferred.coverage.gap_area_m2
    # The unworked strip is roughly 8 m x 44 m.
    assert bounded.coverage.gap_area_m2 == pytest.approx(8.0 * 44.0, rel=0.35)


def test_large_time_gap_is_not_bridged():
    # 120 m walk; dropping a third leaves a silence longer than the 45 s max gap.
    long_length = 120.0
    track = straight_pass(length_m=long_length, interval_s=1.0)
    n = len(track)
    keep = np.ones(n, dtype=bool)
    keep[n // 3 : 2 * n // 3] = False
    holed = track.subset(keep)

    result = analyze(holed, SprayerConfig(swath_width_m=SWATH, cell_size_m=CELL), render=False)
    assert result.coverage.sprayed_area_m2 == pytest.approx(long_length * SWATH * 2 / 3, rel=0.12)
    assert any("fix gap" in w for w in result.warnings)
