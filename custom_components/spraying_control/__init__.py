"""Spraying Control: coverage, misses, overlap and product use from a GPS track."""

from __future__ import annotations

import logging
from datetime import datetime

import voluptuous as vol
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant, ServiceCall, callback
from homeassistant.exceptions import ServiceValidationError
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers.event import async_track_time_change
from homeassistant.helpers.update_coordinator import UpdateFailed
from homeassistant.util import dt as dt_util

from .const import ATTR_CONFIG_ENTRY_ID, ATTR_DATE, CONF_DAILY_TIME, DOMAIN, SERVICE_ANALYZE
from .coordinator import SprayingCoordinator

_LOGGER = logging.getLogger(__name__)

PLATFORMS: list[Platform] = [Platform.SENSOR, Platform.SWITCH, Platform.BUTTON]

SprayingConfigEntry = ConfigEntry[SprayingCoordinator]

SERVICE_SCHEMA = vol.Schema(
    {
        vol.Optional(ATTR_DATE): cv.date,
        vol.Optional(ATTR_CONFIG_ENTRY_ID): cv.string,
    }
)


async def async_setup_entry(hass: HomeAssistant, entry: SprayingConfigEntry) -> bool:
    coordinator = SprayingCoordinator(hass, entry)
    entry.runtime_data = coordinator

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    entry.async_on_unload(entry.add_update_listener(_async_reload))

    _schedule_daily(hass, entry, coordinator)
    _register_service(hass)
    return True


async def async_unload_entry(hass: HomeAssistant, entry: SprayingConfigEntry) -> bool:
    unloaded = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unloaded and not hass.config_entries.async_loaded_entries(DOMAIN):
        hass.services.async_remove(DOMAIN, SERVICE_ANALYZE)
    return unloaded


async def _async_reload(hass: HomeAssistant, entry: SprayingConfigEntry) -> None:
    await hass.config_entries.async_reload(entry.entry_id)


@callback
def _schedule_daily(
    hass: HomeAssistant, entry: SprayingConfigEntry, coordinator: SprayingCoordinator
) -> None:
    """Optionally analyse yesterday's work every day at a set time."""
    raw = (coordinator.options.get(CONF_DAILY_TIME) or "").strip()
    if not raw:
        return

    parsed = dt_util.parse_time(raw)
    if parsed is None:
        _LOGGER.warning("Ignoring unreadable daily analysis time %r", raw)
        return

    async def _run(_now: datetime) -> None:
        try:
            await coordinator.async_analyze_day()
        except UpdateFailed as err:
            # A day with no spraying is normal; do not shout about it.
            _LOGGER.debug("Scheduled analysis for %s found nothing: %s", coordinator.tracker, err)

    entry.async_on_unload(
        async_track_time_change(hass, _run, hour=parsed.hour, minute=parsed.minute, second=0)
    )


@callback
def _register_service(hass: HomeAssistant) -> None:
    if hass.services.has_service(DOMAIN, SERVICE_ANALYZE):
        return

    async def _handle(call: ServiceCall) -> None:
        entry_id = call.data.get(ATTR_CONFIG_ENTRY_ID)
        entries = hass.config_entries.async_loaded_entries(DOMAIN)
        if entry_id:
            entries = [e for e in entries if e.entry_id == entry_id]
            if not entries:
                raise ServiceValidationError(f"No loaded Spraying Control entry {entry_id}")

        day = call.data.get(ATTR_DATE)
        failures: list[str] = []
        for entry in entries:
            try:
                await entry.runtime_data.async_analyze_day(day)
            except UpdateFailed as err:
                failures.append(f"{entry.title}: {err}")

        if failures and len(failures) == len(entries):
            raise ServiceValidationError("; ".join(failures))

    hass.services.async_register(DOMAIN, SERVICE_ANALYZE, _handle, schema=SERVICE_SCHEMA)
