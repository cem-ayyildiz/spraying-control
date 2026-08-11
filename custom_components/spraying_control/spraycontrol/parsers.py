"""Readers for the track formats a sprayer log can arrive in.

Everything normalises to a :class:`~spraycontrol.models.Track`: epoch seconds,
degrees, metres per second.
"""

from __future__ import annotations

import csv
import io
import json
import re
from datetime import datetime, timezone
from xml.etree import ElementTree as ET

import numpy as np

from .models import Track

__all__ = ["parse_track", "parse_gpx", "parse_csv", "parse_geojson", "parse_kml", "parse_ha_history"]


class TrackParseError(ValueError):
    pass


def _to_epoch(value) -> float:
    """Parse the assorted timestamp spellings these formats use."""
    if value is None or value == "":
        return float("nan")
    if isinstance(value, (int, float)):
        v = float(value)
        # Heuristic: anything past ~2286 in seconds is really milliseconds.
        return v / 1000.0 if v > 1e11 else v
    s = str(value).strip()
    if not s:
        return float("nan")
    if re.fullmatch(r"-?\d+(\.\d+)?", s):
        v = float(s)
        return v / 1000.0 if v > 1e11 else v
    iso = s.replace("Z", "+00:00")
    # Trim sub-second precision beyond microseconds, which fromisoformat rejects.
    iso = re.sub(r"(\.\d{6})\d+", r"\1", iso)
    try:
        dt = datetime.fromisoformat(iso)
    except ValueError:
        for fmt in ("%Y-%m-%d %H:%M:%S", "%Y/%m/%d %H:%M:%S", "%d/%m/%Y %H:%M:%S"):
            try:
                dt = datetime.strptime(s, fmt)
                break
            except ValueError:
                continue
        else:
            raise TrackParseError(f"unrecognised timestamp: {value!r}")
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.timestamp()


def _localname(tag: str) -> str:
    return tag.rsplit("}", 1)[-1].lower()


def _finish(t, lat, lon, acc, spd, name, source) -> Track:
    if not lat:
        raise TrackParseError("no positions found in file")
    t = np.asarray(t, dtype=float)
    lat = np.asarray(lat, dtype=float)
    lon = np.asarray(lon, dtype=float)
    acc_arr = np.asarray(acc, dtype=float) if any(v is not None for v in acc) else None
    spd_arr = np.asarray(spd, dtype=float) if any(v is not None for v in spd) else None

    # Points with no usable timestamp cannot be turned into a swath.
    good = np.isfinite(t) & np.isfinite(lat) & np.isfinite(lon)
    good &= (np.abs(lat) <= 90) & (np.abs(lon) <= 180)
    # Drop the null island, a common logger artefact.
    good &= ~((np.abs(lat) < 1e-9) & (np.abs(lon) < 1e-9))
    if not good.any():
        raise TrackParseError("no positions with a valid timestamp")
    t, lat, lon = t[good], lat[good], lon[good]
    if acc_arr is not None:
        acc_arr = acc_arr[good]
    if spd_arr is not None:
        spd_arr = spd_arr[good]

    order = np.argsort(t, kind="stable")
    t, lat, lon = t[order], lat[order], lon[order]
    if acc_arr is not None:
        acc_arr = acc_arr[order]
    if spd_arr is not None:
        spd_arr = spd_arr[order]

    # Duplicate timestamps break every speed calculation downstream.
    keep = np.ones(t.shape[0], dtype=bool)
    keep[1:] = np.diff(t) > 0
    t, lat, lon = t[keep], lat[keep], lon[keep]
    if acc_arr is not None:
        acc_arr = acc_arr[keep]
    if spd_arr is not None:
        spd_arr = spd_arr[keep]

    return Track(t=t, lat=lat, lon=lon, accuracy=acc_arr, speed=spd_arr, name=name, source=source)


def parse_gpx(data: bytes | str, name: str = "") -> Track:
    root = ET.fromstring(data if isinstance(data, bytes) else data.encode())
    t, lat, lon, acc, spd = [], [], [], [], []
    track_name = name

    for el in root.iter():
        ln = _localname(el.tag)
        if ln == "name" and not track_name and el.text:
            track_name = el.text.strip()
        if ln not in ("trkpt", "rtept", "wpt"):
            continue
        try:
            la = float(el.attrib["lat"])
            lo = float(el.attrib["lon"])
        except (KeyError, ValueError):
            continue
        ts = float("nan")
        speed_v = None
        acc_v = None
        for child in el.iter():
            cln = _localname(child.tag)
            if cln == "time" and child.text:
                ts = _to_epoch(child.text)
            elif cln == "speed" and child.text:
                try:
                    speed_v = float(child.text)  # GPX speed is m/s
                except ValueError:
                    pass
            elif cln in ("hdop", "pdop") and child.text:
                try:
                    # Rough conversion; DOP alone is not an accuracy in metres,
                    # but it is the only quality hint GPX offers.
                    acc_v = float(child.text) * 5.0
                except ValueError:
                    pass
        t.append(ts)
        lat.append(la)
        lon.append(lo)
        acc.append(acc_v)
        spd.append(speed_v)

    return _finish(t, lat, lon, acc, spd, track_name or "GPX track", "gpx")


_COL_ALIASES = {
    "lat": ("lat", "latitude", "gps_lat", "y"),
    "lon": ("lon", "long", "lng", "longitude", "gps_lon", "x"),
    "time": ("time", "timestamp", "datetime", "date_time", "utc", "gps_time", "last_updated", "last_changed"),
    "acc": ("accuracy", "gps_accuracy", "hacc", "horizontal_accuracy", "epx"),
    "speed": ("speed", "velocity", "gps_speed"),
}


def _match_columns(header: list[str]) -> dict[str, int]:
    norm = [re.sub(r"[^a-z0-9]+", "_", h.strip().lower()).strip("_") for h in header]
    found: dict[str, int] = {}
    for key, aliases in _COL_ALIASES.items():
        for alias in aliases:
            if alias in norm:
                found[key] = norm.index(alias)
                break
    return found


def parse_csv(data: bytes | str, name: str = "") -> Track:
    text = data.decode("utf-8-sig", errors="replace") if isinstance(data, bytes) else data
    sample = text[:8192]
    try:
        dialect = csv.Sniffer().sniff(sample, delimiters=",;\t|")
    except csv.Error:
        dialect = csv.excel
    reader = csv.reader(io.StringIO(text), dialect)
    rows = list(reader)
    if not rows:
        raise TrackParseError("empty CSV")

    cols = _match_columns(rows[0])
    if "lat" not in cols or "lon" not in cols:
        raise TrackParseError(
            "CSV needs latitude and longitude columns "
            f"(looked for {_COL_ALIASES['lat']} / {_COL_ALIASES['lon']}); got {rows[0]!r}"
        )
    if "time" not in cols:
        raise TrackParseError(f"CSV needs a timestamp column (looked for {_COL_ALIASES['time']})")

    t, lat, lon, acc, spd = [], [], [], [], []

    def pick(row, key):
        idx = cols.get(key)
        if idx is None or idx >= len(row):
            return None
        val = row[idx].strip()
        return val or None

    for row in rows[1:]:
        if not row:
            continue
        try:
            la = float(pick(row, "lat"))
            lo = float(pick(row, "lon"))
        except (TypeError, ValueError):
            continue
        try:
            ts = _to_epoch(pick(row, "time"))
        except TrackParseError:
            continue
        a = pick(row, "acc")
        s = pick(row, "speed")
        t.append(ts)
        lat.append(la)
        lon.append(lo)
        acc.append(float(a) if a and _is_num(a) else None)
        spd.append(float(s) if s and _is_num(s) else None)

    return _finish(t, lat, lon, acc, spd, name or "CSV track", "csv")


def _is_num(s: str) -> bool:
    try:
        float(s)
        return True
    except ValueError:
        return False


def parse_geojson(data: bytes | str, name: str = "") -> Track:
    obj = json.loads(data)
    t, lat, lon, acc, spd = [], [], [], [], []

    def add(coord, props, idx):
        if not coord or len(coord) < 2:
            return
        lon.append(float(coord[0]))
        lat.append(float(coord[1]))
        ts = None
        for key in ("time", "timestamp", "t", "datetime"):
            if key in props:
                ts = props[key]
                break
        # A LineString may carry a parallel array of times in its properties.
        if ts is None and isinstance(props.get("coordTimes"), list) and idx < len(props["coordTimes"]):
            ts = props["coordTimes"][idx]
        t.append(_to_epoch(ts) if ts is not None else float("nan"))
        acc.append(props.get("accuracy"))
        spd.append(props.get("speed"))

    features = obj.get("features", [obj]) if isinstance(obj, dict) else []
    for feat in features:
        geom = feat.get("geometry", feat) or {}
        props = feat.get("properties", {}) or {}
        gtype = geom.get("type")
        if gtype == "Point":
            add(geom.get("coordinates"), props, 0)
        elif gtype in ("LineString", "MultiPoint"):
            for i, c in enumerate(geom.get("coordinates", [])):
                add(c, props, i)
        elif gtype == "MultiLineString":
            i = 0
            for line in geom.get("coordinates", []):
                for c in line:
                    add(c, props, i)
                    i += 1

    return _finish(t, lat, lon, acc, spd, name or "GeoJSON track", "geojson")


def parse_kml(data: bytes | str, name: str = "") -> Track:
    root = ET.fromstring(data if isinstance(data, bytes) else data.encode())
    t, lat, lon, acc, spd = [], [], [], [], []

    # gx:Track interleaves <when> and <gx:coord> as siblings.
    for el in root.iter():
        if _localname(el.tag) != "track":
            continue
        whens, coords = [], []
        for child in el:
            cln = _localname(child.tag)
            if cln == "when" and child.text:
                whens.append(_to_epoch(child.text))
            elif cln == "coord" and child.text:
                parts = child.text.split()
                if len(parts) >= 2:
                    coords.append((float(parts[0]), float(parts[1])))
        for i, (lo, la) in enumerate(coords):
            lon.append(lo)
            lat.append(la)
            t.append(whens[i] if i < len(whens) else float("nan"))
            acc.append(None)
            spd.append(None)

    if not lat:
        # Plain <coordinates> blocks carry no time; unusable for rate analysis
        # but we surface a clearer error than "no positions".
        for el in root.iter():
            if _localname(el.tag) == "coordinates" and el.text:
                if el.text.split():
                    raise TrackParseError(
                        "KML contains only <coordinates> without timestamps. "
                        "Export as gx:Track (Google Earth 'Save Place As' with time) or use GPX."
                    )

    return _finish(t, lat, lon, acc, spd, name or "KML track", "kml")


def parse_ha_history(data: bytes | str | list, name: str = "") -> Track:
    """Parse the payload of Home Assistant's ``/api/history/period`` for a
    ``device_tracker`` entity.

    The Companion app stores the position in the state *attributes*, so the
    history must be fetched without ``minimal_response``.
    """
    obj = json.loads(data) if isinstance(data, (bytes, str)) else data
    # The endpoint returns one list per requested entity.
    if isinstance(obj, list) and obj and isinstance(obj[0], list):
        states = [s for sub in obj for s in sub]
    elif isinstance(obj, list):
        states = obj
    elif isinstance(obj, dict):
        states = [s for sub in obj.values() for s in sub]
    else:
        raise TrackParseError("unrecognised Home Assistant history payload")

    t, lat, lon, acc, spd = [], [], [], [], []
    entity = name
    for st in states:
        if not isinstance(st, dict):
            continue
        attrs = st.get("a") or st.get("attributes") or {}
        la = attrs.get("latitude")
        lo = attrs.get("longitude")
        if la is None or lo is None:
            continue
        stamp = st.get("last_updated") or st.get("last_changed") or st.get("lu") or st.get("lc")
        if not entity:
            entity = st.get("entity_id", "")
        t.append(_to_epoch(stamp))
        lat.append(float(la))
        lon.append(float(lo))
        acc.append(attrs.get("gps_accuracy"))
        raw_speed = attrs.get("speed")
        # The Companion app reports -1 when speed is unavailable.
        spd.append(float(raw_speed) if isinstance(raw_speed, (int, float)) and raw_speed >= 0 else None)

    return _finish(t, lat, lon, acc, spd, entity or "HA history", "ha")


def parse_track(data: bytes, filename: str = "") -> Track:
    """Dispatch on extension, falling back to sniffing the content."""
    ext = filename.rsplit(".", 1)[-1].lower() if "." in filename else ""
    stem = filename.rsplit("/", 1)[-1]
    parsers = {
        "gpx": parse_gpx,
        "csv": parse_csv,
        "tsv": parse_csv,
        "txt": parse_csv,
        "geojson": parse_geojson,
        "kml": parse_kml,
    }
    if ext in parsers:
        return parsers[ext](data, stem)
    if ext == "json":
        try:
            return parse_ha_history(data, stem)
        except (TrackParseError, ValueError):
            return parse_geojson(data, stem)

    head = data[:2048].lstrip()
    if head.startswith(b"<"):
        low = head.lower()
        return parse_kml(data, stem) if b"kml" in low[:200] else parse_gpx(data, stem)
    if head.startswith((b"{", b"[")):
        for fn in (parse_ha_history, parse_geojson):
            try:
                return fn(data, stem)
            except (TrackParseError, ValueError, KeyError):
                continue
        raise TrackParseError("JSON did not look like HA history or GeoJSON")
    return parse_csv(data, stem)
