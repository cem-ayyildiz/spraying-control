"""FastAPI application: upload a track, look at the coverage, push to HA."""

from __future__ import annotations

import json
import uuid
from collections import OrderedDict
from datetime import datetime, timezone
from pathlib import Path

from fastapi import FastAPI, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles

from ..analyze import analyze
from ..demo import synthetic_run
from ..models import M2_PER_HA, AnalysisResult, BaseLocation, PointState, SprayerConfig
from ..parsers import TrackParseError, parse_track
from ..report import COVERAGE_COLORS, COVERAGE_LABELS, to_geojson
from ..segment import segment_track

STATIC_DIR = Path(__file__).parent / "static"
MAX_RESULTS = 20
MAX_POLYLINE_POINTS = 4000

app = FastAPI(title="Spraying Control", docs_url=None, redoc_url=None)

# Most recent analyses, so the overlay and exports can be fetched separately.
_results: "OrderedDict[str, tuple[AnalysisResult, dict]]" = OrderedDict()


def _remember(result: AnalysisResult, payload: dict) -> None:
    _results[payload["id"]] = (result, payload)
    while len(_results) > MAX_RESULTS:
        _results.popitem(last=False)


def _get(result_id: str) -> tuple[AnalysisResult, dict]:
    if result_id not in _results:
        raise HTTPException(404, "result not found or expired; run the analysis again")
    return _results[result_id]


def _f(value: str | None, default: float) -> float:
    if value is None or value == "":
        return default
    try:
        return float(value)
    except ValueError:
        raise HTTPException(400, f"expected a number, got {value!r}")


def _polylines(track, cfg, base) -> dict:
    """Simplified track geometry for drawing, split by what the machine was doing."""
    seg = segment_track(track, cfg, base)
    step = max(1, len(seg.track) // MAX_POLYLINE_POINTS)

    lines: dict[str, list] = {"spraying": [], "transport": []}
    groups = {PointState.SPRAYING: "spraying", PointState.TRANSPORT: "transport"}
    for state, key in groups.items():
        current: list = []
        for i, s in enumerate(seg.seg_state):
            if s == state:
                if not current:
                    current.append([round(float(seg.track.lat[i]), 7), round(float(seg.track.lon[i]), 7)])
                current.append([round(float(seg.track.lat[i + 1]), 7), round(float(seg.track.lon[i + 1]), 7)])
            elif current:
                lines[key].append(current[::step] if step > 1 else current)
                current = []
        if current:
            lines[key].append(current[::step] if step > 1 else current)
    return lines


def _build_payload(result: AnalysisResult, track, cfg, base) -> dict:
    result_id = uuid.uuid4().hex[:12]
    south, west, north, east = result.bounds
    cov = result.coverage

    return {
        "id": result_id,
        "summary": result.summary(),
        "bounds": [[south, west], [north, east]],
        "has_overlay": result.overlay_png is not None,
        "legend": [
            {"label": label, "color": f"rgba({c[0]},{c[1]},{c[2]},{c[3] / 255:.2f})"}
            for label, c in zip(COVERAGE_LABELS, COVERAGE_COLORS)
        ],
        "base": (
            {"lat": base.lat, "lon": base.lon, "radius_m": base.radius_m}
            if base is not None
            else None
        ),
        "loads": [
            {
                "index": load.index + 1,
                "area_ha": round(load.area_ha, 3),
                "volume_l": round(load.volume_l, 1),
                "rate_l_per_ha": round(load.rate_l_per_ha, 1),
                "complete": load.is_complete,
                "start": datetime.fromtimestamp(load.start_t, timezone.utc).isoformat(),
                "end": datetime.fromtimestamp(load.end_t, timezone.utc).isoformat(),
            }
            for load in result.loads
        ],
        "gaps": [
            {
                "area_m2": round(gap.area_m2),
                "max_width_m": round(gap.max_width_m, 1),
                "lat": gap.lat,
                "lon": gap.lon,
                "polygon": gap.polygon,
            }
            for gap in result.gaps
        ],
        "visits": [
            {
                "start": datetime.fromtimestamp(v.start_t, timezone.utc).isoformat(),
                "minutes": round(v.duration_s / 60, 1),
            }
            for v in result.visits
        ],
        "histogram": [
            {"passes": k, "area_ha": round(v / M2_PER_HA, 4)}
            for k, v in sorted(cov.histogram.items())
        ],
        "warnings": result.warnings,
        "track": _polylines(track, cfg, base),
    }


async def _run(
    track,
    cfg: SprayerConfig,
    base: BaseLocation | None,
    field_geojson: str | None,
) -> JSONResponse:
    rings = _parse_field(field_geojson)
    try:
        result = analyze(track, cfg, base, rings, render=True)
    except ValueError as exc:
        raise HTTPException(400, str(exc))

    payload = _build_payload(result, track, cfg, base)
    _remember(result, payload)
    return JSONResponse(payload)


def _parse_field(field_geojson: str | None) -> list | None:
    if not field_geojson or field_geojson.strip() in ("", "null", "[]"):
        return None
    try:
        obj = json.loads(field_geojson)
    except json.JSONDecodeError as exc:
        raise HTTPException(400, f"field boundary is not valid JSON: {exc}")

    rings: list = []
    if isinstance(obj, list) and obj and isinstance(obj[0], list):
        # A bare list of rings, which is what the map drawing tool sends.
        rings = obj
    else:
        def collect(geom):
            if not isinstance(geom, dict):
                return
            if geom.get("type") == "Polygon":
                rings.append(geom["coordinates"][0])
            elif geom.get("type") == "MultiPolygon":
                rings.extend(poly[0] for poly in geom["coordinates"])

        if obj.get("type") == "FeatureCollection":
            for feat in obj.get("features", []):
                collect(feat.get("geometry"))
        elif obj.get("type") == "Feature":
            collect(obj.get("geometry"))
        else:
            collect(obj)

    if not rings:
        raise HTTPException(400, "no polygon found in the field boundary")
    return rings


def _config(
    swath: str | None,
    tank: str | None,
    min_speed: str | None,
    max_speed: str | None,
    max_gap: str | None,
    max_accuracy: str | None,
    cell: str | None,
    min_gap_area: str | None,
) -> SprayerConfig:
    try:
        return SprayerConfig(
            swath_width_m=_f(swath, 1.0),
            tank_capacity_l=_f(tank, 18.0),
            min_speed_kmh=_f(min_speed, 0.4),
            max_speed_kmh=_f(max_speed, 4.5),
            max_gap_s=_f(max_gap, 45.0),
            max_accuracy_m=_f(max_accuracy, 25.0),
            cell_size_m=_f(cell, 0.25),
            min_gap_area_m2=_f(min_gap_area, 2.0),
        )
    except ValueError as exc:
        raise HTTPException(400, str(exc))


def _base(
    base_lat: str | None,
    base_lon: str | None,
    base_radius: str | None,
    base_dwell: str | None,
) -> BaseLocation | None:
    if not base_lat or not base_lon:
        return None
    return BaseLocation(
        lat=_f(base_lat, 0.0),
        lon=_f(base_lon, 0.0),
        radius_m=_f(base_radius, 8.0),
        min_dwell_s=_f(base_dwell, 60.0),
    )


@app.post("/api/analyze")
async def api_analyze(
    file: UploadFile | None = None,
    source: str = Form("file"),
    swath: str = Form(None),
    tank: str = Form(None),
    base_lat: str = Form(None),
    base_lon: str = Form(None),
    base_radius: str = Form(None),
    base_dwell: str = Form(None),
    min_speed: str = Form(None),
    max_speed: str = Form(None),
    max_gap: str = Form(None),
    max_accuracy: str = Form(None),
    cell: str = Form(None),
    min_gap_area: str = Form(None),
    field: str = Form(None),
    entity: str = Form(None),
    day: str = Form(None),
    tz_offset: str = Form(None),
):
    cfg = _config(swath, tank, min_speed, max_speed, max_gap, max_accuracy, cell, min_gap_area)
    base = _base(base_lat, base_lon, base_radius, base_dwell)

    if source == "demo":
        track, demo_base, _ = synthetic_run(swath_width_m=cfg.swath_width_m)
        base = base or demo_base
    elif source == "ha":
        from ..ha import HAClient, HAError, day_bounds

        if not entity:
            raise HTTPException(400, "choose a device tracker")
        try:
            with HAClient.from_env() as client:
                start, end = day_bounds(day or None, _f(tz_offset, 0.0))
                track = client.fetch_track(entity, start, end)
        except HAError as exc:
            raise HTTPException(502, str(exc))
        if len(track) < 2:
            raise HTTPException(400, f"{entity} reported fewer than 2 positions in that period")
    else:
        if file is None:
            raise HTTPException(400, "no file uploaded")
        data = await file.read()
        if not data:
            raise HTTPException(400, "uploaded file is empty")
        try:
            track = parse_track(data, file.filename or "")
        except TrackParseError as exc:
            raise HTTPException(400, f"could not read the track: {exc}")

    return await _run(track, cfg, base, field)


@app.get("/api/result/{result_id}/overlay.png")
async def api_overlay(result_id: str):
    result, _ = _get(result_id)
    if not result.overlay_png:
        raise HTTPException(404, "no overlay for this result")
    return Response(result.overlay_png, media_type="image/png", headers={"Cache-Control": "max-age=3600"})


@app.get("/api/result/{result_id}/geojson")
async def api_geojson(result_id: str):
    result, _ = _get(result_id)
    return JSONResponse(
        to_geojson(result),
        headers={"Content-Disposition": f'attachment; filename="spray-{result_id}.geojson"'},
    )


@app.get("/api/result/{result_id}/summary.json")
async def api_summary(result_id: str):
    result, _ = _get(result_id)
    return JSONResponse(
        result.summary(),
        headers={"Content-Disposition": f'attachment; filename="spray-{result_id}.json"'},
    )


@app.get("/api/ha/status")
async def api_ha_status():
    from ..ha import HAClient, HAError

    try:
        with HAClient.from_env() as client:
            version = client.ping()
            trackers = client.list_device_trackers()
        return {"available": True, "version": version, "trackers": trackers}
    except HAError as exc:
        return {"available": False, "error": str(exc), "trackers": []}


@app.post("/api/ha/push")
async def api_push(result_id: str = Form(...), prefix: str = Form("spray")):
    from ..ha import HAClient, HAError

    result, _ = _get(result_id)
    safe = "".join(c if c.isalnum() or c == "_" else "_" for c in prefix.strip().lower()) or "spray"
    try:
        with HAClient.from_env() as client:
            created = client.push_result(result, safe)
    except HAError as exc:
        raise HTTPException(502, str(exc))
    return {"created": created}


@app.get("/api/defaults")
async def api_defaults():
    """Form defaults, taken from the add-on options when running under HA."""
    import os

    def env(name: str, fallback):
        raw = os.environ.get(name)
        if raw in (None, ""):
            return fallback
        try:
            return float(raw)
        except ValueError:
            return fallback

    lat = env("SPRAY_BASE_LATITUDE", 0.0)
    lon = env("SPRAY_BASE_LONGITUDE", 0.0)
    # 0,0 is the schema default, meaning "not configured", not the Gulf of Guinea.
    has_base = not (abs(lat) < 1e-9 and abs(lon) < 1e-9)

    return {
        "addon": os.environ.get("SPRAYCONTROL_ADDON") == "1",
        "swath": env("SPRAY_SWATH_WIDTH_M", 1.0),
        "tank": env("SPRAY_TANK_CAPACITY_L", 18.0),
        "base_lat": lat if has_base else None,
        "base_lon": lon if has_base else None,
        "base_radius": env("SPRAY_BASE_RADIUS_M", 8.0),
        "base_dwell": env("SPRAY_BASE_MIN_STOP_S", 60.0),
        "min_speed": env("SPRAY_MIN_SPEED_KMH", 0.4),
        "max_speed": env("SPRAY_MAX_SPEED_KMH", 4.5),
        "prefix": os.environ.get("SPRAY_SENSOR_PREFIX", "spray"),
    }


@app.get("/api/health")
async def api_health():
    return {"status": "ok"}


@app.get("/")
async def index():
    return FileResponse(STATIC_DIR / "index.html")


app.mount("/", StaticFiles(directory=STATIC_DIR), name="static")
