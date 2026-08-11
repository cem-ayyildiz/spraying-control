"""The coverage map: the pass-count overlay as an image entity.

Home Assistant's map card can only plot points, so the sprayed/overlap/missed
pattern is served here as an image instead, for a picture card on the dashboard.
Green is one pass, yellow two, orange three, purple four or more, red a miss.
"""

from __future__ import annotations

from homeassistant.components.image import ImageEntity
from homeassistant.core import HomeAssistant
from homeassistant.helpers.device_registry import DeviceEntryType, DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity
from homeassistant.util import dt as dt_util

from . import SprayingConfigEntry
from .const import DOMAIN
from .coordinator import SprayingCoordinator


async def async_setup_entry(
    hass: HomeAssistant,
    entry: SprayingConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    async_add_entities([CoverageImage(hass, entry.runtime_data, entry)])


class CoverageImage(CoordinatorEntity[SprayingCoordinator], ImageEntity):
    """The coverage overlay of the most recent analysis."""

    _attr_has_entity_name = True
    _attr_translation_key = "coverage_map"
    _attr_content_type = "image/png"

    def __init__(
        self,
        hass: HomeAssistant,
        coordinator: SprayingCoordinator,
        entry: SprayingConfigEntry,
    ) -> None:
        CoordinatorEntity.__init__(self, coordinator)
        ImageEntity.__init__(self, hass)
        self._attr_unique_id = f"{entry.entry_id}_coverage_map"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, entry.entry_id)},
            name=entry.title,
            manufacturer="Spraying Control",
            model="GPS coverage analysis",
            entry_type=DeviceEntryType.SERVICE,
        )
        if coordinator.data is not None and coordinator.data.overlay_png is not None:
            self._attr_image_last_updated = dt_util.utcnow()

    @property
    def available(self) -> bool:
        data = self.coordinator.data
        return data is not None and data.overlay_png is not None

    async def async_image(self) -> bytes | None:
        data = self.coordinator.data
        return data.overlay_png if data is not None else None

    def _handle_coordinator_update(self) -> None:
        # Signal the frontend to re-fetch the picture on each new analysis.
        if self.coordinator.data is not None and self.coordinator.data.overlay_png is not None:
            self._attr_image_last_updated = dt_util.utcnow()
        super()._handle_coordinator_update()
