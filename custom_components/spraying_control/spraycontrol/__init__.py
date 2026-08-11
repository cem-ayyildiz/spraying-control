"""Sprayer GPS track analysis: coverage, overlap, misses, refills and rate."""

from .analyze import analyze
from .models import (
    AnalysisResult,
    BaseLocation,
    CoverageStats,
    GapPatch,
    Pass,
    SprayerConfig,
    TankLoad,
    Track,
)
from .parsers import parse_track
from .report import format_text_report, to_geojson

__version__ = "0.1.0"

__all__ = [
    "analyze",
    "parse_track",
    "to_geojson",
    "format_text_report",
    "AnalysisResult",
    "BaseLocation",
    "CoverageStats",
    "GapPatch",
    "Pass",
    "SprayerConfig",
    "TankLoad",
    "Track",
    "__version__",
]
