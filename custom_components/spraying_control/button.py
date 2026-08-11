"""Buttons: capture the start point, and analyse on demand."""

from __future__ import annotations

from homeassistant.components.button import ButtonEntity
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.device_registry import DeviceEntryType, DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity, UpdateFailed

from . import SprayingConfigEntry
from .const import DOMAIN
from .coordinator import SprayingCoordinator


async def async_setup_entry(
    hass: HomeAssistant,
    entry: SprayingConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    coordinator = entry.runtime_data
    async_add_entities(
        [SetStartPointButton(coordinator, entry), AnalyzeTodayButton(coordinator, entry)]
    )


class _BaseButton(CoordinatorEntity[SprayingCoordinator], ButtonEntity):
    _attr_has_entity_name = True

    def __init__(self, coordinator: SprayingCoordinator, entry: SprayingConfigEntry, key: str) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = f"{entry.entry_id}_{key}"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, entry.entry_id)},
            name=entry.title,
            manufacturer="Spraying Control",
            model="GPS coverage analysis",
            entry_type=DeviceEntryType.SERVICE,
        )


class SetStartPointButton(_BaseButton):
    """Capture where the phone is right now as the refill / start point."""

    _attr_translation_key = "set_start_point"
    _attr_icon = "mdi:map-marker-plus"

    def __init__(self, coordinator: SprayingCoordinator, entry: SprayingConfigEntry) -> None:
        super().__init__(coordinator, entry, "set_start_point")

    async def async_press(self) -> None:
        try:
            self.coordinator.capture_start_point()
        except UpdateFailed as err:
            raise HomeAssistantError(str(err)) from err


class AnalyzeTodayButton(_BaseButton):
    """Analyse today's track without waiting for the schedule."""

    _attr_translation_key = "analyze_today"
    _attr_icon = "mdi:map-search"

    def __init__(self, coordinator: SprayingCoordinator, entry: SprayingConfigEntry) -> None:
        super().__init__(coordinator, entry, "analyze_today")

    async def async_press(self) -> None:
        try:
            await self.coordinator.async_analyze_day()
        except UpdateFailed as err:
            raise HomeAssistantError(str(err)) from err
