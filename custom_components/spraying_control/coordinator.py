"""Pulls tracker history out of the recorder and analyses it."""

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
    CONF_SWATH_WIDTH,
    CONF_MAX_ACCURACY,
    CONF_MAX_GAP,
    CONF_MAX_SPEED,
    CONF_MIN_SPEED,
    CONF_TANK_CAPACITY,
    CONF_TRACKER,
    DEFAULT_BASE_DWELL,
    DEFAULT_BASE_RADIUS,
    DEFAULT_SWATH_WIDTH,
    DEFAULT_MAX_ACCURACY,
    DEFAULT_MAX_GAP,
    DEFAULT_MAX_SPEED,
    DEFAULT_MIN_SPEED,
    DEFAULT_TANK_CAPACITY,
    DOMAIN,
)

_LOGGER = logging.getLogger(__name__)


class SprayingCoordinator(DataUpdateCoordinator):
    """Holds the most recent analysis for one configured sprayer.

    There is nothing to poll - a run is analysed when asked for, either by the
    ``spraying_control.analyze`` service or by the optional daily schedule.
    """

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry) -> None:
        super().__init__(hass, _LOGGER, name=f"{DOMAIN} {entry.title}", update_interval=None)
        self.entry = entry
        self.last_error: str | None = None

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
        base = None
        if base_conf.get("latitude") is not None and base_conf.get("longitude") is not None:
            base = BaseLocation(
                lat=float(base_conf["latitude"]),
                lon=float(base_conf["longitude"]),
                radius_m=float(base_conf.get("radius") or DEFAULT_BASE_RADIUS),
                min_dwell_s=float(opts.get(CONF_BASE_DWELL, DEFAULT_BASE_DWELL)),
            )
        return cfg, base

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

        # Reuse the file parser's cleaning: it sorts, drops null islands and
        # removes duplicate timestamps.
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
            # The raster work is CPU bound; keep it off the event loop.
            result = await self.hass.async_add_executor_job(
                partial(analyze, track, cfg, base, None, False)
            )
        except (TrackParseError, ValueError) as err:
            raise UpdateFailed(str(err)) from err

        self.last_error = None
        self.async_set_updated_data(result)
        _LOGGER.debug(
            "Analysed %s: %.2f ha sprayed, %d loads, %d gaps",
            self.tracker,
            result.coverage.sprayed_area_m2 / 10_000,
            len(result.loads),
            len(result.gaps),
        )

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
