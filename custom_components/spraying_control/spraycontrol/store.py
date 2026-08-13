"""Persistent projects: a garden, its tracks, and its aerial imagery.

Everything used to be one-shot - upload a track, read the numbers, lose them.
A project keeps the parts that do not change (where you refill, how wide you
spray, the plot boundary, the picture of the garden) so each new session is
just another file dropped in, and any set of sessions can be looked at together.

Layout on disk, one directory per project:

    <root>/<project-id>/
        project.json      settings, plot boundary, overlay placement
        tracks/<id>.<ext> the uploaded files, untouched
        overlays/<id>.<ext>
"""

from __future__ import annotations

import json
import re
import shutil
import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from .imagery import ImageError, Placement, guess_placement, image_size
from .models import BaseLocation, SprayerConfig
from .parsers import parse_track

SCHEMA_VERSION = 1
_SAFE = re.compile(r"[^a-zA-Z0-9._-]+")


def _new_id() -> str:
    return uuid.uuid4().hex[:12]


def _safe_suffix(filename: str) -> str:
    suffix = Path(filename or "").suffix.lower()
    return _SAFE.sub("", suffix)[:12] or ".dat"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass
class TrackRecord:
    """One uploaded session."""

    id: str
    name: str
    filename: str
    added: str
    start_t: float = 0.0
    end_t: float = 0.0
    n_points: int = 0
    enabled: bool = True
    note: str = ""

    @property
    def started(self) -> str:
        if not self.start_t:
            return ""
        return datetime.fromtimestamp(self.start_t, timezone.utc).isoformat()


@dataclass
class OverlayRecord:
    """An aerial picture and where it sits on the ground."""

    id: str
    name: str
    filename: str
    added: str
    width_px: int
    height_px: int
    placement: dict  # Placement.as_dict()
    source: str = ""  # how the first guess was reached
    opacity: float = 1.0
    enabled: bool = True

    def as_placement(self) -> Placement:
        return Placement.from_dict(self.placement)


@dataclass
class Project:
    """A garden: its settings, its sessions and its imagery."""

    id: str
    name: str
    created: str
    updated: str
    config: dict = field(default_factory=dict)
    base: dict | None = None
    field_rings: list = field(default_factory=list)  # [[[lon, lat], ...], ...]
    tracks: list[TrackRecord] = field(default_factory=list)
    overlays: list[OverlayRecord] = field(default_factory=list)
    schema: int = SCHEMA_VERSION

    def sprayer_config(self) -> SprayerConfig:
        allowed = SprayerConfig.__dataclass_fields__
        return SprayerConfig(**{k: v for k, v in self.config.items() if k in allowed})

    def base_location(self) -> BaseLocation | None:
        if not self.base:
            return None
        lat, lon = self.base.get("lat"), self.base.get("lon")
        if lat is None or lon is None:
            return None
        return BaseLocation(
            lat=float(lat),
            lon=float(lon),
            radius_m=float(self.base.get("radius_m", 8.0)),
            min_dwell_s=float(self.base.get("min_dwell_s", 60.0)),
        )

    def track(self, track_id: str) -> TrackRecord | None:
        return next((t for t in self.tracks if t.id == track_id), None)

    def overlay(self, overlay_id: str) -> OverlayRecord | None:
        return next((o for o in self.overlays if o.id == overlay_id), None)


class ProjectStore:
    """Reads and writes projects under a root directory."""

    def __init__(self, root: str | Path) -> None:
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)

    # --- paths ---

    def _dir(self, project_id: str) -> Path:
        safe = _SAFE.sub("", project_id)
        if not safe:
            raise KeyError("bad project id")
        return self.root / safe

    def _json(self, project_id: str) -> Path:
        return self._dir(project_id) / "project.json"

    def track_path(self, project_id: str, record: TrackRecord) -> Path:
        return self._dir(project_id) / "tracks" / record.filename

    def overlay_path(self, project_id: str, record: OverlayRecord) -> Path:
        return self._dir(project_id) / "overlays" / record.filename

    # --- project lifecycle ---

    def list_projects(self) -> list[Project]:
        out = []
        for path in sorted(self.root.iterdir()):
            if (path / "project.json").is_file():
                try:
                    out.append(self.load(path.name))
                except (OSError, ValueError, KeyError):
                    continue
        out.sort(key=lambda p: p.updated, reverse=True)
        return out

    def create(self, name: str = "My garden") -> Project:
        project_id = _new_id()
        now = _now()
        project = Project(
            id=project_id,
            name=name.strip() or "My garden",
            created=now,
            updated=now,
            config=asdict(SprayerConfig()),
        )
        (self._dir(project_id) / "tracks").mkdir(parents=True, exist_ok=True)
        (self._dir(project_id) / "overlays").mkdir(parents=True, exist_ok=True)
        self.save(project)
        return project

    def load(self, project_id: str) -> Project:
        path = self._json(project_id)
        if not path.is_file():
            raise KeyError(f"no project {project_id}")
        raw = json.loads(path.read_text())
        raw["tracks"] = [TrackRecord(**t) for t in raw.get("tracks", [])]
        raw["overlays"] = [OverlayRecord(**o) for o in raw.get("overlays", [])]
        known = Project.__dataclass_fields__
        return Project(**{k: v for k, v in raw.items() if k in known})

    def save(self, project: Project) -> Project:
        project.updated = _now()
        path = self._json(project.id)
        path.parent.mkdir(parents=True, exist_ok=True)
        # Write beside the target then move, so a crash cannot leave a torn file.
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(asdict(project), indent=2))
        tmp.replace(path)
        return project

    def delete(self, project_id: str) -> None:
        shutil.rmtree(self._dir(project_id), ignore_errors=True)

    # --- tracks ---

    def add_track(
        self, project: Project, data: bytes, filename: str, name: str = ""
    ) -> TrackRecord:
        """Store an uploaded track, reading its span so it can be listed."""
        track = parse_track(data, filename)  # raises TrackParseError on rubbish

        record = TrackRecord(
            id=_new_id(),
            name=name.strip() or track.name or Path(filename).stem or "Session",
            filename="",
            added=_now(),
            start_t=float(track.t[0]),
            end_t=float(track.t[-1]),
            n_points=len(track),
        )
        record.filename = f"{record.id}{_safe_suffix(filename)}"

        target = self.track_path(project.id, record)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)

        project.tracks.append(record)
        project.tracks.sort(key=lambda t: t.start_t)
        self.save(project)
        return record

    def read_track(self, project: Project, record: TrackRecord):
        """Parse a stored track back into memory."""
        data = self.track_path(project.id, record).read_bytes()
        track = parse_track(data, record.filename)
        track.name = record.name
        return track

    def load_tracks(self, project: Project, ids: list[str] | None = None) -> list:
        """Parse the chosen sessions, or every enabled one."""
        wanted = [t for t in project.tracks if (t.id in ids if ids else t.enabled)]
        return [self.read_track(project, r) for r in wanted]

    def remove_track(self, project: Project, track_id: str) -> bool:
        record = project.track(track_id)
        if record is None:
            return False
        self.track_path(project.id, record).unlink(missing_ok=True)
        project.tracks = [t for t in project.tracks if t.id != track_id]
        self.save(project)
        return True

    # --- overlays ---

    def add_overlay(
        self,
        project: Project,
        data: bytes,
        filename: str,
        name: str = "",
        world_file: str | None = None,
        fallback_centre: tuple[float, float] | None = None,
        default_width_m: float = 60.0,
        centre_label: str = "centred on your tracks",
    ) -> OverlayRecord:
        """Store an aerial picture with a first guess at where it belongs."""
        width_px, height_px = image_size(data)
        placement, source = guess_placement(
            data,
            fallback_centre=fallback_centre,
            default_width_m=default_width_m,
            world_file=world_file,
            centre_label=centre_label,
        )

        record = OverlayRecord(
            id=_new_id(),
            name=name.strip() or Path(filename).stem or "Aerial photo",
            filename="",
            added=_now(),
            width_px=width_px,
            height_px=height_px,
            placement=placement.as_dict(),
            source=source,
        )
        record.filename = f"{record.id}{_safe_suffix(filename)}"

        target = self.overlay_path(project.id, record)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)

        project.overlays.append(record)
        self.save(project)
        return record

    def read_overlay(self, project: Project, record: OverlayRecord) -> bytes:
        return self.overlay_path(project.id, record).read_bytes()

    def remove_overlay(self, project: Project, overlay_id: str) -> bool:
        record = project.overlay(overlay_id)
        if record is None:
            return False
        self.overlay_path(project.id, record).unlink(missing_ok=True)
        project.overlays = [o for o in project.overlays if o.id != overlay_id]
        self.save(project)
        return True

    def tracks_centre(self, project: Project) -> tuple[float, float] | None:
        """Middle of everything recorded so far, to centre a new picture on."""
        lats: list[float] = []
        lons: list[float] = []
        for record in project.tracks:
            try:
                track = self.read_track(project, record)
            except (OSError, ValueError):
                continue
            lats.extend((float(track.lat.min()), float(track.lat.max())))
            lons.extend((float(track.lon.min()), float(track.lon.max())))
        if not lats:
            return None
        return ((min(lats) + max(lats)) / 2.0, (min(lons) + max(lons)) / 2.0)


__all__ = [
    "ImageError",
    "OverlayRecord",
    "Project",
    "ProjectStore",
    "TrackRecord",
]
