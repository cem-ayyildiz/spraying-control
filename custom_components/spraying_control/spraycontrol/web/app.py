"""FastAPI application: a garden, its sessions, its aerial photo, its coverage.

The interface is built around a *project* - one garden, kept on disk - so that
settings are entered once and every session you add sits alongside the last.
Analysing several sessions together is then just a matter of ticking them.
"""

from __future__ import annotations

import json
import os
import uuid
from collections import OrderedDict
from datetime import datetime, timezone
from pathlib import Path

from fastapi import FastAPI, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles

from ..analyze import analyze
from ..demo import synthetic_run, to_gpx
from ..imagery import ImageError, Placement
from ..models import M2_PER_HA, AnalysisResult, PointState, SprayerConfig
from ..parsers import TrackParseError
from ..report import COVERAGE_COLORS, COVERAGE_LABELS, to_geojson
from ..segment import segment_track
from ..store import Project, ProjectStore

STATIC_DIR = Path(__file__).parent / "static"
MAX_RESULTS = 20
MAX_POLYLINE_POINTS = 4000

IMAGE_TYPES = {".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg"}
TRACK_SUFFIXES = {".gpx", ".csv", ".tsv", ".txt", ".kml", ".geojson", ".json"}
IMAGE_SUFFIXES = set(IMAGE_TYPES)

app = FastAPI(title="Spraying Control", docs_url=None, redoc_url=None)

_results: "OrderedDict[str, AnalysisResult]" = OrderedDict()


def data_dir() -> Path:
    """Where projects live.

    Inside an add-on or the standalone image /data is the persistent volume;
    elsewhere fall back to the user's data directory so nothing is lost between
    runs.
    """
    configured = os.environ.get("SPRAY_DATA_DIR")
    if configured:
        return Path(configured)
    if Path("/data").is_dir() and os.access("/data", os.W_OK):
        return Path("/data/projects")
    return Path.home() / ".local" / "share" / "spraycontrol" / "projects"


_store: ProjectStore | None = None


def store() -> ProjectStore:
    global _store
    if _store is None:
        _store = ProjectStore(data_dir())
    return _store


# --- helpers ---------------------------------------------------------------


def _project(project_id: str) -> Project:
    try:
        return store().load(project_id)
    except KeyError:
        raise HTTPException(404, "no such project")


def _remember(result: AnalysisResult) -> str:
    result_id = uuid.uuid4().hex[:12]
    _results[result_id] = result
    while len(_results) > MAX_RESULTS:
        _results.popitem(last=False)
    return result_id


def _iso(epoch: float) -> str:
    return datetime.fromtimestamp(epoch, timezone.utc).isoformat()


def _f(value: str | None, default: float) -> float:
    """A number from a form field, falling back when it is absent or rubbish."""
    if value is None or value == "":
        return default
    try:
        return float(value)
    except ValueError:
        return default


def _project_json(project: Project) -> dict:
    return {
        "id": project.id,
        "name": project.name,
        "created": project.created,
        "updated": project.updated,
        "config": project.config,
        "base": project.base,
        "field_rings": project.field_rings,
        "tracks": [
            {
                "id": t.id,
                "name": t.name,
                "added": t.added,
                "start": _iso(t.start_t) if t.start_t else "",
                "end": _iso(t.end_t) if t.end_t else "",
                "n_points": t.n_points,
                "enabled": t.enabled,
                "note": t.note,
            }
            for t in project.tracks
        ],
        "overlays": [
            {
                "id": o.id,
                "name": o.name,
                "width_px": o.width_px,
                "height_px": o.height_px,
                "placement": o.placement,
                "source": o.source,
                "opacity": o.opacity,
                "enabled": o.enabled,
                "url": f"api/projects/{project.id}/overlays/{o.id}/image",
            }
            for o in project.overlays
        ],
    }


def _polylines(tracks, cfg, base) -> dict:
    """Simplified geometry for drawing, split by what the walker was doing."""
    lines: dict[str, list] = {"spraying": [], "transport": []}
    groups = {PointState.SPRAYING: "spraying", PointState.TRANSPORT: "transport"}
    for track in tracks:
        try:
            seg = segment_track(track, cfg, base)
        except ValueError:
            continue
        step = max(1, len(seg.track) // MAX_POLYLINE_POINTS)
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


def _result_json(result: AnalysisResult, result_id: str, tracks, cfg, base) -> dict:
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
        "sessions": [
            {
                "name": s.name,
                "start": _iso(s.start_t),
                "area_ha": round(s.area_ha, 4),
                "area_m2": round(s.area_m2, 1),
                "new_area_m2": round(s.new_area_m2, 1),
                "repeat_area_m2": round(s.repeat_area_m2, 1),
                "distance_m": round(s.distance_m),
                "volume_l": round(s.volume_l, 1),
                "loads": s.n_loads,
            }
            for s in result.tracks
        ],
        "loads": [
            {
                "index": load.index + 1,
                "track": load.track_name,
                "area_ha": round(load.area_ha, 4),
                "volume_l": round(load.volume_l, 1),
                "rate_l_per_ha": round(load.rate_l_per_ha, 1),
                "complete": load.is_complete,
            }
            for load in result.loads
        ],
        "gaps": [
            {
                "area_m2": round(g.area_m2),
                "max_width_m": round(g.max_width_m, 1),
                "lat": g.lat,
                "lon": g.lon,
                "polygon": g.polygon,
            }
            for g in result.gaps
        ],
        "histogram": [
            {"passes": k, "area_ha": round(v / M2_PER_HA, 4)}
            for k, v in sorted(cov.histogram.items())
        ],
        "warnings": result.warnings,
        "track": _polylines(tracks, cfg, base),
    }


# --- projects ---------------------------------------------------------------


@app.get("/api/projects")
async def api_projects():
    projects = store().list_projects()
    if not projects:
        projects = [store().create()]
    return {
        "projects": [
            {
                "id": p.id,
                "name": p.name,
                "updated": p.updated,
                "n_tracks": len(p.tracks),
                "n_overlays": len(p.overlays),
            }
            for p in projects
        ]
    }


@app.post("/api/projects")
async def api_create_project(name: str = Form("My garden")):
    return _project_json(store().create(name))


@app.get("/api/projects/{project_id}")
async def api_project(project_id: str):
    return _project_json(_project(project_id))


@app.patch("/api/projects/{project_id}")
async def api_update_project(
    project_id: str,
    name: str = Form(None),
    config: str = Form(None),
    base: str = Form(None),
    field_rings: str = Form(None),
):
    project = _project(project_id)
    if name is not None:
        project.name = name.strip() or project.name
    if config is not None:
        try:
            incoming = json.loads(config)
        except json.JSONDecodeError as err:
            raise HTTPException(400, f"config is not valid JSON: {err}")
        merged = {**project.config, **incoming}
        try:
            SprayerConfig(**{k: v for k, v in merged.items() if k in SprayerConfig.__dataclass_fields__})
        except (TypeError, ValueError) as err:
            raise HTTPException(400, str(err))
        project.config = merged
    if base is not None:
        project.base = json.loads(base) if base.strip() not in ("", "null") else None
    if field_rings is not None:
        rings = json.loads(field_rings) if field_rings.strip() not in ("", "null") else []
        project.field_rings = rings
    return _project_json(store().save(project))


@app.delete("/api/projects/{project_id}")
async def api_delete_project(project_id: str):
    store().delete(project_id)
    return {"deleted": project_id}


# --- tracks -----------------------------------------------------------------


@app.post("/api/projects/{project_id}/tracks")
async def api_add_tracks(project_id: str, files: list[UploadFile]):
    """Take one or many dropped files at once."""
    project = _project(project_id)
    added, failed = [], []
    for upload in files:
        data = await upload.read()
        if not data:
            failed.append({"name": upload.filename, "error": "empty file"})
            continue
        try:
            record = store().add_track(project, data, upload.filename or "track")
        except (TrackParseError, ValueError) as err:
            failed.append({"name": upload.filename, "error": str(err)})
            continue
        added.append(record.id)
    if not added and failed:
        raise HTTPException(400, "; ".join(f"{f['name']}: {f['error']}" for f in failed))
    return {"added": added, "failed": failed, "project": _project_json(project)}


@app.patch("/api/projects/{project_id}/tracks/{track_id}")
async def api_update_track(
    project_id: str,
    track_id: str,
    enabled: str = Form(None),
    name: str = Form(None),
):
    project = _project(project_id)
    record = project.track(track_id)
    if record is None:
        raise HTTPException(404, "no such session")
    if enabled is not None:
        record.enabled = enabled.lower() in ("1", "true", "yes", "on")
    if name is not None and name.strip():
        record.name = name.strip()
    return _project_json(store().save(project))


@app.delete("/api/projects/{project_id}/tracks/{track_id}")
async def api_delete_track(project_id: str, track_id: str):
    project = _project(project_id)
    if not store().remove_track(project, track_id):
        raise HTTPException(404, "no such session")
    return _project_json(project)


# --- overlays ---------------------------------------------------------------


@app.post("/api/projects/{project_id}/overlays")
async def api_add_overlay(
    project_id: str,
    file: UploadFile,
    world_file: UploadFile | None = None,
    name: str = Form(""),
    centre_lat: str = Form(None),
    centre_lon: str = Form(None),
    width_m: str = Form(None),
):
    project = _project(project_id)
    data = await file.read()
    if not data:
        raise HTTPException(400, "the image is empty")

    world_text = None
    if world_file is not None:
        world_text = (await world_file.read()).decode("utf-8", "replace")

    # Prefer the tracks, then whatever the user is looking at. Without the
    # second fallback a first photo in an empty garden has nowhere to go.
    centre = store().tracks_centre(project)
    centre_label = "centred on your tracks"
    if centre is None and centre_lat and centre_lon:
        try:
            centre = (float(centre_lat), float(centre_lon))
            centre_label = "centred on your view"
        except ValueError:
            centre = None

    try:
        record = store().add_overlay(
            project,
            data,
            file.filename or "aerial.png",
            name=name,
            world_file=world_text,
            fallback_centre=centre,
            default_width_m=_f(width_m, 60.0),
            centre_label=centre_label,
        )
    except ImageError as err:
        raise HTTPException(400, str(err))
    return {"overlay_id": record.id, "project": _project_json(project)}


@app.patch("/api/projects/{project_id}/overlays/{overlay_id}")
async def api_update_overlay(
    project_id: str,
    overlay_id: str,
    placement: str = Form(None),
    opacity: str = Form(None),
    enabled: str = Form(None),
    name: str = Form(None),
):
    project = _project(project_id)
    record = project.overlay(overlay_id)
    if record is None:
        raise HTTPException(404, "no such photo")
    if placement is not None:
        try:
            record.placement = Placement.from_dict(json.loads(placement)).as_dict()
        except (json.JSONDecodeError, KeyError, TypeError, ValueError) as err:
            raise HTTPException(400, f"bad placement: {err}")
    if opacity is not None:
        record.opacity = max(0.0, min(1.0, float(opacity)))
    if enabled is not None:
        record.enabled = enabled.lower() in ("1", "true", "yes", "on")
    if name is not None and name.strip():
        record.name = name.strip()
    return _project_json(store().save(project))


@app.get("/api/projects/{project_id}/overlays/{overlay_id}/image")
async def api_overlay_image(project_id: str, overlay_id: str):
    project = _project(project_id)
    record = project.overlay(overlay_id)
    if record is None:
        raise HTTPException(404, "no such photo")
    media = IMAGE_TYPES.get(Path(record.filename).suffix.lower(), "application/octet-stream")
    try:
        data = store().read_overlay(project, record)
    except OSError:
        raise HTTPException(404, "the image file is missing")
    return Response(data, media_type=media, headers={"Cache-Control": "max-age=86400"})


@app.delete("/api/projects/{project_id}/overlays/{overlay_id}")
async def api_delete_overlay(project_id: str, overlay_id: str):
    project = _project(project_id)
    if not store().remove_overlay(project, overlay_id):
        raise HTTPException(404, "no such photo")
    return _project_json(project)


# --- analysis ---------------------------------------------------------------


@app.post("/api/projects/{project_id}/analyze")
async def api_analyze_project(project_id: str, track_ids: str = Form(None)):
    project = _project(project_id)
    ids = [i for i in (track_ids or "").split(",") if i] or None

    try:
        tracks = store().load_tracks(project, ids)
    except (OSError, TrackParseError) as err:
        raise HTTPException(400, f"could not read a session: {err}")
    if not tracks:
        raise HTTPException(400, "no sessions selected")

    cfg = project.sprayer_config()
    base = project.base_location()
    try:
        result = analyze(tracks, cfg, base, project.field_rings or None, render=True)
    except ValueError as err:
        raise HTTPException(400, str(err))

    return JSONResponse(_result_json(result, _remember(result), tracks, cfg, base))


@app.post("/api/demo")
async def api_demo():
    """A worked example, for a first look without any data."""
    track, base, cfg = synthetic_run()
    project = store().create("Demo garden")
    project.config = {**project.config, "swath_width_m": cfg.swath_width_m, "tank_capacity_l": cfg.tank_capacity_l}
    project.base = {"lat": base.lat, "lon": base.lon, "radius_m": base.radius_m, "min_dwell_s": base.min_dwell_s}
    store().save(project)
    store().add_track(project, to_gpx(track).encode(), "demo-walk.gpx", name="Demo walk")
    return _project_json(project)


@app.get("/api/result/{result_id}/overlay.png")
async def api_overlay(result_id: str):
    result = _results.get(result_id)
    if result is None or not result.overlay_png:
        raise HTTPException(404, "no overlay for this result")
    return Response(result.overlay_png, media_type="image/png", headers={"Cache-Control": "max-age=3600"})


@app.get("/api/result/{result_id}/geojson")
async def api_geojson(result_id: str):
    result = _results.get(result_id)
    if result is None:
        raise HTTPException(404, "result expired; analyse again")
    return JSONResponse(
        to_geojson(result),
        headers={"Content-Disposition": f'attachment; filename="spray-{result_id}.geojson"'},
    )


@app.get("/api/result/{result_id}/summary.json")
async def api_summary(result_id: str):
    result = _results.get(result_id)
    if result is None:
        raise HTTPException(404, "result expired; analyse again")
    return JSONResponse(
        result.summary(),
        headers={"Content-Disposition": f'attachment; filename="spray-{result_id}.json"'},
    )


# --- Home Assistant ---------------------------------------------------------


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


@app.post("/api/projects/{project_id}/import/ha")
async def api_import_ha(
    project_id: str,
    entity: str = Form(...),
    day: str = Form(None),
    tz_offset: str = Form("0"),
):
    """Pull a day of tracker history straight in as a session."""
    from ..ha import HAClient, HAError, day_bounds

    project = _project(project_id)
    try:
        with HAClient.from_env() as client:
            start, end = day_bounds(day or None, float(tz_offset or 0))
            track = client.fetch_track(entity, start, end)
    except HAError as exc:
        raise HTTPException(502, str(exc))
    if len(track) < 2:
        raise HTTPException(400, f"{entity} reported fewer than 2 positions that day")

    # Stored as GPX so the session file is readable and re-parsable like any other.
    name = f"{entity.split('.')[-1]} {day or 'today'}"
    record = store().add_track(project, to_gpx(track).encode(), f"{name}.gpx", name=name)
    return {"added": record.id, "project": _project_json(project)}


@app.post("/api/ha/push")
async def api_push(result_id: str = Form(...), prefix: str = Form("spray")):
    from ..ha import HAClient, HAError

    result = _results.get(result_id)
    if result is None:
        raise HTTPException(404, "result expired; analyse again")
    safe = "".join(c if c.isalnum() or c == "_" else "_" for c in prefix.strip().lower()) or "spray"
    try:
        with HAClient.from_env() as client:
            created = client.push_result(result, safe)
    except HAError as exc:
        raise HTTPException(502, str(exc))
    return {"created": created}


@app.get("/api/defaults")
async def api_defaults():
    """Machine defaults from the add-on options, for a brand-new project."""

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
        "data_dir": str(data_dir()),
    }


@app.get("/api/health")
async def api_health():
    return {"status": "ok"}


@app.get("/")
async def index():
    return FileResponse(STATIC_DIR / "index.html")


app.mount("/", StaticFiles(directory=STATIC_DIR), name="static")
