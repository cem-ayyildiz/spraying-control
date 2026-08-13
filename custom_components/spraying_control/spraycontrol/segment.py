"""Turn a raw track into classified motion: what was sprayed, what was
transport, and when the machine went back to base to refill.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from .geo import LocalPlane, haversine_m
from .models import BaseLocation, BaseVisit, Pass, PointState, SprayerConfig, Track

# Above this implied speed a fix is a GPS spike, not a vehicle.
SPIKE_SPEED_MS = 40.0


@dataclass
class SegmentedTrack:
    track: Track  # after filtering
    plane: LocalPlane
    x: np.ndarray
    y: np.ndarray
    seg_state: np.ndarray  # length n-1, PointState values
    seg_dist: np.ndarray  # length n-1, metres
    seg_dt: np.ndarray  # length n-1, seconds
    in_base: np.ndarray  # length n, bool
    passes: list[Pass]
    visits: list[BaseVisit]
    n_points_raw: int
    warnings: list[str]

    @property
    def spraying_mask(self) -> np.ndarray:
        return self.seg_state == PointState.SPRAYING

    def distance_where(self, state: PointState) -> float:
        return float(self.seg_dist[self.seg_state == state].sum())


def _drop_bad_accuracy(track: Track, cfg: SprayerConfig, warnings: list[str]) -> Track:
    if track.accuracy is None:
        return track
    acc = track.accuracy
    bad = np.isfinite(acc) & (acc > cfg.max_accuracy_m)
    if not bad.any():
        return track
    if bad.all():
        warnings.append(
            f"Every fix reports accuracy worse than {cfg.max_accuracy_m:.0f} m; "
            "accuracy filtering was skipped so the run could still be analysed."
        )
        return track
    warnings.append(f"Dropped {int(bad.sum())} fixes with accuracy worse than {cfg.max_accuracy_m:.0f} m.")
    return track.subset(~bad)


def _drop_spikes(track: Track, warnings: list[str]) -> Track:
    """Remove single fixes that would require an implausible speed to reach and
    leave. Phone GPS produces these regularly under tree cover."""
    n = len(track)
    if n < 3:
        return track
    keep = np.ones(n, dtype=bool)
    d = haversine_m(track.lat[:-1], track.lon[:-1], track.lat[1:], track.lon[1:])
    dt = np.diff(track.t)
    with np.errstate(divide="ignore", invalid="ignore"):
        v = np.where(dt > 0, d / dt, np.inf)
    for i in range(1, n - 1):
        if v[i - 1] > SPIKE_SPEED_MS and v[i] > SPIKE_SPEED_MS:
            straight = haversine_m(track.lat[i - 1], track.lon[i - 1], track.lat[i + 1], track.lon[i + 1])
            if straight < d[i - 1] + d[i] - 1.0:  # the detour is real, not just fast driving
                keep[i] = False
    if keep.all():
        return track
    warnings.append(f"Removed {int((~keep).sum())} GPS spikes.")
    return track.subset(keep)


def segment_track(
    track: Track,
    cfg: SprayerConfig,
    base: BaseLocation | None = None,
    plane: LocalPlane | None = None,
) -> SegmentedTrack:
    """Classify one track's motion.

    ``plane`` lets several tracks share one projection, which is what makes a
    combined coverage grid across sessions possible.
    """
    warnings: list[str] = []
    n_raw = len(track)
    if n_raw < 2:
        raise ValueError("track needs at least 2 points")

    track = _drop_bad_accuracy(track, cfg, warnings)
    track = _drop_spikes(track, warnings)
    n = len(track)
    if n < 2:
        raise ValueError("track has fewer than 2 usable points after filtering")

    if plane is None:
        plane = LocalPlane.anchored_on(track.lat, track.lon)
    x, y = plane.forward(track.lat, track.lon)

    seg_dist = np.hypot(np.diff(x), np.diff(y))
    seg_dt = np.diff(track.t)
    with np.errstate(divide="ignore", invalid="ignore"):
        seg_speed = np.where(seg_dt > 0, seg_dist / seg_dt, 0.0)

    # Proximity to base is judged per fix, so a pass that clips the headland
    # next to the yard is not mistaken for a refill.
    if base is not None:
        dist_base = haversine_m(track.lat, track.lon, base.lat, base.lon)
        in_base = dist_base <= base.radius_m
    else:
        in_base = np.zeros(n, dtype=bool)

    min_ms = cfg.min_speed_kmh / 3.6
    max_ms = cfg.max_speed_kmh / 3.6

    state = np.full(n - 1, PointState.SPRAYING, dtype=np.int8)
    state[seg_speed < min_ms] = PointState.STOPPED
    state[seg_speed > max_ms] = PointState.TRANSPORT
    # A segment with either end inside the base radius is yard movement.
    state[in_base[:-1] | in_base[1:]] = PointState.AT_BASE
    # A long silence could hide anything; never paint a swath across it.
    state[seg_dt > cfg.max_gap_s] = PointState.GAP

    visits = _find_base_visits(track.t, in_base, base) if base is not None else []
    passes = _find_passes(track, state, seg_dist)

    _check_fix_density(state, seg_dist, seg_dt, cfg, warnings)
    _check_accuracy(track, cfg, warnings)

    n_gap = int((state == PointState.GAP).sum())
    if n_gap:
        gap_dist = float(seg_dist[state == PointState.GAP].sum())
        warnings.append(
            f"{n_gap} segments exceeded the {cfg.max_gap_s:.0f} s fix gap "
            f"({gap_dist:.0f} m of travel) and were left uncovered."
        )

    return SegmentedTrack(
        track=track,
        plane=plane,
        x=x,
        y=y,
        seg_state=state,
        seg_dist=seg_dist,
        seg_dt=seg_dt,
        in_base=in_base,
        passes=passes,
        visits=visits,
        n_points_raw=n_raw,
        warnings=warnings,
    )


def _check_fix_density(
    state: np.ndarray,
    seg_dist: np.ndarray,
    seg_dt: np.ndarray,
    cfg: SprayerConfig,
    warnings: list[str],
) -> None:
    """Note when fixes are further apart than the spray band is wide.

    Between two fixes the walker is assumed to have gone straight. That holds
    while the spacing stays under about a swath width; past that the band
    between fixes is interpolated. The Companion app reports slowly by default,
    so this is worth surfacing.
    """
    spraying = state == PointState.SPRAYING
    if spraying.sum() < 5:
        return

    spacing = float(np.median(seg_dist[spraying]))
    if spacing <= cfg.swath_width_m:
        return

    interval = float(np.median(seg_dt[spraying]))
    speed_ms = spacing / interval if interval > 0 else 0.0
    needed = cfg.swath_width_m / speed_ms if speed_ms > 0 else 0.0
    warnings.append(
        f"Fixes are {spacing:.0f} m apart on average ({interval:.0f} s at "
        f"{speed_ms * 3.6:.1f} km/h), wider than the {cfg.swath_width_m:g} m spray band, "
        f"so the coverage between fixes is filled in. For a sharper map log a "
        f"position every {max(1, int(needed)):d} s or so - in the Companion app "
        f"turn on high accuracy mode while you spray."
    )


def _check_accuracy(track: Track, cfg: SprayerConfig, warnings: list[str]) -> None:
    """Note when GPS accuracy is coarse relative to the spray band.

    A phone is typically accurate to several metres. When that is a good deal
    wider than the band you treat, the totals (area, tanks, volume, rate) still
    hold up, but the fine gap and overlap map is only a rough guide.
    """
    if track.accuracy is None:
        return
    acc = track.accuracy[np.isfinite(track.accuracy)]
    if acc.size == 0:
        return
    median_acc = float(np.median(acc))
    if median_acc <= 2.0 * cfg.swath_width_m:
        return
    warnings.append(
        f"GPS accuracy is about {median_acc:.0f} m, wider than the "
        f"{cfg.swath_width_m:g} m spray band. Area treated, tanks and volume are "
        f"still sound; treat the gap and overlap map as a rough guide rather than "
        f"metre-perfect."
    )


def _runs(mask: np.ndarray) -> list[tuple[int, int]]:
    """Index ranges (start, end_inclusive) of each True run."""
    if not mask.any():
        return []
    padded = np.concatenate(([False], mask, [False]))
    edges = np.flatnonzero(padded[1:] != padded[:-1])
    return [(int(a), int(b - 1)) for a, b in zip(edges[::2], edges[1::2])]


def _find_base_visits(t: np.ndarray, in_base: np.ndarray, base: BaseLocation) -> list[BaseVisit]:
    visits: list[BaseVisit] = []
    for start, end in _runs(in_base):
        # A visit's duration spans from arrival to departure, so extend to the
        # neighbouring fixes where they exist.
        t_start = t[start]
        t_end = t[end]
        if t_end - t_start >= base.min_dwell_s:
            visits.append(BaseVisit(start_t=float(t_start), end_t=float(t_end), index=len(visits)))
    return visits


def _find_passes(track: Track, state: np.ndarray, seg_dist: np.ndarray) -> list[Pass]:
    passes: list[Pass] = []
    for start, end in _runs(state == PointState.SPRAYING):
        passes.append(
            Pass(
                start_idx=start,
                end_idx=end + 1,  # point index of the segment's far end
                start_t=float(track.t[start]),
                end_t=float(track.t[end + 1]),
                distance_m=float(seg_dist[start : end + 1].sum()),
            )
        )
    return passes


def assign_loads(passes: list[Pass], visits: list[BaseVisit], t_first: float, t_last: float) -> list[tuple[float, float, bool]]:
    """Split the run into tank loads.

    A load starts when the machine leaves base (or when the track starts) and
    ends when it next arrives at base. Returns ``(start_t, end_t, is_complete)``
    per load, where a complete load is one that ended in a refill and so
    consumed the full tank.

    Only a stop that is followed by more spraying counts as a refill. Pulling
    into the yard at the end of the day is parking: the tank may well be part
    full, so the last load is left partial rather than charged in full.
    """
    last_pass_t = max((p.end_t for p in passes), default=t_first)
    refills = [v for v in visits if v.end_t < last_pass_t]

    starts = [t_first] + [v.end_t for v in refills]
    ends = [v.start_t for v in refills] + [t_last]

    loads: list[tuple[float, float, bool]] = []
    for i, (s, e) in enumerate(zip(starts, ends)):
        if e <= s:
            continue
        complete = i < len(refills)  # anything but the trailing interval
        # An interval with no spraying in it is yard time, not a tank load.
        if any(p.start_t < e and p.end_t > s for p in passes):
            loads.append((s, e, complete))
    return loads
