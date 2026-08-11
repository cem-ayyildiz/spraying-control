"""Pulls tracker history out of the recorder and analyses it.

Also holds the live recording session: when the user taps Start, the machine
notes the time (and, if configured, switches the phone to high-accuracy GPS);
when they tap Stop, the session's window is analysed and the sensors fill in.
"""

from __future__ import annotations

import logging
from datetime import date, datetime, timedelta
from functools import partial

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed
from homeassistant.util import dt as dt_util

from .const import (
    CONF_BASE,
    CONF_BASE_DWELL,
    CONF_HIGH_ACCURACY,
    CONF_MAX_ACCURACY,
    CONF_MAX_GAP,
    CONF_MAX_SPEED,
    CONF_MIN_SPEED,
    CONF_SWATH_WIDTH,
    CONF_TANK_CAPACITY,
    CONF_TRACKER,
    DEFAULT_BASE_DWELL,
    DEFAULT_BASE_RADIUS,
    DEFAULT_MAX_ACCURACY,
    DEFAULT_MAX_GAP,
    DEFAULT_MAX_SPEED,
    DEFAULT_MIN_SPEED,
    DEFAULT_SWATH_WIDTH,
    DEFAULT_TANK_CAPACITY,
    DOMAIN,
)

_LOGGER = logging.getLogger(__name__)


class SprayingCoordinator(DataUpdateCoordinator):
    """Holds the most recent analysis, and the live recording session, for one
    configured sprayer.

    There is nothing to poll - a run is analysed when asked for: by the Stop
    button, the ``spraying_control.analyze`` service, or the daily schedule.
    """

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry) -> None:
        super().__init__(hass, _LOGGER, name=f"{DOMAIN} {entry.title}", update_interval=None)
        self.entry = entry
        self.last_error: str | None = None

        # Live session state.
        self.recording: bool = False
        self.session_start: datetime | None = None
        # A start point captured on the ground, overriding the configured base.
        self.session_point: tuple[float, float] | None = None

    @property
    def options(self) -> dict:
        return {**self.entry.data, **self.entry.options}

    @property
    def tracker(self) -> str:
        return self.options[CONF_TRACKER]

    def _config(self):
        from .spraycontrol.models import BaseLocation, SprayerConfig

        opts = self.options
        cfg = SprayerConfig(
            swath_width_m=float(opts.get(CONF_SWATH_WIDTH, DEFAULT_SWATH_WIDTH)),
            tank_capacity_l=float(opts.get(CONF_TANK_CAPACITY, DEFAULT_TANK_CAPACITY)),
            min_speed_kmh=float(opts.get(CONF_MIN_SPEED, DEFAULT_MIN_SPEED)),
            max_speed_kmh=float(opts.get(CONF_MAX_SPEED, DEFAULT_MAX_SPEED)),
            max_gap_s=float(opts.get(CONF_MAX_GAP, DEFAULT_MAX_GAP)),
            max_accuracy_m=float(opts.get(CONF_MAX_ACCURACY, DEFAULT_MAX_ACCURACY)),
        )

        base_conf = opts.get(CONF_BASE) or {}
        lat = base_conf.get("latitude")
        lon = base_conf.get("longitude")
        radius = base_conf.get("radius") or DEFAULT_BASE_RADIUS
        # A point captured on the ground wins over the configured one.
        if self.session_point is not None:
            lat, lon = self.session_point

        base = None
        if lat is not None and lon is not None and not (abs(lat) < 1e-9 and abs(lon) < 1e-9):
            base = BaseLocation(
                lat=float(lat),
                lon=float(lon),
                radius_m=float(radius),
                min_dwell_s=float(opts.get(CONF_BASE_DWELL, DEFAULT_BASE_DWELL)),
            )
        return cfg, base

    # --- live session ---------------------------------------------------------

    @property
    def start_point(self) -> tuple[float, float] | None:
        """The refill/start point in force, captured or configured."""
        if self.session_point is not None:
            return self.session_point
        base_conf = self.options.get(CONF_BASE) or {}
        lat, lon = base_conf.get("latitude"), base_conf.get("longitude")
        if lat is not None and lon is not None and not (abs(lat) < 1e-9 and abs(lon) < 1e-9):
            return float(lat), float(lon)
        return None

    def capture_start_point(self) -> tuple[float, float]:
        """Record where the tracker is right now as the start/refill point."""
        state = self.hass.states.get(self.tracker)
        if state is None:
            raise UpdateFailed(f"{self.tracker} is not available")
        lat = state.attributes.get("latitude")
        lon = state.attributes.get("longitude")
        if lat is None or lon is None:
            raise UpdateFailed(f"{self.tracker} is not reporting a position yet")
        self.session_point = (float(lat), float(lon))
        _LOGGER.debug("Captured start point %.6f, %.6f", lat, lon)
        self.async_update_listeners()
        return self.session_point

    async def async_start_recording(self) -> None:
        self.recording = True
        self.session_start = dt_util.utcnow()
        await self._set_high_accuracy(True)
        self.async_update_listeners()
        _LOGGER.info("Started recording for %s at %s", self.tracker, self.session_start)

    async def async_stop_recording(self) -> None:
        was_recording = self.recording
        start = self.session_start
        self.recording = False
        await self._set_high_accuracy(False)
        self.async_update_listeners()
        if not was_recording or start is None:
            return
        end = dt_util.utcnow()
        # A couple of minutes of margin catches fixes logged either side.
        try:
            await self.async_analyze_period(start - timedelta(minutes=1), end + timedelta(minutes=1))
        except UpdateFailed as err:
            self.last_error = str(err)
            self.async_update_listeners()
            _LOGGER.warning("Recording for %s produced nothing: %s", self.tracker, err)

    async def _set_high_accuracy(self, on: bool) -> None:
        """Best-effort switch of the phone's high-accuracy GPS while recording.

        Uses the Companion app's notify command, derived from the tracker name.
        Any failure is logged and ignored - the user may drive location a
        different way, and recording must never hinge on it.
        """
        if not self.options.get(CONF_HIGH_ACCURACY, True):
            return
        # device_tracker.sm_a356e -> notify.mobile_app_sm_a356e
        service = f"mobile_app_{self.tracker.split('.', 1)[-1]}"
        if not self.hass.services.has_service("notify", service):
            _LOGGER.debug("No notify.%s; skipping high-accuracy command", service)
            return
        try:
            await self.hass.services.async_call(
                "notify",
                service,
                {
                    "message": "command_high_accuracy_mode",
                    "data": {"command": "turn_on" if on else "turn_off"},
                },
                blocking=False,
            )
        except Exception as err:  # noqa: BLE001 - best effort, never fatal
            _LOGGER.debug("Could not set high-accuracy mode: %s", err)

    # --- analysis -------------------------------------------------------------

    async def async_analyze_day(self, day: date | None = None) -> None:
        """Analyse one local day of tracker history."""
        target = day or dt_util.now().date()
        start = dt_util.start_of_local_day(target)
        await self.async_analyze_period(start, start + timedelta(days=1))

    async def async_analyze_period(self, start: datetime, end: datetime) -> None:
        from .spraycontrol.analyze import analyze
        from .spraycontrol.parsers import TrackParseError, parse_ha_history

        states = await self._async_history(start, end)
        if not states:
            raise UpdateFailed(
                f"{self.tracker} recorded no positions between "
                f"{start:%Y-%m-%d %H:%M} and {end:%Y-%m-%d %H:%M}"
            )

        payload = [
            {
                "entity_id": self.tracker,
                "last_updated": st.last_updated.timestamp(),
                "attributes": dict(st.attributes),
            }
            for st in states
        ]

        cfg, base = self._config()
        try:
            track = await self.hass.async_add_executor_job(parse_ha_history, payload, self.tracker)
            track.name = f"{self.tracker} {start:%Y-%m-%d}"
            result = await self.hass.async_add_executor_job(
                partial(analyze, track, cfg, base, None, True)
            )
        except (TrackParseError, ValueError) as err:
            raise UpdateFailed(str(err)) from err

        self.last_error = None
        self.async_set_updated_data(result)
        _LOGGER.debug(
            "Analysed %s: %.3f ha sprayed, %d loads, %d gaps",
            self.tracker,
            result.coverage.sprayed_area_m2 / 10_000,
            len(result.loads),
            len(result.gaps),
        )

    async def async_analyze_bytes(self, data: bytes, filename: str) -> None:
        """Analyse an uploaded track file (GPX, CSV, KML, GeoJSON, ...)."""
        from .spraycontrol.analyze import analyze
        from .spraycontrol.parsers import TrackParseError, parse_track

        cfg, base = self._config()
        try:
            track = await self.hass.async_add_executor_job(parse_track, data, filename)
            result = await self.hass.async_add_executor_job(
                partial(analyze, track, cfg, base, None, True)
            )
        except (TrackParseError, ValueError) as err:
            raise UpdateFailed(str(err)) from err
        self.last_error = None
        self.async_set_updated_data(result)
        _LOGGER.debug("Analysed uploaded %s: %.3f ha sprayed", filename, result.coverage.sprayed_area_m2 / 10_000)

    async def async_analyze_path(self, path: str) -> None:
        """Analyse a track file already on the Home Assistant host."""
        from pathlib import Path

        if not self.hass.config.is_allowed_path(path):
            raise UpdateFailed(
                f"{path} is not in an allowed directory. Add its folder to "
                "homeassistant.allowlist_external_dirs, or drop the file under /media."
            )

        def _read() -> tuple[bytes, str]:
            p = Path(path)
            return p.read_bytes(), p.name

        try:
            data, name = await self.hass.async_add_executor_job(_read)
        except OSError as err:
            raise UpdateFailed(f"could not read {path}: {err}") from err
        await self.async_analyze_bytes(data, name)

    async def _async_history(self, start: datetime, end: datetime) -> list:
        """Read tracker states from the recorder, attributes included."""
        from homeassistant.components.recorder import get_instance
        from homeassistant.components.recorder.history import state_changes_during_period

        def _fetch():
            return state_changes_during_period(
                self.hass,
                start,
                end,
                self.tracker,
                no_attributes=False,
                include_start_time_state=True,
            )

        changes = await get_instance(self.hass).async_add_executor_job(_fetch)
        return list(changes.get(self.tracker, []))
