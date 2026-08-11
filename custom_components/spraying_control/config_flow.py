"""Config and options flow."""

from __future__ import annotations

from typing import Any

import voluptuous as vol
from homeassistant.config_entries import ConfigEntry, ConfigFlow, ConfigFlowResult, OptionsFlow
from homeassistant.core import callback
from homeassistant.helpers import selector

from .const import (
    CONF_BASE,
    CONF_BASE_DWELL,
    CONF_SWATH_WIDTH,
    CONF_DAILY_TIME,
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


def _number(minimum: float, maximum: float, step: float, unit: str | None = None):
    return selector.NumberSelector(
        selector.NumberSelectorConfig(
            min=minimum,
            max=maximum,
            step=step,
            unit_of_measurement=unit,
            mode=selector.NumberSelectorMode.BOX,
        )
    )


def _schema(defaults: dict[str, Any], include_tracker: bool = True) -> vol.Schema:
    fields: dict[Any, Any] = {}

    if include_tracker:
        fields[vol.Required(CONF_TRACKER, default=defaults.get(CONF_TRACKER))] = (
            selector.EntitySelector(selector.EntitySelectorConfig(domain="device_tracker"))
        )

    fields.update(
        {
            vol.Required(
                CONF_SWATH_WIDTH, default=defaults.get(CONF_SWATH_WIDTH, DEFAULT_SWATH_WIDTH)
            ): _number(0.2, 5, 0.1, "m"),
            vol.Required(
                CONF_TANK_CAPACITY, default=defaults.get(CONF_TANK_CAPACITY, DEFAULT_TANK_CAPACITY)
            ): _number(1, 50, 0.5, "L"),
            vol.Optional(
                CONF_BASE,
                default=defaults.get(CONF_BASE, {"radius": DEFAULT_BASE_RADIUS}),
            ): selector.LocationSelector(selector.LocationSelectorConfig(radius=True)),
            vol.Required(
                CONF_BASE_DWELL, default=defaults.get(CONF_BASE_DWELL, DEFAULT_BASE_DWELL)
            ): _number(10, 1800, 5, "s"),
            vol.Required(
                CONF_MIN_SPEED, default=defaults.get(CONF_MIN_SPEED, DEFAULT_MIN_SPEED)
            ): _number(0, 10, 0.1, "km/h"),
            vol.Required(
                CONF_MAX_SPEED, default=defaults.get(CONF_MAX_SPEED, DEFAULT_MAX_SPEED)
            ): _number(1, 15, 0.5, "km/h"),
            vol.Required(
                CONF_MAX_GAP, default=defaults.get(CONF_MAX_GAP, DEFAULT_MAX_GAP)
            ): _number(5, 600, 5, "s"),
            vol.Required(
                CONF_MAX_ACCURACY, default=defaults.get(CONF_MAX_ACCURACY, DEFAULT_MAX_ACCURACY)
            ): _number(1, 100, 1, "m"),
            vol.Optional(CONF_DAILY_TIME, default=defaults.get(CONF_DAILY_TIME, "")): (
                selector.TextSelector(selector.TextSelectorConfig(type=selector.TextSelectorType.TIME))
            ),
        }
    )
    return vol.Schema(fields)


def _validate(user_input: dict[str, Any]) -> dict[str, str]:
    errors: dict[str, str] = {}
    if user_input[CONF_MIN_SPEED] >= user_input[CONF_MAX_SPEED]:
        errors[CONF_MIN_SPEED] = "speed_window"
    return errors


class SprayingControlConfigFlow(ConfigFlow, domain=DOMAIN):
    """Set up one sprayer."""

    VERSION = 1

    async def async_step_user(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        errors: dict[str, str] = {}

        if user_input is not None:
            errors = _validate(user_input)
            if not errors:
                await self.async_set_unique_id(user_input[CONF_TRACKER])
                self._abort_if_unique_id_configured()

                registry_name = self.hass.states.get(user_input[CONF_TRACKER])
                title = (
                    registry_name.attributes.get("friendly_name")
                    if registry_name
                    else user_input[CONF_TRACKER]
                )
                return self.async_create_entry(title=f"Spraying · {title}", data=user_input)

        return self.async_show_form(
            step_id="user",
            data_schema=_schema(user_input or {}),
            errors=errors,
        )

    @staticmethod
    @callback
    def async_get_options_flow(entry: ConfigEntry) -> OptionsFlow:
        return SprayingControlOptionsFlow()


class SprayingControlOptionsFlow(OptionsFlow):
    """Adjust the machine settings without re-adding the sprayer."""

    async def async_step_init(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        errors: dict[str, str] = {}

        if user_input is not None:
            errors = _validate(user_input)
            if not errors:
                return self.async_create_entry(data=user_input)

        current = {**self.config_entry.data, **self.config_entry.options}
        return self.async_show_form(
            step_id="init",
            data_schema=_schema(user_input or current, include_tracker=False),
            errors=errors,
        )
