"""Geometry checks against hand-computable answers."""

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

BOOM = 12.0
LENGTH = 300.0


def _plane():
    return LocalPlane(40.0, 32.5)


def test_projection_roundtrip():
    plane = _plane()
    lat, lon = plane.inverse(np.array([0.0, 1234.0]), np.array([0.0, -987.0]))
    x, y = plane.forward(lat, lon)
    assert np.allclose(x, [0.0, 1234.0], atol=1e-6)
    assert np.allclose(y, [0.0, -987.0], atol=1e-6)


def test_projection_matches_haversine():
    """Plane distances should agree with great-circle distance over a field."""
    plane = _plane()
    lat, lon = plane.inverse(np.array([0.0, 800.0]), np.array([0.0, 600.0]))
    d = haversine_m(lat[0], lon[0], lat[1], lon[1])
    assert d == pytest.approx(1000.0, rel=1e-4)


def test_single_swath_area_is_length_times_boom():
    """Square end caps mean one pass covers exactly length x boom width."""
    plane = _plane()
    cfg = SprayerConfig(boom_width_m=BOOM, cell_size_m=0.5)
    xs = np.array([0.0, 0.0])
    ys = np.array([0.0, LENGTH])
    grid = make_grid(xs, ys, plane, cfg)
    mask = np.zeros(grid.shape, dtype=bool)
    stamp_polyline(grid, xs, ys, BOOM / 2, mask)
    area = mask.sum() * grid.cell_area
    assert area == pytest.approx(LENGTH * BOOM, rel=0.01)


def test_adjacent_passes_tile_without_overlap():
    """Passes spaced exactly one boom width apart must not double-count."""
    plane = _plane()
    cfg = SprayerConfig(boom_width_m=BOOM, cell_size_m=0.5)
    grid = make_grid(np.array([0.0, BOOM]), np.array([0.0, LENGTH]), plane, cfg)

    for x in (0.0, BOOM):
        mask = np.zeros(grid.shape, dtype=bool)
        stamp_polyline(grid, np.array([x, x]), np.array([0.0, LENGTH]), BOOM / 2, mask)
        grid.counts += mask

    overlap_area = (grid.counts >= 2).sum() * grid.cell_area
    total_area = (grid.counts > 0).sum() * grid.cell_area
    assert total_area == pytest.approx(2 * LENGTH * BOOM, rel=0.01)
    assert overlap_area < 0.02 * total_area


def test_half_overlapped_passes_report_half_overlap():
    """Two passes 6 m apart with a 12 m boom overlap over half their width."""
    plane = _plane()
    cfg = SprayerConfig(boom_width_m=BOOM, cell_size_m=0.5)
    grid = make_grid(np.array([0.0, BOOM / 2]), np.array([0.0, LENGTH]), plane, cfg)

    for x in (0.0, BOOM / 2):
        mask = np.zeros(grid.shape, dtype=bool)
        stamp_polyline(grid, np.array([x, x]), np.array([0.0, LENGTH]), BOOM / 2, mask)
        grid.counts += mask

    overlap_area = (grid.counts >= 2).sum() * grid.cell_area
    assert overlap_area == pytest.approx(LENGTH * BOOM / 2, rel=0.02)


def test_single_pass_run_end_to_end():
    track = straight_pass(length_m=LENGTH)
    result = analyze(track, SprayerConfig(boom_width_m=BOOM, cell_size_m=0.5), render=False)
    assert result.coverage.sprayed_area_m2 == pytest.approx(LENGTH * BOOM, rel=0.02)
    assert len(result.passes) == 1
    # One tank load, never completed, so the volume is the full-tank upper bound.
    assert len(result.loads) == 1
    assert result.total_volume_l == pytest.approx(1000.0)


def test_gpx_roundtrip_preserves_geometry():
    track = straight_pass(length_m=LENGTH)
    reparsed = parse_track(to_gpx(track).encode(), "demo.gpx")
    assert len(reparsed) == len(track)
    assert np.allclose(reparsed.lat, track.lat, atol=1e-7)
    assert np.allclose(reparsed.t - reparsed.t[0], track.t - track.t[0], atol=1e-3)


class TestSyntheticRun:
    """A full run with one skipped pass and one 5 m overlap built in."""

    @staticmethod
    def _run(**kwargs):
        track, base, cfg = synthetic_run(**kwargs)
        cfg.cell_size_m = 0.5
        return analyze(track, cfg, base, render=False), base, cfg

    def test_refills_and_loads(self):
        # 20 passes, one skipped, refilling every 7 -> 19 worked, 3 loads.
        result, _, _ = self._run()
        assert len(result.loads) == 3
        assert result.refill_count == 2
        # Four base dwells: the initial fill, two refills, and parking up.
        assert len(result.visits) == 4

    def test_volume_from_refill_count(self):
        result, _, cfg = self._run()
        complete = [load for load in result.loads if load.is_complete]
        assert len(complete) == 2
        assert all(load.volume_l == pytest.approx(cfg.tank_capacity_l) for load in complete)
        # Two full tanks plus a scaled partial.
        assert 2000.0 < result.total_volume_l <= 3000.0

    def test_skipped_pass_shows_as_a_gap(self):
        result, _, _ = self._run(skip_passes=(11,), shift_passes={})
        assert result.gaps, "the skipped pass should be reported"
        biggest = result.gaps[0]
        # A whole missed pass is one boom wide and one pass long.
        assert biggest.max_width_m == pytest.approx(BOOM, abs=2.0)
        assert biggest.area_m2 == pytest.approx(BOOM * 300.0, rel=0.25)

    def test_no_gap_when_nothing_is_skipped(self):
        result, _, _ = self._run(skip_passes=(), shift_passes={})
        gap_area = sum(g.area_m2 for g in result.gaps)
        assert gap_area < 0.01 * result.coverage.field_area_m2

    def test_shifted_pass_shows_as_overlap(self):
        """Shifting one pass 5 m into its neighbour overlaps ~5 m x 300 m."""
        clean, _, _ = self._run(skip_passes=(), shift_passes={})
        shifted, _, _ = self._run(skip_passes=(), shift_passes={4: -5.0})
        extra = shifted.coverage.overlap_area_m2 - clean.coverage.overlap_area_m2
        assert extra == pytest.approx(5.0 * 300.0, rel=0.3)

    def test_transport_is_not_sprayed(self):
        """Driving to the yard at 25 km/h must not paint a swath."""
        result, _, _ = self._run()
        assert result.transport_distance_m > 1000.0
        # 19 passes x 300 m x 12 m, plus headland turns.
        assert result.coverage.sprayed_area_m2 < 19 * 300.0 * BOOM * 1.35

    def test_survives_gps_noise(self):
        result, _, _ = self._run(noise_m=1.5, skip_passes=(11,), shift_passes={})
        assert len(result.loads) == 3
        assert result.gaps
        assert result.gaps[0].area_m2 == pytest.approx(BOOM * 300.0, rel=0.4)

    def test_sparse_fixes_still_work(self):
        """The HA Companion app can report as slowly as every 30 s."""
        result, _, _ = self._run(interval_s=30.0, skip_passes=(11,), shift_passes={})
        assert len(result.loads) == 3
        assert result.gaps


def test_base_radius_excludes_yard_movement():
    track, base, cfg = synthetic_run(interval_s=5.0)
    seg = segment_track(track, cfg, base)
    at_base = seg.seg_state == 3  # PointState.AT_BASE
    assert at_base.any()
    # No pass may start inside the base radius.
    assert not any(seg.in_base[p.start_idx] for p in seg.passes)


def test_no_base_means_one_load():
    track, _, cfg = synthetic_run(interval_s=10.0)
    result = analyze(track, cfg, base=None, render=False)
    assert len(result.loads) == 1
    assert result.refill_count == 0
    assert any("No base location" in w for w in result.warnings)


def test_field_polygon_finds_edge_miss():
    """An edge never approached is invisible to inference but caught by a
    supplied boundary."""
    track, base, cfg = synthetic_run(n_passes=10, skip_passes=(), shift_passes={}, interval_s=5.0)
    cfg.cell_size_m = 1.0
    plane = LocalPlane(base.lat, base.lon)

    # The worked block is 400..520 m east; claim the field runs to 580 m.
    ring_xy = [(395.0, -10.0), (580.0, -10.0), (580.0, 310.0), (395.0, 310.0), (395.0, -10.0)]
    ring = []
    for x, y in ring_xy:
        la, lo = plane.inverse(x, y)
        ring.append([float(lo), float(la)])

    inferred = analyze(track, cfg, base, render=False)
    bounded = analyze(track, cfg, base, field_polygons=[ring], render=False)

    assert bounded.coverage.gap_area_m2 > inferred.coverage.gap_area_m2
    # The unworked strip is 60 m x 320 m.
    assert bounded.coverage.gap_area_m2 == pytest.approx(60.0 * 320.0, rel=0.25)


def test_large_time_gap_is_not_bridged():
    # Long enough that dropping a third of it exceeds the 60 s max fix gap.
    long_length = 900.0
    track = straight_pass(length_m=long_length, interval_s=1.0)
    # Delete the middle third, leaving a jump the analyser must not paint over.
    n = len(track)
    keep = np.ones(n, dtype=bool)
    keep[n // 3 : 2 * n // 3] = False
    holed = track.subset(keep)

    result = analyze(holed, SprayerConfig(boom_width_m=BOOM, cell_size_m=0.5), render=False)
    assert result.coverage.sprayed_area_m2 == pytest.approx(long_length * BOOM * 2 / 3, rel=0.1)
    assert any("fix gap" in w for w in result.warnings)
