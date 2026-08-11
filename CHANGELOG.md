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
