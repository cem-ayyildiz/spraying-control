# Changelog

All notable changes to this project are recorded here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and the project uses
[Semantic Versioning](https://semver.org/spec/v2.0.0.html).

Versioning is done locally with `./scripts/release.sh <version>` (or
`make release VERSION=<version>`), which keeps the version in sync across
`pyproject.toml`, `config.yaml` and the integration `manifest.json`, stamps the
release below, creates the `v<version>` git tag, and publishes a matching
GitHub Release. HACS lists **GitHub Releases**, not bare tags, so the Release is
what makes an update appear in Home Assistant.

## [Unreleased]

## [0.10.0] - 2026-08-13

An easier way to put a photo on the map.

### Added
- **Pin two points.** Click a feature you can recognise in the photo, then click
  where that feature really is. Twice. Position, scale and rotation are solved
  from the pair - the standard way to georeference an image, and far quicker
  than nudging corners. The photo fades while you pick the ground point so you
  can see what is underneath.
- `placement_from_pins()` in the library, so the same solve is available outside
  the interface. Recovers a known placement to under a millimetre.

Dragging still works, and the two mix freely: pin the photo roughly into place,
then nudge a corner if a hedge does not quite line up.

## [0.9.1] - 2026-08-13

### Fixed
- **An aerial photo appeared to vanish once a session was added.** It was never
  gone - a photo placed before any track is centred on whatever you happen to be
  looking at, and analysing then jumps the map to where you actually walked,
  leaving the photo outside the view. Now:
  - the map takes in the photo as well as the coverage when the two overlap;
  - when they do not, a note says how far away it is, with **Move it onto the
    plot** (keeping the size and angle you set) and **Show me it**;
  - every photo has a locate button, so it can always be found;
  - the empty-state hint says to add a session first, since a photo added
    afterwards lands on it by itself.

## [0.9.0] - 2026-08-13

Lining a photo up no longer means wrestling three corners.

### Added
- **Move**: a white centre handle that carries the whole picture.
- **Rotate**: a slider, a live degree readout, and 1 degree and 90 degree nudge
  buttons. Turning it keeps the picture square to itself rather than shearing it.
- **Width in metres**: type the real ground width and the height follows the
  photo's own proportions.
- **Reset shape**, to undo a fiddle and start again from where it was.

The three coloured corners remain for pinning onto features exactly, and now
update the readouts as you drag them. The browser's placement maths uses the
same WGS84 radii as the analysis, so a width means the same thing on both sides
(they agree to under a millimetre).

## [0.8.2] - 2026-08-13

### Fixed
- **Adding the first aerial photo to a new garden failed.** With no sessions to
  centre on and no GPS in the picture, the upload was refused outright - which
  is exactly what happens when you start with the photo rather than a track. It
  now lands on the part of the map you are looking at, sized to the view, ready
  to drag into place. (A helper lost in the 0.8.0 rewrite also made that request
  500 rather than reporting anything useful.)

### Added
- **Aerial photos** is always visible, with an **Add an aerial photo…** button,
  instead of appearing only once a photo exists.
- The placement note now says which it used: *centred on your view* or *centred
  on your tracks*.

## [0.8.1] - 2026-08-13

Works with no internet, and the picture's own coordinates are addressable.

### Added
- **"None (offline)" basemap**, and an automatic fall back to it when tiles
  cannot be fetched. Everything that matters still works without a connection:
  your aerial photo is the backdrop, the coverage draws on top and every figure
  is computed locally.
- `Placement.pixel_to_latlon()` and `latlon_to_pixel()`: convert between a
  pixel of your aerial photo and a point on the ground, in both directions.
  Useful for asking "where is this bit of the picture?" or "did I spray the
  spot shown here?".

## [0.8.0] - 2026-08-11

Your own aerial photo as the backdrop, several sessions in one picture, and a
garden that remembers itself.

### Added
- **Projects.** A garden is now kept on disk: its settings, its refill point,
  its plot boundary, every session file and every photo. Set it up once and each
  new session is one drag away. Stored under `/data/projects` in the add-on and
  the standalone image, so it survives restarts and rebuilds.
- **Aerial photos.** Drop in a picture from a drone or a mast and it becomes the
  map backdrop, which is the only way to see garden detail - satellite tiles top
  out around 0.3 m per pixel and often have nothing at all at that zoom. The
  first guess at where it belongs comes from an ESRI world file if you have one,
  otherwise the camera's own GPS, otherwise the middle of your tracks. Then drag
  three corners to line it up: position, scale and rotation all follow.
- **Several sessions at once.** Tick any set of sessions and analyse them
  together. Ground covered again in a later session counts as overlap, and each
  session reports how much was new versus already done.
- **Drag and drop anywhere**, several files at a time, tracks and photos mixed.
- **A coverage opacity slider**, to fade the overlay back and read the ground
  underneath.
- `spraycontrol analyze` now takes several files: `analyze a.gpx b.gpx`.

### Changed
- `analyze()` accepts a list of tracks as well as a single one. One projection
  and one grid are shared across them, which is what makes cross-session overlap
  measurable.
- The web interface is rebuilt around the project, replacing the
  upload-and-forget form.


## [0.7.0] - 2026-08-11

Groundwork for HACS default-store submission.

### Added
- A brand icon (`custom_components/spraying_control/brand/icon.png`,
  `icon@2x.png`) via the local-brands feature added in Home Assistant 2026.3 -
  no separate PR to home-assistant/brands needed. Fixes the "icon not
  available" placeholder shown in Settings.
- `.github/workflows/validate.yml` is back. HACS default-store review requires
  a public, passing GitHub Actions run (hassfest + the HACS action) as proof;
  there is no submission path that skips it. `scripts/validate.sh` /
  `make validate` remain the everyday local workflow - this exists
  specifically for that requirement.

## [0.6.0] - 2026-08-11

Run the real map without a Supervisor.

### Added
- `docker-compose.standalone.yml` and `.env.standalone.example`: run the web
  interface as its own container on Home Assistant Container / Core installs,
  where there is no Supervisor and therefore no Add-ons menu. It talks to Home
  Assistant over the REST API via `HA_URL`/`HA_TOKEN` (the same fallback the
  add-on already used when no `SUPERVISOR_TOKEN` is present) - reads tracker
  history and pushes `sensor.spray_*` states. Verified end-to-end against a
  real remote Home Assistant instance with no Supervisor involved.

## [0.5.0] - 2026-08-11

See the coverage pattern in the dashboard.

### Added
- **Coverage map** image entity — the pass-count overlay (green once, yellow
  twice, orange three times, purple four+, red missed) rendered as an image, for
  a picture card. Home Assistant's map card can only plot points, so this is how
  the overlaps and misses show up on the dashboard. The shipped dashboard now
  includes it with a legend.

### Changed
- The integration now renders the coverage overlay on every analysis (it was
  computed but discarded before).

## [0.4.0] - 2026-08-11

Upload a track file straight into Home Assistant.

### Added
- **Configure → Analyse a track file** — a native file picker in the options
  flow. Choose a GPX, CSV, KML or GeoJSON from your device and the sensors
  update from it, no add-on needed.
- **`spraying_control.analyze_file` action** — analyses a track file already on
  the host (e.g. dropped into `/media`), for automations and dashboard buttons.
  Paths are restricted to Home Assistant's allowed directories.

## [0.3.0] - 2026-08-11

Record a spray session from the phone, with no keyboard.

### Added
- **Recording switch** — turn it on to start a session (and switch the phone to
  high-accuracy GPS), off to stop and analyse just that session.
- **Set start point button** — captures where the phone is right now as the
  refill/base point for the session.
- **Analyse today button** — analyse the whole day's track on demand.
- **Start point sensor** exposing the point's coordinates, so it plots on a map
  card next to the phone.
- A "Switch phone to high accuracy while recording" option in the config flow.
- [docs/dashboard.yaml](docs/dashboard.yaml): a ready-made Lovelace view with the
  controls, a map, gauges and the results.

## [0.2.0] - 2026-08-11

Reoriented the whole project from tractor/boom sprayers to a **garden owner with
a knapsack (backpack) sprayer**, walking a plot with a phone in their pocket.

### Changed
- Renamed "boom width" to **spray width** everywhere (`swath_width_m`), and made
  it a first-class control: preset buttons (0.5 / 1 / 1.5 / 2 m) plus a custom
  value in the web interface, and a bounded number field in the config flow.
- New defaults for a person on foot: 18 L tank, 1 m spray band, 0.4–4.5 km/h
  spraying-speed window, 8 m refill radius, 0.25 m grid cells.
- Terminology throughout is now garden-scale: plot, lane, refill point.
- The demo is a walk over a small plot with a skipped lane and an overlapping
  lane built in.

### Added
- A plain note when GPS accuracy is coarse relative to the spray band: the
  totals stay reliable, the fine gap/overlap map is a guide.
- **Use my location** button and a **draggable refill-point marker** in the web
  interface.
- Local development workflow: `scripts/validate.sh`, `scripts/release.sh` and a
  `Makefile`. Validation runs on your machine, not in the cloud.

### Fixed
- Map clicks no longer get swallowed by the drawn analysis layers, so the refill
  point can be re-picked after an analysis.

### Removed
- The GitHub Actions workflow. All checks run locally via `make validate`.

## [0.1.0] - 2026-08-11

Initial release: GPS spray-coverage analysis as a Home Assistant integration and
add-on, with a CLI and web interface.
