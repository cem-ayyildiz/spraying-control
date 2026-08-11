# Spraying Control

[![hacs][hacs-badge]][hacs-url]
[![validate][validate-badge]][validate-url]
[![license][license-badge]](LICENSE)

**Find the strips you missed and the ones you sprayed twice.**

Feed it the GPS track from a spraying run and it tells you what you actually
covered, where the gaps are, how much product went out, and how many tank loads
it took. Works from a phone in the cab — no terminal, no section control, no
subscription.

![Coverage map showing a missed strip and an overlap](docs/example.png)

---

## What it tells you

| | |
|---|---|
| **Coverage** | Sprayed area in hectares, and what fraction of the field that is |
| **Misses** | Every unsprayed patch inside the field — area, widest point, map location, largest first |
| **Overlap** | Ground covered twice, three times, four or more |
| **Refills** | Every return to base, which splits the run into tank loads |
| **Product used** | Tank capacity × loads, with the final partial load scaled by area |
| **Rate per tank load** | Litres per hectare for **each load separately** |

That last row is the one to watch. You configure a tank size, never a rate, so
the rate is *derived* from the ground each load actually covered. When one load
comes out at 380 L/ha and the next at 540, something changed — a blocked nozzle,
a wrong gear, a section left on through the headland. A single whole-field
average hides exactly that.

## Before you start: your phone probably logs too slowly

This is the one thing that decides whether any of it works.

Between two fixes the machine is assumed to have driven straight. That holds
while fixes are closer together than the boom is wide, and becomes meaningless
once they are not — a turn taken between two fixes simply does not exist in the
data.

> **Log a position at least every `boom_width ÷ speed` seconds.**
> For a 12 m boom at 8 km/h, that is about every 5 seconds.

The Home Assistant Companion app does **not** do this by default. Left alone it
reports every few minutes. Measured on two ordinary phones over 48 hours:

```
tracker A    395 fixes / 48 h    median interval 237 s
tracker B     26 fixes / 48 h    median interval 727 s
```

At 8 km/h a 237-second interval is **527 m between fixes — 44 boom widths**.
Coverage maps built on that are fiction. Spraying Control measures your fix
spacing and refuses to pretend otherwise:

> Fixes are 300 m apart on average (135 s at 8.0 km/h), which is wider than the
> 12 m boom. Coverage between fixes is interpolated, so misses and overlap are
> unreliable. Log a position at least every 5 s — in the Companion app enable
> high accuracy mode for the run.

**To fix it:** Companion app → **Settings → Companion app → Manage sensors →
Location → High accuracy mode**, interval 5–10 seconds. Bind it to something so
it is not draining the battery all day:

- **High accuracy mode only when in zone** — set a zone over the field, or
- **High accuracy mode trigger entity** — an `input_boolean.spraying` you flip
  on from the cab.

Any handheld GPS or tractor terminal that exports GPX at 1 Hz will do better.
GPX, CSV, KML (`gx:Track`) and GeoJSON are all accepted.

## Installing

Two independent pieces. Install either, or both.

| | **Integration** (HACS) | **Add-on** |
|---|---|---|
| Gives you | Entities, statistics, a service to call | The map interface |
| Setup | UI config flow | Add-on configuration tab |
| Survives a restart | Yes | Sensors do not |

HACS does not distribute add-ons — it carries integrations, dashboard plugins,
themes, templates, AppDaemon apps and python_scripts. That is why the map lives
in an add-on and the entities live in an integration.

> **Architecture:** amd64 and aarch64 only. numpy, scipy and shapely publish no
> armv7 wheels, so a 32-bit Pi would have to compile them on the device.

### Integration, via HACS

[![Open your Home Assistant instance and open a repository inside HACS.][hacs-repo-badge]][hacs-repo-url]

1. HACS → ⋮ → **Custom repositories** → this repository, category **Integration**
2. Download, then restart Home Assistant
3. **Settings → Devices & services → Add integration → Spraying Control**
4. Pick the device tracker that rides on the sprayer, set boom width and tank
   size, and drag the base marker onto the yard

It reads history straight from the recorder — no token, no network round trip —
and creates eleven entities on one device:

| Entity | Unit | Attributes |
|---|---|---|
| `sensor.<name>_area_sprayed` | ha | |
| `sensor.<name>_volume_used` | L | per-tank-load breakdown |
| `sensor.<name>_application_rate` | L/ha | per-tank-load breakdown |
| `sensor.<name>_refills` | count | |
| `sensor.<name>_tank_loads` | count | |
| `sensor.<name>_coverage` | % | |
| `sensor.<name>_missed_area` | ha | ten largest patches, with coordinates |
| `sensor.<name>_overlap` | % | |
| `sensor.<name>_distance` | km | |
| `sensor.<name>_working_time` | h | |
| `sensor.<name>_last_run` | timestamp | full summary and warnings |

Analysis runs when you ask for it:

```yaml
action: spraying_control.analyze
data:
  date: "2026-08-11"      # optional, defaults to today
```

Or set **Analyse automatically at** in the options and it runs every evening for
the day just finished. A worked example — tell me when a run went badly:

```yaml
automation:
  - alias: Warn about a bad spray job
    triggers:
      - trigger: state
        entity_id: sensor.sprayer_last_run
    conditions:
      - condition: numeric_state
        entity_id: sensor.sprayer_missed_area
        above: 0.1
    actions:
      - action: notify.mobile_app
        data:
          title: Missed ground
          message: >-
            {{ states('sensor.sprayer_missed_area') }} ha unsprayed across
            {{ state_attr('sensor.sprayer_missed_area', 'patch_count') }} patches.
```

### Add-on, for the map

1. Copy this folder to `/addons/spraying_control/` on your HA host — via the
   Samba or File editor add-on, or `git clone` straight into that path
2. **Settings → Add-ons → Add-on store → ⋮ → Check for updates**
3. *Spraying Control* appears under **Local add-ons**. Install it
4. Set boom width, tank capacity and base location in **Configuration**; they
   become the defaults in the interface
5. Start it and open it from the sidebar

It reaches Home Assistant through the Supervisor, so there is no token to create
and no port to expose. Its **Push to Home Assistant** button writes the same
figures as `sensor.spray_*`, but directly into the state machine, so **those do
not survive a restart**. For durable entities use the integration; the push is
there for a quick look and for setups without HACS.

## How it decides what was sprayed

There is no boom on/off signal in a GPS track. Everything is built on what can
be measured honestly:

| Question | Rule |
|---|---|
| Was it spraying? | Moving between `min_speed` and `max_speed`, outside the base radius |
| Where did the spray land? | A swath `boom_width` wide along the track, square-capped at the ends |
| Was a cell sprayed twice? | Covered again after a gap long enough to mean a genuine return |
| Where is the field? | Inferred by closing the spacing between swaths — or from a boundary you draw |
| Was that a refill? | Parked inside the base radius for `min_stop`, **and spraying resumed afterwards** |

Coverage is a raster of pass counts rather than polygon booleans, which makes
overlap exact: the number of passes over a cell falls straight out of the
accumulator, and gaps are the cells inside the field that nobody reached.

### What it will get wrong

Stated plainly, because a coverage map that hides its assumptions is worse than
no map:

- **Headland turns count as sprayed.** Turning at 6 km/h looks identical to
  spraying at 6 km/h. The overlap it reports there is real ground that got
  double-dosed, but if you shut the boom off in the turn it will overstate.
- **An edge you never drove to is invisible.** Without a field boundary, an
  unworked headland cannot be told apart from land that was never in the field.
  Draw a boundary and those edges get caught.
- **Sparse fixes are guesswork.** See the fix-rate section above. The app warns
  rather than quietly drawing a confident-looking map.
- **The last load is a guess.** Parking up at the end of the day is not treated
  as a refill, so the final load is scaled by area against the completed ones
  instead of being charged a full tank.

Anything it cannot stand behind comes back as a warning, not a confident number.

## Field boundaries

Without a boundary the field is inferred from the sprayed area itself, by
closing the spacing between adjacent swaths and filling holes. That catches
anything skipped *inside* the worked block, which is most of what goes wrong.

To catch the edges too, draw it on the map (**Draw field boundary** → click the
corners → **Finish**) or pass a GeoJSON polygon with `--field`.

## Without Home Assistant

```bash
uv sync                              # or: pip install -e .
uv run spraycontrol serve            # web interface on :8099
```

```bash
# Analyse a file
uv run spraycontrol analyze run.gpx \
    --boom 12 --tank 1000 \
    --base 38.3005,32.8985 --base-radius 40 \
    --png coverage.png --geojson misses.geojson

# A synthetic run with a skipped pass and an overlap built in
uv run spraycontrol demo

# Pull a day out of Home Assistant and publish the result back
export HA_URL=https://homeassistant.local:8123
export HA_TOKEN=<long-lived access token>
uv run spraycontrol fetch --list
uv run spraycontrol fetch --entity device_tracker.sprayer --day 2026-08-11 --push
```

`spraycontrol analyze --help` lists every option: speed window, maximum fix gap,
accuracy filter, grid cell size, minimum patch size worth reporting.

As a library:

```python
from spraycontrol import analyze, parse_track, SprayerConfig, BaseLocation

track = parse_track(open("run.gpx", "rb").read(), "run.gpx")
result = analyze(
    track,
    SprayerConfig(boom_width_m=12, tank_capacity_l=1000),
    BaseLocation(lat=38.3005, lon=32.8985, radius_m=40),
)

print(result.summary())
for load in result.loads:
    print(load.index + 1, load.area_ha, load.volume_l, load.rate_l_per_ha)
```

## Development

```bash
uv sync
uv run pytest                          # analysis library
```

The library suite builds synthetic runs containing defects of *known size* and
asserts the reported figures match. A skipped pass of 12 m × 300 m must come
back as 3,600 m² ± a few percent; a pass shifted 5 m into its neighbour must
produce ~1,500 m² of overlap **and** a matching gap on its far side.
`tests/test_coverage.py` is the place to start reading.

The Home Assistant tests need the pinned HA test harness, which requires a newer
Python than the library targets, so they get their own environment:

```bash
uv venv --python 3.13 .venv-ha
uv pip install --python .venv-ha --prerelease=allow \
    pytest-homeassistant-custom-component numpy scipy shapely
.venv-ha/bin/python -m pytest -c pytest-ha.ini
```

### Layout

The analysis library lives *inside* the integration, so HACS ships a
self-contained folder and there is only ever one copy of the code. The add-on
image and the `spraycontrol` wheel both build from that same directory.

```
custom_components/spraying_control/
  __init__.py       setup, analyze service, daily schedule
  config_flow.py    UI setup and options
  coordinator.py    recorder history -> analysis
  sensor.py         the eleven entities
  manifest.json     numpy / scipy / shapely requirements
  spraycontrol/     the analysis library
    geo.py          local tangent-plane projection, no PROJ dependency
    parsers.py      GPX, CSV, KML, GeoJSON, Home Assistant history
    segment.py      speed classification, spraying runs, base visits, tank loads
    coverage.py     swath rasterising, field inference, gap labelling
    analyze.py      orchestration, overlap counting, volume per load
    report.py       PNG overlay, GeoJSON, text report
    ha.py           Home Assistant REST client (add-on and CLI only)
    cli.py          command line
    web/            FastAPI app and the map interface
config.yaml         add-on manifest
Dockerfile          add-on image
hacs.json           HACS metadata
```

## Prior art

[AgOpenGPS](https://github.com/AgOpenGPS-Official/AgOpenGPS) is the reference
open-source project for precision agriculture, and if you are fitting real
guidance and section control to a machine, use it. It runs on a terminal in the
cab and prevents overlap *as you drive*.

Spraying Control solves a different, smaller problem: you already sprayed, you
had nothing but a phone, and you want to know how it went.

## Contributing

Issues and pull requests are welcome. `.github/workflows/validate.yml` runs
`hassfest`, the HACS action, both test suites and the add-on image build on
every push, so please make sure those pass.

## License

MIT — see [LICENSE](LICENSE).

[hacs-badge]: https://img.shields.io/badge/HACS-Custom-41BDF5.svg
[hacs-url]: https://hacs.xyz
[validate-badge]: https://github.com/cem-ayyildiz/spraying-control/actions/workflows/validate.yml/badge.svg
[validate-url]: https://github.com/cem-ayyildiz/spraying-control/actions/workflows/validate.yml
[license-badge]: https://img.shields.io/badge/license-MIT-blue.svg
[hacs-repo-badge]: https://my.home-assistant.io/badges/hacs_repository.svg
[hacs-repo-url]: https://my.home-assistant.io/redirect/hacs_repository/?owner=cem-ayyildiz&repository=spraying-control&category=integration
