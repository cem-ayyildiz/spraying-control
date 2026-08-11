"""Synthetic runs with known-good geometry.

Used by the tests to check the numbers against hand-computable answers, and by
the web UI to give you something to look at before your own track is ready.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from .geo import LocalPlane
from .models import BaseLocation, SprayerConfig, Track


@dataclass
class PathBuilder:
    """Walk a machine along waypoints at given speeds, sampling at a fixed rate."""

    interval_s: float = 2.0
    t: float = 0.0

    def __post_init__(self) -> None:
        self.xs: list[float] = []
        self.ys: list[float] = []
        self.ts: list[float] = []
        self._x = 0.0
        self._y = 0.0

    def start_at(self, x: float, y: float) -> None:
        self._x, self._y = x, y
        self._emit(x, y)

    def _emit(self, x: float, y: float) -> None:
        self.xs.append(x)
        self.ys.append(y)
        self.ts.append(self.t)

    def move_to(self, x: float, y: float, speed_kmh: float) -> None:
        speed = speed_kmh / 3.6
        dx, dy = x - self._x, y - self._y
        length = float(np.hypot(dx, dy))
        if length <= 0:
            return
        duration = length / speed
        n = max(1, int(round(duration / self.interval_s)))
        for k in range(1, n + 1):
            frac = k / n
            self.t += duration / n
            self._emit(self._x + dx * frac, self._y + dy * frac)
        self._x, self._y = x, y

    def dwell(self, seconds: float, jitter_m: float = 1.5, rng: np.random.Generator | None = None) -> None:
        """Sit still. A little jitter keeps it honest - a parked phone still
        wanders a metre or two."""
        rng = rng or np.random.default_rng(0)
        n = max(1, int(round(seconds / self.interval_s)))
        for _ in range(n):
            self.t += seconds / n
            self._emit(self._x + rng.normal(0, jitter_m), self._y + rng.normal(0, jitter_m))


def synthetic_run(
    *,
    # A garden plot; the exact spot only matters for the map backdrop.
    base_lat: float = 38.3005,
    base_lon: float = 32.8985,
    swath_width_m: float = 1.0,
    n_passes: int = 24,
    pass_length_m: float = 40.0,
    field_offset_m: float = 12.0,
    passes_per_load: int = 8,
    spray_speed_kmh: float = 2.5,
    transport_speed_kmh: float = 5.0,
    turn_speed_kmh: float = 1.2,
    interval_s: float = 2.0,
    skip_passes: tuple[int, ...] = (13,),
    shift_passes: dict[int, float] | None = None,
    refill_dwell_s: float = 150.0,
    noise_m: float = 0.0,
    seed: int = 7,
) -> tuple[Track, BaseLocation, SprayerConfig]:
    """Build a plausible knapsack run with deliberate, measurable defects.

    A garden owner walks back-and-forth lanes across a plot, refilling the
    backpack at a water point. ``skip_passes`` omits whole lanes, leaving a
    swath-wide miss. ``shift_passes`` maps a lane index to a lateral offset in
    metres, which creates a measurable overlap with its neighbour.
    """
    rng = np.random.default_rng(seed)
    if shift_passes is None:
        shift_passes = {5: -0.4}
    plane = LocalPlane(base_lat, base_lon)

    builder = PathBuilder(interval_s=interval_s)
    builder.start_at(0.0, 0.0)
    builder.dwell(90.0, jitter_m=0.6, rng=rng)  # initial fill

    def pass_x(index: int) -> float:
        return field_offset_m + swath_width_m * (index + 0.5) + shift_passes.get(index, 0.0)

    worked = 0
    for i in range(n_passes):
        if i in skip_passes:
            continue
        x = pass_x(i)
        # Alternate direction, the way you actually walk a plot.
        y_from, y_to = (0.0, pass_length_m) if worked % 2 == 0 else (pass_length_m, 0.0)

        if worked > 0 and worked % passes_per_load == 0:
            builder.move_to(0.0, 0.0, transport_speed_kmh)  # walk back to refill
            builder.dwell(refill_dwell_s, jitter_m=0.6, rng=rng)
            builder.move_to(x, y_from, transport_speed_kmh)
        elif worked == 0:
            builder.move_to(x, y_from, transport_speed_kmh)
        else:
            builder.move_to(x, y_from, turn_speed_kmh)  # step across to the next lane

        builder.move_to(x, y_to, spray_speed_kmh)
        worked += 1

    builder.move_to(0.0, 0.0, transport_speed_kmh)
    builder.dwell(90.0, jitter_m=0.6, rng=rng)

    xs = np.asarray(builder.xs)
    ys = np.asarray(builder.ys)
    if noise_m > 0:
        xs = xs + rng.normal(0, noise_m, xs.shape)
        ys = ys + rng.normal(0, noise_m, ys.shape)

    lat, lon = plane.inverse(xs, ys)
    # Anchor the run at a fixed wall-clock time so results are reproducible.
    t = np.asarray(builder.ts) + 1_723_000_000.0

    track = Track(
        t=t,
        lat=lat,
        lon=lon,
        accuracy=np.full(t.shape, 2.0),
        speed=None,
        name="Demo run",
        source="synthetic",
    )
    base = BaseLocation(lat=base_lat, lon=base_lon, radius_m=8.0, min_dwell_s=60.0, name="Water point")
    cfg = SprayerConfig(swath_width_m=swath_width_m, tank_capacity_l=18.0)
    return track, base, cfg


def straight_pass(
    length_m: float = 25.0,
    speed_kmh: float = 2.5,
    interval_s: float = 1.0,
    lat0: float = 40.0,
    lon0: float = 32.5,
    x_offset_m: float = 0.0,
) -> Track:
    """A single straight pass. Its swath area is exactly length x swath width."""
    plane = LocalPlane(lat0, lon0)
    speed = speed_kmh / 3.6
    n = int(round(length_m / speed / interval_s))
    t = np.arange(n + 1, dtype=float) * interval_s + 1_723_000_000.0
    y = np.linspace(0.0, length_m, n + 1)
    x = np.full_like(y, x_offset_m)
    lat, lon = plane.inverse(x, y)
    return Track(t=t, lat=lat, lon=lon, name="straight", source="synthetic")


def to_gpx(track: Track) -> str:
    """Serialise a track back to GPX, for exercising the parser."""
    from datetime import datetime, timezone

    points = "\n".join(
        f'      <trkpt lat="{la:.8f}" lon="{lo:.8f}">'
        f"<time>{datetime.fromtimestamp(ts, timezone.utc).isoformat().replace('+00:00', 'Z')}</time></trkpt>"
        for ts, la, lo in zip(track.t, track.lat, track.lon)
    )
    return (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<gpx version="1.1" creator="spraycontrol" xmlns="http://www.topografix.com/GPX/1/1">\n'
        f"  <trk><name>{track.name}</name><trkseg>\n{points}\n  </trkseg></trk>\n</gpx>\n"
    )
