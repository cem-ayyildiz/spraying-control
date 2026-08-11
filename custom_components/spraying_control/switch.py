"""The recording switch: on = start a spray session, off = stop and analyse."""

from __future__ import annotations

from homeassistant.components.switch import SwitchEntity
from homeassistant.core import HomeAssistant
from homeassistant.helpers.device_registry import DeviceEntryType, DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from . import SprayingConfigEntry
from .const import DOMAIN
from .coordinator import SprayingCoordinator


async def async_setup_entry(
    hass: HomeAssistant,
    entry: SprayingConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    async_add_entities([RecordingSwitch(entry.runtime_data, entry)])


class RecordingSwitch(CoordinatorEntity[SprayingCoordinator], SwitchEntity):
    """Start and stop recording a spray session.

    Turning it on marks the start time and, if enabled, switches the phone to
    high-accuracy GPS. Turning it off analyses the session and fills the sensors.
    """

    _attr_has_entity_name = True
    _attr_translation_key = "recording"
    _attr_icon = "mdi:record-circle"

    def __init__(self, coordinator: SprayingCoordinator, entry: SprayingConfigEntry) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = f"{entry.entry_id}_recording"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, entry.entry_id)},
            name=entry.title,
            manufacturer="Spraying Control",
            model="GPS coverage analysis",
            entry_type=DeviceEntryType.SERVICE,
        )

    @property
    def is_on(self) -> bool:
        return self.coordinator.recording

    async def async_turn_on(self, **kwargs) -> None:
        await self.coordinator.async_start_recording()

    async def async_turn_off(self, **kwargs) -> None:
        await self.coordinator.async_stop_recording()
