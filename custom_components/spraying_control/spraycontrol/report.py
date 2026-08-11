"""Rendering: coverage overlay image and GeoJSON export."""

from __future__ import annotations

import struct
import zlib

import numpy as np

from .models import AnalysisResult, M2_PER_HA

# Colour per pass count. Index 0 is an unsprayed cell inside the field.
COVERAGE_COLORS: list[tuple[int, int, int, int]] = [
    (255, 45, 85, 170),  # 0 - missed
    (46, 194, 126, 110),  # 1 - single pass, on target
    (245, 194, 17, 155),  # 2 - double
    (255, 120, 0, 180),  # 3 - triple
    (145, 65, 172, 205),  # 4+ - heavy
]

COVERAGE_LABELS = ["Missed", "1 pass", "2 passes", "3 passes", "4+ passes"]


def write_png(rgba: np.ndarray) -> bytes:
    """Encode an (H, W, 4) uint8 array as a PNG."""
    if rgba.dtype != np.uint8:
        raise TypeError("rgba must be uint8")
    height, width, channels = rgba.shape
    if channels != 4:
        raise ValueError("expected 4 channels")

    # Each scanline is prefixed with filter type 0 (none).
    stride = width * 4
    raw = np.empty((height, stride + 1), dtype=np.uint8)
    raw[:, 0] = 0
    raw[:, 1:] = rgba.reshape(height, stride)

    def chunk(tag: bytes, payload: bytes) -> bytes:
        body = tag + payload
        return struct.pack(">I", len(payload)) + body + struct.pack(">I", zlib.crc32(body) & 0xFFFFFFFF)

    ihdr = struct.pack(">IIBBBBB", width, height, 8, 6, 0, 0, 0)
    return (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", ihdr)
        + chunk(b"IDAT", zlib.compress(raw.tobytes(), 6))
        + chunk(b"IEND", b"")
    )


def render_overlay(counts: np.ndarray, field_mask: np.ndarray, gap_mask: np.ndarray) -> bytes:
    """Colour the pass-count grid for display on a map.

    Only gaps that survived the minimum-area filter are painted, so the ragged
    single-cell fringe along swath edges does not read as a miss.
    """
    klass = np.clip(counts, 0, len(COVERAGE_COLORS) - 1).astype(np.uint8)
    visible = (counts > 0) | gap_mask

    height, width = counts.shape
    rgba = np.zeros((height, width, 4), dtype=np.uint8)
    for value, color in enumerate(COVERAGE_COLORS):
        sel = (klass == value) & visible
        if not sel.any():
            continue
        rgba[sel] = color

    # Row 0 of the grid is the southern edge; PNG rows run top-down.
    return write_png(rgba[::-1])


def to_geojson(result: AnalysisResult) -> dict:
    """Gaps, base location and per-load summaries as a FeatureCollection."""
    features: list[dict] = []

    for i, gap in enumerate(result.gaps):
        geometry = (
            {"type": "Polygon", "coordinates": [gap.polygon]}
            if len(gap.polygon) >= 4
            else {"type": "Point", "coordinates": [gap.lon, gap.lat]}
        )
        features.append(
            {
                "type": "Feature",
                "geometry": geometry,
                "properties": {
                    "kind": "gap",
                    "rank": i + 1,
                    "area_m2": round(gap.area_m2, 1),
                    "area_ha": round(gap.area_m2 / M2_PER_HA, 4),
                    "max_width_m": round(gap.max_width_m, 2),
                },
            }
        )

    if result.base is not None:
        features.append(
            {
                "type": "Feature",
                "geometry": {"type": "Point", "coordinates": [result.base.lon, result.base.lat]},
                "properties": {
                    "kind": "base",
                    "name": result.base.name,
                    "radius_m": result.base.radius_m,
                    "refills": result.refill_count,
                },
            }
        )

    return {
        "type": "FeatureCollection",
        "properties": result.summary(),
        "features": features,
    }


def format_text_report(result: AnalysisResult) -> str:
    """Plain-text summary for the CLI."""
    cov = result.coverage
    lines = [
        f"Track:            {result.track_name}",
        f"Points:           {result.n_points_used} used of {result.n_points_raw}",
        "",
        "COVERAGE",
        f"  Field area:     {cov.field_area_m2 / M2_PER_HA:8.3f} ha",
        f"  Sprayed:        {cov.sprayed_area_m2 / M2_PER_HA:8.3f} ha  ({cov.coverage_pct:.1f}% of field)",
        f"  Missed:         {cov.gap_area_m2 / M2_PER_HA:8.3f} ha  ({cov.gap_pct:.1f}%, {len(result.gaps)} patches)",
        f"  Overlapped 2x+: {cov.overlap_area_m2 / M2_PER_HA:8.3f} ha  ({cov.overlap_pct:.1f}% of sprayed)",
        f"  Overlapped 3x+: {cov.heavy_overlap_area_m2 / M2_PER_HA:8.3f} ha",
        f"  Mean passes:    {cov.mean_passes_over_sprayed:8.2f}",
        "",
        "PRODUCT",
        f"  Tank capacity:  {result.config.tank_capacity_l:8.0f} L",
        f"  Tank loads:     {len(result.loads):8d}  ({result.refill_count} refill"
        f"{'' if result.refill_count == 1 else 's'})",
        f"  Total volume:   {result.total_volume_l:8.0f} L",
        f"  Overall rate:   {result.overall_rate_l_per_ha:8.1f} L/ha",
    ]

    if result.loads:
        lines += ["", "  Per tank load:"]
        for load in result.loads:
            flag = "" if load.is_complete else "  (partial - run ended before refill)"
            lines.append(
                f"    #{load.index + 1}  {load.area_ha:6.3f} ha  "
                f"{load.volume_l:6.0f} L  {load.rate_l_per_ha:6.1f} L/ha{flag}"
            )

    if result.gaps:
        lines += ["", "LARGEST MISSED PATCHES"]
        for i, gap in enumerate(result.gaps[:10], 1):
            lines.append(
                f"  {i:2d}. {gap.area_m2:8.0f} m^2   up to {gap.max_width_m:5.1f} m wide   "
                f"{gap.lat:.6f}, {gap.lon:.6f}"
            )

    lines += [
        "",
        "WORK",
        f"  Spraying runs:  {len(result.passes):8d}",
        f"  Spraying time:  {result.spraying_time_s / 3600:8.2f} h",
        f"  Distance:       {result.total_distance_m / 1000:8.2f} km "
        f"({result.transport_distance_m / 1000:.2f} km transport)",
    ]

    if result.warnings:
        lines += ["", "WARNINGS"]
        lines += [f"  - {w}" for w in result.warnings]

    return "\n".join(lines)
