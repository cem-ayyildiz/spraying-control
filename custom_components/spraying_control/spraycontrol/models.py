"""Configuration and result types."""

from __future__ import annotations

import enum
from dataclasses import dataclass, field
from typing import Any

import numpy as np

M2_PER_HA = 10_000.0


class PointState(enum.IntEnum):
    """Classification of the segment that *starts* at a given point."""

    STOPPED = 0
    SPRAYING = 1
    TRANSPORT = 2
    AT_BASE = 3
    GAP = 4  # time gap too large to trust; not interpolated


@dataclass
class Track:
    """A parsed GPS track. All arrays share the same length and are sorted by time."""

    t: np.ndarray  # epoch seconds, float
    lat: np.ndarray
    lon: np.ndarray
    accuracy: np.ndarray | None = None  # metres, if the source reported it
    speed: np.ndarray | None = None  # m/s, if the source reported it
    name: str = "track"
    source: str = ""

    def __len__(self) -> int:
        return int(self.t.shape[0])

    def subset(self, mask: np.ndarray) -> "Track":
        return Track(
            t=self.t[mask],
            lat=self.lat[mask],
            lon=self.lon[mask],
            accuracy=None if self.accuracy is None else self.accuracy[mask],
            speed=None if self.speed is None else self.speed[mask],
            name=self.name,
            source=self.source,
        )


@dataclass
class BaseLocation:
    """Where the tank is refilled. Returns here are counted as refills."""

    lat: float
    lon: float
    radius_m: float = 8.0
    min_dwell_s: float = 60.0
    name: str = "Base"


@dataclass
class SprayerConfig:
    """Knapsack sprayer and analysis parameters.

    Defaults are for a garden owner walking a plot with a 16-20 L backpack
    sprayer, treating a metre-wide band with a hand lance.
    """

    # Effective width of the band treated on one pass. Set it to match your
    # nozzle and technique - a single wide-angle nozzle covers roughly a metre.
    swath_width_m: float = 1.0
    tank_capacity_l: float = 18.0

    # Speed window that counts as active spraying, in km/h. Below the minimum the
    # walker is treated as paused; above the maximum they are just walking, not
    # spraying (a stroll to the next bed, or back to refill).
    min_speed_kmh: float = 0.4
    max_speed_kmh: float = 4.5

    # Two fixes further apart in time than this are not joined into a swath;
    # we cannot know what happened in between.
    max_gap_s: float = 45.0

    # Phone GPS reports an accuracy radius. Fixes worse than this are dropped.
    max_accuracy_m: float = 25.0

    # A cell touched again within this many seconds is the same pass, not a
    # second one. Zero derives it from the fix interval; see analyze.reentry_gap.
    reentry_gap_s: float = 0.0

    # Fine enough to resolve a metre-wide band.
    cell_size_m: float = 0.25
    min_gap_area_m2: float = 2.0

    # Structuring radius for inferring the plot boundary from the sprayed area,
    # as a multiple of the swath width. Lane spacings up to this are closed over.
    field_close_factor: float = 2.0

    def __post_init__(self) -> None:
        if self.swath_width_m <= 0:
            raise ValueError("swath_width_m must be positive")
        if self.tank_capacity_l <= 0:
            raise ValueError("tank_capacity_l must be positive")
        if self.min_speed_kmh >= self.max_speed_kmh:
            raise ValueError("min_speed_kmh must be below max_speed_kmh")
        if self.cell_size_m <= 0:
            raise ValueError("cell_size_m must be positive")


@dataclass
class BaseVisit:
    """A dwell inside the base radius, long enough to count as a refill stop."""

    start_t: float
    end_t: float
    index: int  # order within the run, 0-based

    @property
    def duration_s(self) -> float:
        return self.end_t - self.start_t


@dataclass
class Pass:
    """A contiguous run of spraying."""

    start_idx: int
    end_idx: int  # inclusive
    start_t: float
    end_t: float
    distance_m: float
    area_m2: float = 0.0
    load_index: int = 0

    @property
    def duration_s(self) -> float:
        return self.end_t - self.start_t

    @property
    def mean_speed_kmh(self) -> float:
        return 3.6 * self.distance_m / self.duration_s if self.duration_s > 0 else 0.0


@dataclass
class TankLoad:
    """Work done on one tank of spray, between two refill stops."""

    index: int
    start_t: float
    end_t: float
    area_m2: float
    distance_m: float
    volume_l: float
    is_complete: bool  # ended with a return to base, so the tank was run out

    @property
    def area_ha(self) -> float:
        return self.area_m2 / M2_PER_HA

    @property
    def rate_l_per_ha(self) -> float:
        return self.volume_l / self.area_ha if self.area_ha > 0 else 0.0


@dataclass
class GapPatch:
    """A contiguous unsprayed area inside the field boundary."""

    area_m2: float
    lat: float
    lon: float
    max_width_m: float
    polygon: list[list[float]] = field(default_factory=list)  # [[lon, lat], ...]


@dataclass
class CoverageStats:
    field_area_m2: float
    sprayed_area_m2: float
    gap_area_m2: float
    overlap_area_m2: float  # area covered 2+ times
    heavy_overlap_area_m2: float  # area covered 3+ times
    mean_passes_over_sprayed: float
    histogram: dict[int, float]  # pass count -> area m2

    @property
    def coverage_pct(self) -> float:
        return 100.0 * self.sprayed_area_m2 / self.field_area_m2 if self.field_area_m2 else 0.0

    @property
    def overlap_pct(self) -> float:
        return 100.0 * self.overlap_area_m2 / self.sprayed_area_m2 if self.sprayed_area_m2 else 0.0

    @property
    def gap_pct(self) -> float:
        return 100.0 * self.gap_area_m2 / self.field_area_m2 if self.field_area_m2 else 0.0


@dataclass
class AnalysisResult:
    config: SprayerConfig
    base: BaseLocation | None
    track_name: str
    start_t: float
    end_t: float

    n_points_raw: int
    n_points_used: int

    passes: list[Pass]
    visits: list[BaseVisit]
    loads: list[TankLoad]
    coverage: CoverageStats
    gaps: list[GapPatch]

    total_volume_l: float
    total_distance_m: float
    spraying_time_s: float
    transport_distance_m: float

    bounds: tuple[float, float, float, float]  # south, west, north, east
    overlay_png: bytes | None = None
    warnings: list[str] = field(default_factory=list)

    @property
    def refill_count(self) -> int:
        """Number of times the tank was refilled *after* the initial fill."""
        return max(0, len(self.loads) - 1)

    @property
    def overall_rate_l_per_ha(self) -> float:
        ha = self.coverage.sprayed_area_m2 / M2_PER_HA
        return self.total_volume_l / ha if ha > 0 else 0.0

    def summary(self) -> dict[str, Any]:
        cov = self.coverage
        return {
            "track": self.track_name,
            "start": self.start_t,
            "end": self.end_t,
            "duration_s": self.end_t - self.start_t,
            "points_raw": self.n_points_raw,
            "points_used": self.n_points_used,
            "field_area_ha": round(cov.field_area_m2 / M2_PER_HA, 4),
            "sprayed_area_ha": round(cov.sprayed_area_m2 / M2_PER_HA, 4),
            "coverage_pct": round(cov.coverage_pct, 2),
            "gap_area_ha": round(cov.gap_area_m2 / M2_PER_HA, 4),
            "gap_pct": round(cov.gap_pct, 2),
            "gap_patches": len(self.gaps),
            "overlap_area_ha": round(cov.overlap_area_m2 / M2_PER_HA, 4),
            "overlap_pct": round(cov.overlap_pct, 2),
            "heavy_overlap_area_ha": round(cov.heavy_overlap_area_m2 / M2_PER_HA, 4),
            "mean_passes": round(cov.mean_passes_over_sprayed, 3),
            "tank_loads": len(self.loads),
            "refills": self.refill_count,
            "total_volume_l": round(self.total_volume_l, 1),
            "overall_rate_l_per_ha": round(self.overall_rate_l_per_ha, 2),
            "spraying_runs": len(self.passes),
            "spraying_time_s": round(self.spraying_time_s),
            "total_distance_m": round(self.total_distance_m),
            "transport_distance_m": round(self.transport_distance_m),
            "warnings": self.warnings,
        }
