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
