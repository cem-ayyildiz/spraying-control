"""Sensors describing the most recent spraying run."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timezone

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorEntityDescription,
    SensorStateClass,
)
from homeassistant.const import (
    PERCENTAGE,
    UnitOfLength,
    UnitOfTime,
    UnitOfVolume,
)
from homeassistant.core import HomeAssistant
from homeassistant.helpers.device_registry import DeviceEntryType, DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.restore_state import RestoreEntity
from homeassistant.helpers.update_coordinator import CoordinatorEntity
from homeassistant.util import dt as dt_util

from . import SprayingConfigEntry
from .const import DOMAIN
from .coordinator import SprayingCoordinator

M2_PER_HA = 10_000.0
AREA_HECTARES = "ha"
RATE_L_PER_HA = "L/ha"


@dataclass(frozen=True, kw_only=True)
class SprayingSensorDescription(SensorEntityDescription):
    """Describes one derived value of a run."""

    value_fn: Callable[[object], float | int | datetime | None]
    attrs_fn: Callable[[object], dict] | None = None


def _loads(result) -> dict:
    return {
        "loads": [
            {
                "index": load.index + 1,
                "area_ha": round(load.area_ha, 3),
                "volume_l": round(load.volume_l, 1),
                "rate_l_per_ha": round(load.rate_l_per_ha, 1),
                "complete": load.is_complete,
                "start": datetime.fromtimestamp(load.start_t, timezone.utc).isoformat(),
                "end": datetime.fromtimestamp(load.end_t, timezone.utc).isoformat(),
            }
            for load in result.loads
        ]
    }


def _gaps(result) -> dict:
    return {
        "patch_count": len(result.gaps),
        "patches": [
            {
                "area_m2": round(gap.area_m2),
                "max_width_m": round(gap.max_width_m, 1),
                "latitude": gap.lat,
                "longitude": gap.lon,
            }
            for gap in result.gaps[:10]
        ],
    }


SENSORS: tuple[SprayingSensorDescription, ...] = (
    SprayingSensorDescription(
        key="area_sprayed",
        translation_key="area_sprayed",
        native_unit_of_measurement=AREA_HECTARES,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=2,
        value_fn=lambda r: r.coverage.sprayed_area_m2 / M2_PER_HA,
    ),
    SprayingSensorDescription(
        key="volume_used",
        translation_key="volume_used",
        device_class=SensorDeviceClass.VOLUME_STORAGE,
        native_unit_of_measurement=UnitOfVolume.LITERS,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=0,
        value_fn=lambda r: r.total_volume_l,
        attrs_fn=_loads,
    ),
    SprayingSensorDescription(
        key="application_rate",
        translation_key="application_rate",
        native_unit_of_measurement=RATE_L_PER_HA,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=1,
        value_fn=lambda r: r.overall_rate_l_per_ha,
        attrs_fn=_loads,
    ),
    SprayingSensorDescription(
        key="refills",
        translation_key="refills",
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=lambda r: r.refill_count,
    ),
    SprayingSensorDescription(
        key="tank_loads",
        translation_key="tank_loads",
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=lambda r: len(r.loads),
        attrs_fn=_loads,
    ),
    SprayingSensorDescription(
        key="coverage",
        translation_key="coverage",
        native_unit_of_measurement=PERCENTAGE,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=1,
        value_fn=lambda r: r.coverage.coverage_pct,
    ),
    SprayingSensorDescription(
        key="missed_area",
        translation_key="missed_area",
        native_unit_of_measurement=AREA_HECTARES,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=3,
        value_fn=lambda r: r.coverage.gap_area_m2 / M2_PER_HA,
        attrs_fn=_gaps,
    ),
    SprayingSensorDescription(
        key="overlap",
        translation_key="overlap",
        native_unit_of_measurement=PERCENTAGE,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=1,
        value_fn=lambda r: r.coverage.overlap_pct,
    ),
    SprayingSensorDescription(
        key="distance",
        translation_key="distance",
        device_class=SensorDeviceClass.DISTANCE,
        native_unit_of_measurement=UnitOfLength.KILOMETERS,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=2,
        value_fn=lambda r: r.total_distance_m / 1000.0,
    ),
    SprayingSensorDescription(
        key="working_time",
        translation_key="working_time",
        device_class=SensorDeviceClass.DURATION,
        native_unit_of_measurement=UnitOfTime.HOURS,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=2,
        value_fn=lambda r: r.spraying_time_s / 3600.0,
    ),
    SprayingSensorDescription(
        key="last_run",
        translation_key="last_run",
        device_class=SensorDeviceClass.TIMESTAMP,
        value_fn=lambda r: datetime.fromtimestamp(r.end_t, timezone.utc),
        attrs_fn=lambda r: {"summary": r.summary(), "warnings": r.warnings},
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: SprayingConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    coordinator = entry.runtime_data
    entities: list = [SprayingSensor(coordinator, entry, desc) for desc in SENSORS]
    entities.append(StartPointSensor(coordinator, entry))
    async_add_entities(entities)


class SprayingSensor(CoordinatorEntity[SprayingCoordinator], SensorEntity, RestoreEntity):
    """One derived value, restored across restarts until the next analysis."""

    _attr_has_entity_name = True
    entity_description: SprayingSensorDescription

    def __init__(
        self,
        coordinator: SprayingCoordinator,
        entry: SprayingConfigEntry,
        description: SprayingSensorDescription,
    ) -> None:
        super().__init__(coordinator)
        self.entity_description = description
        self._attr_unique_id = f"{entry.entry_id}_{description.key}"
        self._restored: float | int | datetime | None = None
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, entry.entry_id)},
            name=entry.title,
            manufacturer="Spraying Control",
            model="GPS coverage analysis",
            entry_type=DeviceEntryType.SERVICE,
        )

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        if self.coordinator.data is not None:
            return
        # No analysis has run yet this session; show the previous value rather
        # than going unknown after every restart.
        if (last := await self.async_get_last_state()) is None:
            return
        if last.state in (None, "unknown", "unavailable"):
            return
        if self.entity_description.device_class is SensorDeviceClass.TIMESTAMP:
            self._restored = dt_util.parse_datetime(last.state)
        else:
            try:
                self._restored = float(last.state)
            except ValueError:
                self._restored = None

    @property
    def native_value(self):
        result = self.coordinator.data
        if result is None:
            return self._restored
        return self.entity_description.value_fn(result)

    @property
    def extra_state_attributes(self) -> dict | None:
        result = self.coordinator.data
        if result is None or self.entity_description.attrs_fn is None:
            return None
        return self.entity_description.attrs_fn(result)

    @property
    def available(self) -> bool:
        # Never poll, so the coordinator's own success flag is not meaningful
        # until a run has actually been analysed.
        return self.coordinator.data is not None or self._restored is not None


class StartPointSensor(CoordinatorEntity[SprayingCoordinator], SensorEntity):
    """The refill / start point, exposing latitude and longitude so the map card
    can plot it next to the phone."""

    _attr_has_entity_name = True
    _attr_translation_key = "start_point"
    _attr_icon = "mdi:map-marker-radius"

    def __init__(self, coordinator: SprayingCoordinator, entry: SprayingConfigEntry) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = f"{entry.entry_id}_start_point"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, entry.entry_id)},
            name=entry.title,
            manufacturer="Spraying Control",
            model="GPS coverage analysis",
            entry_type=DeviceEntryType.SERVICE,
        )

    @property
    def native_value(self) -> str:
        return "set" if self.coordinator.start_point is not None else "not set"

    @property
    def extra_state_attributes(self) -> dict | None:
        point = self.coordinator.start_point
        if point is None:
            return {"source": "none"}
        return {
            "latitude": point[0],
            "longitude": point[1],
            "source": "captured" if self.coordinator.session_point is not None else "configured",
        }
