"""Top-level analysis: track in, coverage and product usage out."""

from __future__ import annotations

import numpy as np

from .coverage import (
    CoverageGrid,
    find_gaps,
    infer_field_mask,
    make_grid,
    rasterize_field_polygons,
    swath_windows,
)
from .geo import LocalPlane
from .models import (
    AnalysisResult,
    BaseLocation,
    CoverageStats,
    M2_PER_HA,
    PointState,
    SprayerConfig,
    TankLoad,
    Track,
    TrackSummary,
)
from .report import render_overlay
from .segment import SegmentedTrack, assign_loads, segment_track


def analyze(
    tracks: Track | list[Track],
    cfg: SprayerConfig | None = None,
    base: BaseLocation | None = None,
    field_polygons: list[list[list[float]]] | None = None,
    render: bool = True,
) -> AnalysisResult:
    """Analyse one spraying run, or several sessions combined.

    Pass a list of tracks to build one coverage picture across sessions: ground
    that a later session went over again shows as overlap, because the shared
    grid remembers when each cell was last covered.

    ``field_polygons`` are optional boundaries as ``[[[lon, lat], ...], ...]``.
    Without them the field is inferred from the sprayed area, which finds
    interior misses but cannot know about an edge that was never approached.
    """
    cfg = cfg or SprayerConfig()
    track_list = [tracks] if isinstance(tracks, Track) else list(tracks)
    if not track_list:
        raise ValueError("no tracks to analyse")

    # One projection for every track, so their swaths land on the same grid.
    plane = LocalPlane.anchored_on(
        np.concatenate([t.lat for t in track_list]),
        np.concatenate([t.lon for t in track_list]),
    )

    segments: list[SegmentedTrack] = []
    warnings: list[str] = []
    for track in track_list:
        try:
            seg = segment_track(track, cfg, base, plane=plane)
        except ValueError as err:
            warnings.append(f"{track.name}: {err}")
            continue
        segments.append(seg)
        warnings.extend(
            w if len(track_list) == 1 else f"{track.name}: {w}" for w in seg.warnings
        )

    worked = [s for s in segments if s.passes]
    if not worked:
        if not segments:
            raise ValueError("; ".join(warnings) or "no usable tracks")
        return _empty_result(track_list[0], segments[0], cfg, base, warnings)

    # Chronological order, so "covered again later" means what it says.
    worked.sort(key=lambda s: float(s.track.t[0]))

    grid = _build_grid(worked, cfg, field_polygons, warnings)
    loads, track_summaries = _accumulate(worked, grid, cfg)

    covered = grid.counts > 0
    field_mask, user_bounded = _field_mask(grid, covered, cfg, field_polygons, warnings)

    raw_gap_mask = field_mask & ~covered
    gaps, gap_mask = find_gaps(raw_gap_mask, grid, cfg)

    stats = _coverage_stats(grid, covered, field_mask, gap_mask)
    _assign_volumes(loads, cfg, warnings)
    for summary in track_summaries:
        summary.volume_l = sum(
            load.volume_l for load in loads if load.track_name == summary.name
        )

    if base is None:
        warnings.append(
            "No base location set, so refills could not be detected. "
            "Total volume assumes a single tank."
        )
    elif not any(s.visits for s in worked):
        warnings.append(
            f"No track dwelled {base.min_dwell_s:.0f} s inside "
            f"{base.radius_m:.0f} m of the base, so no refill was detected."
        )

    if not user_bounded:
        warnings.append(
            "The plot was worked out from where you walked. A strip along the "
            "outer edge that you never walked to cannot be spotted until you draw "
            "the plot boundary."
        )

    overlay = render_overlay(grid.counts, field_mask, gap_mask) if render else None
    south, west, north, east = grid.bounds_latlon()

    name = (
        worked[0].track.name
        if len(worked) == 1
        else f"{len(worked)} sessions"
    )

    return AnalysisResult(
        config=cfg,
        base=base,
        track_name=name,
        start_t=min(float(s.track.t[0]) for s in worked),
        end_t=max(float(s.track.t[-1]) for s in worked),
        n_points_raw=sum(s.n_points_raw for s in worked),
        n_points_used=sum(len(s.track) for s in worked),
        passes=[p for s in worked for p in s.passes],
        visits=[v for s in worked for v in s.visits],
        loads=loads,
        coverage=stats,
        gaps=gaps,
        total_volume_l=sum(load.volume_l for load in loads),
        total_distance_m=sum(
            float(s.seg_dist[s.seg_state != PointState.GAP].sum()) for s in worked
        ),
        spraying_time_s=sum(float(s.seg_dt[s.spraying_mask].sum()) for s in worked),
        transport_distance_m=sum(s.distance_where(PointState.TRANSPORT) for s in worked),
        bounds=(south, west, north, east),
        overlay_png=overlay,
        warnings=warnings,
        tracks=track_summaries,
    )


def _build_grid(
    segments: list[SegmentedTrack],
    cfg: SprayerConfig,
    field_polygons: list | None,
    warnings: list[str],
) -> CoverageGrid:
    """Size one grid to every track's sprayed extent, widened to any supplied
    boundary so that unworked parts of the field still fall inside it."""
    plane = segments[0].plane
    xs_parts, ys_parts = [], []
    for seg in segments:
        idx = np.concatenate([np.arange(p.start_idx, p.end_idx + 1) for p in seg.passes])
        xs_parts.append(seg.x[idx])
        ys_parts.append(seg.y[idx])
    xs = np.concatenate(xs_parts)
    ys = np.concatenate(ys_parts)

    if field_polygons:
        px, py = [], []
        for ring in field_polygons:
            for lon, lat in ring:
                x, y = plane.forward(lat, lon)
                px.append(float(x))
                py.append(float(y))
        if px:
            xs = np.concatenate([xs, np.asarray(px)])
            ys = np.concatenate([ys, np.asarray(py)])

    return make_grid(xs, ys, plane, cfg, warnings)


def reentry_gap(seg: SegmentedTrack, cfg: SprayerConfig) -> float:
    """How long a cell must go untouched before returning to it counts as a
    second pass.

    Without a lance on/off signal there are no discrete passes to count: a
    headland turn keeps one spraying run going for the whole tank. Time is the
    discriminator instead. Consecutive fixes re-touch a cell seconds apart,
    whereas the neighbouring swath only comes back minutes later, after the turn.
    The floor is tied to the fix interval so that sparse phone GPS - where
    successive swath windows barely meet - does not read as overlap.
    """
    if cfg.reentry_gap_s > 0:
        return cfg.reentry_gap_s
    dt = seg.seg_dt[seg.spraying_mask]
    median_dt = float(np.median(dt)) if dt.size else 1.0
    return max(3.0 * median_dt, 10.0)


def _accumulate(
    segments: list[SegmentedTrack],
    grid: CoverageGrid,
    cfg: SprayerConfig,
) -> tuple[list[TankLoad], list[TrackSummary]]:
    """Stamp every track's swaths into the shared grid.

    ``last_t`` is shared across tracks, so a cell revisited in a later session
    is more than a re-entry gap old and counts as another pass - which is what
    spraying the same ground twice actually is.
    """
    half_width = cfg.swath_width_m / 2.0

    # Time a cell was last sprayed; -inf means never.
    last_t = np.full(grid.shape, -np.inf, dtype=np.float64)
    pass_mask = np.zeros(grid.shape, dtype=bool)
    load_mask = np.zeros(grid.shape, dtype=bool)
    track_mask = np.zeros(grid.shape, dtype=bool)

    loads: list[TankLoad] = []
    summaries: list[TrackSummary] = []
    seen = np.zeros(grid.shape, dtype=bool)  # ground covered by earlier tracks

    for seg in segments:
        threshold = reentry_gap(seg, cfg)
        intervals = assign_loads(
            seg.passes, seg.visits, float(seg.track.t[0]), float(seg.track.t[-1])
        )
        track_mask[:] = False
        first_load = len(loads)

        for local_i, (start_t, end_t, complete) in enumerate(intervals):
            load_mask[:] = False
            distance = 0.0
            index = len(loads)

            for p in seg.passes:
                # Midpoint decides ownership, so a run is never split across loads.
                if not (start_t <= (p.start_t + p.end_t) / 2.0 <= end_t):
                    continue
                pass_mask[:] = False
                sl = slice(p.start_idx, p.end_idx + 1)
                times = seg.track.t[p.start_idx + 1 : p.end_idx + 1]

                windows = swath_windows(grid, seg.x[sl], seg.y[sl], half_width)
                for (r0, r1, c0, c1, hit), t_seg in zip(windows, times):
                    sub_last = last_t[r0:r1, c0:c1]
                    fresh = hit & ((t_seg - sub_last) > threshold)
                    grid.counts[r0:r1, c0:c1] += fresh
                    sub_last[hit] = t_seg
                    pass_mask[r0:r1, c0:c1] |= hit

                distance += p.distance_m
                p.load_index = index
                p.area_m2 = float(pass_mask.sum()) * grid.cell_area
                load_mask |= pass_mask

            loads.append(
                TankLoad(
                    index=index,
                    start_t=start_t,
                    end_t=end_t,
                    area_m2=float(load_mask.sum()) * grid.cell_area,
                    distance_m=distance,
                    volume_l=0.0,
                    is_complete=complete,
                    track_name=seg.track.name,
                )
            )
            track_mask |= load_mask

        moved = seg.seg_state != PointState.GAP
        summaries.append(
            TrackSummary(
                name=seg.track.name,
                start_t=float(seg.track.t[0]),
                end_t=float(seg.track.t[-1]),
                n_points=len(seg.track),
                area_m2=float(track_mask.sum()) * grid.cell_area,
                new_area_m2=float((track_mask & ~seen).sum()) * grid.cell_area,
                distance_m=float(seg.seg_dist[moved].sum()),
                spraying_time_s=float(seg.seg_dt[seg.spraying_mask].sum()),
                n_loads=len(loads) - first_load,
                volume_l=0.0,
            )
        )
        seen |= track_mask

    return loads, summaries


def _field_mask(
    grid: CoverageGrid,
    covered: np.ndarray,
    cfg: SprayerConfig,
    field_polygons: list | None,
    warnings: list[str],
) -> tuple[np.ndarray, bool]:
    if field_polygons:
        from shapely.geometry import Polygon

        polys = []
        for ring in field_polygons:
            if len(ring) < 3:
                continue
            xy = [grid.plane.forward(lat, lon) for lon, lat in ring]
            poly = Polygon([(float(x), float(y)) for x, y in xy])
            if not poly.is_valid:
                poly = poly.buffer(0)
            if not poly.is_empty:
                polys.append(poly)
        if polys:
            mask = rasterize_field_polygons(grid, polys)
            outside = float((covered & ~mask).sum()) * grid.cell_area
            if outside > 0.02 * float(covered.sum()) * grid.cell_area:
                warnings.append(
                    f"{outside / M2_PER_HA:.2f} ha was sprayed outside the supplied "
                    "field boundary."
                )
            # Spraying beyond the boundary still counts as sprayed ground.
            return mask | covered, True
        warnings.append("Supplied field boundary had no usable rings; falling back to inference.")

    return infer_field_mask(covered, cfg, grid.cell), False


def _coverage_stats(
    grid: CoverageGrid,
    covered: np.ndarray,
    field_mask: np.ndarray,
    gap_mask: np.ndarray,
) -> CoverageStats:
    cell_area = grid.cell_area
    counts = grid.counts

    sprayed_cells = int(covered.sum())
    hist_counts = np.bincount(counts[covered].ravel()) if sprayed_cells else np.zeros(1, dtype=int)
    histogram = {int(k): float(v) * cell_area for k, v in enumerate(hist_counts) if k > 0 and v}

    return CoverageStats(
        field_area_m2=float(field_mask.sum()) * cell_area,
        sprayed_area_m2=sprayed_cells * cell_area,
        gap_area_m2=float(gap_mask.sum()) * cell_area,
        overlap_area_m2=float((counts >= 2).sum()) * cell_area,
        heavy_overlap_area_m2=float((counts >= 3).sum()) * cell_area,
        mean_passes_over_sprayed=(float(counts[covered].mean()) if sprayed_cells else 0.0),
        histogram=histogram,
    )


def _assign_volumes(loads: list[TankLoad], cfg: SprayerConfig, warnings: list[str]) -> None:
    """Turn refill count into litres.

    Every load that ended with a return to base emptied the tank. The final load
    is still in progress, so it is scaled by area against the completed ones.
    """
    tank = cfg.tank_capacity_l
    complete = [load for load in loads if load.is_complete and load.area_m2 > 0]

    for load in loads:
        if load.is_complete:
            load.volume_l = tank
        elif complete:
            mean_area = sum(c.area_m2 for c in complete) / len(complete)
            share = load.area_m2 / mean_area if mean_area > 0 else 1.0
            load.volume_l = tank * min(1.0, share)
        else:
            load.volume_l = tank

    if not complete and loads:
        warnings.append(
            "No completed tank load was observed, so the total is an upper bound: "
            f"at most one full {tank:.0f} L tank."
        )


def _empty_result(
    track: Track,
    seg: SegmentedTrack,
    cfg: SprayerConfig,
    base: BaseLocation | None,
    warnings: list[str],
) -> AnalysisResult:
    warnings.append(
        "No spraying was detected. Check the speed window "
        f"({cfg.min_speed_kmh:g}-{cfg.max_speed_kmh:g} km/h) and the base radius."
    )
    zero = CoverageStats(0.0, 0.0, 0.0, 0.0, 0.0, 0.0, {})
    return AnalysisResult(
        config=cfg,
        base=base,
        track_name=track.name,
        start_t=float(seg.track.t[0]),
        end_t=float(seg.track.t[-1]),
        n_points_raw=seg.n_points_raw,
        n_points_used=len(seg.track),
        passes=[],
        visits=seg.visits,
        loads=[],
        coverage=zero,
        gaps=[],
        total_volume_l=0.0,
        total_distance_m=float(seg.seg_dist.sum()),
        spraying_time_s=0.0,
        transport_distance_m=seg.distance_where(PointState.TRANSPORT),
        bounds=(
            float(seg.track.lat.min()),
            float(seg.track.lon.min()),
            float(seg.track.lat.max()),
            float(seg.track.lon.max()),
        ),
        overlay_png=None,
        warnings=warnings,
    )
