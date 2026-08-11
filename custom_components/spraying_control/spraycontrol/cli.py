"""Command line entry point."""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

from .analyze import analyze
from .demo import synthetic_run
from .models import BaseLocation, SprayerConfig
from .parsers import parse_track
from .report import format_text_report, to_geojson


def _add_machine_args(p: argparse.ArgumentParser) -> None:
    g = p.add_argument_group("sprayer")
    g.add_argument("--swath", "--boom", dest="swath", type=float, default=1.0, metavar="M",
                   help="spray band width in metres (default: 1.0)")
    g.add_argument("--tank", type=float, default=18.0, metavar="L", help="tank capacity in litres (default: 18)")
    g.add_argument("--base", metavar="LAT,LON", help="refill location; returns here are counted as refills")
    g.add_argument("--base-radius", type=float, default=8.0, metavar="M", help="base radius in metres (default: 8)")
    g.add_argument("--base-dwell", type=float, default=60.0, metavar="S", help="seconds stopped to count as a refill (default: 60)")

    a = p.add_argument_group("analysis")
    a.add_argument("--min-speed", type=float, default=0.4, metavar="KMH", help="below this the walker is paused (default: 0.4)")
    a.add_argument("--max-speed", type=float, default=4.5, metavar="KMH", help="above this they are walking, not spraying (default: 6.5)")
    a.add_argument("--max-gap", type=float, default=45.0, metavar="S", help="fix gap beyond which no band is drawn (default: 45)")
    a.add_argument("--max-accuracy", type=float, default=25.0, metavar="M", help="discard fixes worse than this (default: 25)")
    a.add_argument("--cell", type=float, default=0.25, metavar="M", help="grid cell size in metres (default: 0.25)")
    a.add_argument("--min-gap-area", type=float, default=2.0, metavar="M2", help="ignore misses smaller than this (default: 2)")
    a.add_argument("--field", metavar="FILE", help="GeoJSON plot boundary; without it the plot is inferred")

    o = p.add_argument_group("output")
    o.add_argument("--json", metavar="FILE", help="write the summary as JSON")
    o.add_argument("--geojson", metavar="FILE", help="write gaps and base as GeoJSON")
    o.add_argument("--png", metavar="FILE", help="write the coverage overlay image")
    o.add_argument("--quiet", action="store_true", help="suppress the text report")

    h = p.add_argument_group("home assistant")
    h.add_argument("--push", action="store_true", help="publish the result as HA sensors")
    h.add_argument("--prefix", default="spray", help="sensor name prefix (default: spray)")


def _config_from(args) -> SprayerConfig:
    return SprayerConfig(
        swath_width_m=args.swath,
        tank_capacity_l=args.tank,
        min_speed_kmh=args.min_speed,
        max_speed_kmh=args.max_speed,
        max_gap_s=args.max_gap,
        max_accuracy_m=args.max_accuracy,
        cell_size_m=args.cell,
        min_gap_area_m2=args.min_gap_area,
    )


def _base_from(args) -> BaseLocation | None:
    if not args.base:
        return None
    try:
        lat_s, lon_s = args.base.split(",")
        return BaseLocation(
            lat=float(lat_s),
            lon=float(lon_s),
            radius_m=args.base_radius,
            min_dwell_s=args.base_dwell,
        )
    except ValueError:
        raise SystemExit(f"--base must be LAT,LON; got {args.base!r}")


def _field_rings(path: str | None) -> list | None:
    if not path:
        return None
    obj = json.loads(Path(path).read_text())
    rings: list = []

    def collect(geom):
        if not geom:
            return
        gtype = geom.get("type")
        if gtype == "Polygon":
            rings.append(geom["coordinates"][0])
        elif gtype == "MultiPolygon":
            rings.extend(poly[0] for poly in geom["coordinates"])

    if obj.get("type") == "FeatureCollection":
        for feat in obj["features"]:
            collect(feat.get("geometry"))
    elif obj.get("type") == "Feature":
        collect(obj.get("geometry"))
    else:
        collect(obj)

    if not rings:
        raise SystemExit(f"no polygons found in {path}")
    return rings


def _emit(result, args) -> None:
    if not args.quiet:
        print(format_text_report(result))

    if args.json:
        Path(args.json).write_text(json.dumps(result.summary(), indent=2))
        print(f"\nwrote {args.json}", file=sys.stderr)
    if args.geojson:
        Path(args.geojson).write_text(json.dumps(to_geojson(result), indent=2))
        print(f"wrote {args.geojson}", file=sys.stderr)
    if args.png and result.overlay_png:
        Path(args.png).write_bytes(result.overlay_png)
        south, west, north, east = result.bounds
        print(f"wrote {args.png}  bounds S{south:.6f} W{west:.6f} N{north:.6f} E{east:.6f}", file=sys.stderr)

    if args.push:
        from .ha import HAClient

        with HAClient.from_env() as client:
            created = client.push_result(result, args.prefix)
        print(f"\npushed {len(created)} sensors to Home Assistant:", file=sys.stderr)
        for entity in created:
            print(f"  {entity}", file=sys.stderr)


def cmd_analyze(args) -> int:
    data = Path(args.file).read_bytes()
    track = parse_track(data, args.file)
    result = analyze(track, _config_from(args), _base_from(args), _field_rings(args.field), render=bool(args.png))
    _emit(result, args)
    return 0


def cmd_demo(args) -> int:
    track, base, cfg = synthetic_run(swath_width_m=args.swath)
    cfg = _config_from(args)
    cfg.swath_width_m = args.swath
    result = analyze(track, cfg, _base_from(args) or base, render=bool(args.png))
    _emit(result, args)
    return 0


def cmd_fetch(args) -> int:
    from .ha import HAClient, day_bounds

    with HAClient.from_env() as client:
        if args.list:
            for tracker in client.list_device_trackers():
                mark = "gps" if tracker["has_gps"] else "   "
                print(f"  [{mark}] {tracker['entity_id']:<40} {tracker['name']}")
            return 0

        if not args.entity:
            raise SystemExit("--entity is required (use --list to see the options)")

        if args.since:
            start = datetime.now(timezone.utc) - timedelta(hours=args.since)
            end = datetime.now(timezone.utc)
        else:
            start, end = day_bounds(args.day, args.tz_offset)

        track = client.fetch_track(args.entity, start, end)
        print(f"fetched {len(track)} fixes for {args.entity}", file=sys.stderr)

        if args.save:
            Path(args.save).write_text(
                json.dumps(
                    [
                        {"t": float(t), "lat": float(la), "lon": float(lo)}
                        for t, la, lo in zip(track.t, track.lat, track.lon)
                    ]
                )
            )
            print(f"saved raw fixes to {args.save}", file=sys.stderr)

        result = analyze(track, _config_from(args), _base_from(args), _field_rings(args.field), render=bool(args.png))
        _emit(result, args)
    return 0


def cmd_serve(args) -> int:
    import uvicorn

    uvicorn.run("spraycontrol.web.app:app", host=args.host, port=args.port, log_level="info")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="spraycontrol",
        description="Analyse sprayer GPS tracks: coverage, misses, overlap, refills and rate.",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    p_analyze = sub.add_parser("analyze", help="analyse a track file (GPX, CSV, GeoJSON, KML)")
    p_analyze.add_argument("file")
    _add_machine_args(p_analyze)
    p_analyze.set_defaults(func=cmd_analyze)

    p_demo = sub.add_parser("demo", help="analyse a synthetic run with known defects")
    _add_machine_args(p_demo)
    p_demo.set_defaults(func=cmd_demo)

    p_fetch = sub.add_parser("fetch", help="pull a track from Home Assistant and analyse it")
    p_fetch.add_argument("--entity", help="device_tracker entity id")
    p_fetch.add_argument("--list", action="store_true", help="list available device trackers and exit")
    p_fetch.add_argument("--day", metavar="YYYY-MM-DD", help="local day to analyse (default: today)")
    p_fetch.add_argument("--since", type=float, metavar="HOURS", help="analyse the last N hours instead of a day")
    p_fetch.add_argument("--tz-offset", type=float, default=0.0, metavar="H", help="local UTC offset for --day (default: 0)")
    p_fetch.add_argument("--save", metavar="FILE", help="also save the raw fixes as JSON")
    _add_machine_args(p_fetch)
    p_fetch.set_defaults(func=cmd_fetch)

    p_serve = sub.add_parser("serve", help="run the web interface")
    p_serve.add_argument("--host", default="0.0.0.0")
    p_serve.add_argument("--port", type=int, default=8099)
    p_serve.set_defaults(func=cmd_serve)

    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
