"""Per-zone run time used when a valve is opened, and a controller's rain delay."""

from __future__ import annotations

import math
from datetime import datetime

from homeassistant.components.number import NumberEntity, NumberMode, RestoreNumber
from homeassistant.const import EntityCategory, UnitOfTime
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.util import dt as dt_util

from .const import DEFAULT_RUN_MINUTES, MAX_RAIN_DELAY_DAYS, MAX_RUN_MINUTES
from .coordinator import GraasConfigEntry, GraasCoordinator
from .entity import GraasEntity, GraasZoneEntity

PARALLEL_UPDATES = 1


async def async_setup_entry(
    hass: HomeAssistant, entry: GraasConfigEntry, async_add_entities: AddConfigEntryEntitiesCallback
) -> None:
    coordinator = entry.runtime_data
    entities: list[NumberEntity] = [GraasRunTime(coordinator, zone_id) for zone_id in coordinator.data.zones]
    entities += [GraasRainDelay(coordinator, device_id) for device_id in coordinator.data.devices]
    async_add_entities(entities)


class GraasRunTime(GraasZoneEntity, RestoreNumber):
    """Stored in Home Assistant only; GRAAS gets it with each start."""

    _attr_entity_category = EntityCategory.CONFIG
    _attr_mode = NumberMode.BOX
    _attr_native_min_value = 1
    _attr_native_max_value = MAX_RUN_MINUTES
    _attr_native_step = 1
    _attr_native_unit_of_measurement = UnitOfTime.MINUTES
    _attr_translation_key = "run_time"

    def __init__(self, coordinator: GraasCoordinator, zone_id: int) -> None:
        super().__init__(coordinator, zone_id, "run_time")
        # Start from the zone's own schedule length when it has one.
        scheduled = self.zone_data.get("scheduleDurationMinutes")
        initial = int(scheduled) if isinstance(scheduled, (int, float)) and scheduled > 0 else DEFAULT_RUN_MINUTES
        self._attr_native_value = min(max(initial, 1), MAX_RUN_MINUTES)

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        last = await self.async_get_last_number_data()
        if last is not None and last.native_value is not None:
            self._attr_native_value = int(last.native_value)
        self.coordinator.run_minutes[self._zone_id] = int(self._attr_native_value or DEFAULT_RUN_MINUTES)

    async def async_set_native_value(self, value: float) -> None:
        self._attr_native_value = int(value)
        self.coordinator.run_minutes[self._zone_id] = int(value)
        self.async_write_ha_state()


class GraasRainDelay(GraasEntity, NumberEntity):
    """Days left of the controller's rain delay; 0 = none. Setting it starts or ends one.

    While it lasts, every zone skips its scheduled and program runs (manual runs
    still work). GRAAS keeps it even while the controller is offline.
    """

    _attr_mode = NumberMode.BOX
    _attr_native_min_value = 0
    _attr_native_max_value = MAX_RAIN_DELAY_DAYS
    _attr_native_step = 1
    _attr_native_unit_of_measurement = UnitOfTime.DAYS
    _attr_translation_key = "rain_delay"

    def __init__(self, coordinator: GraasCoordinator, device_id: int) -> None:
        super().__init__(coordinator, device_id, "rain_delay")

    @property
    def native_value(self) -> int:
        until = self._paused_until()
        if until is None:
            return 0
        days = (until - dt_util.now()).total_seconds() / 86400
        # Rounded, but a delay that is still on never shows as 0 ("off").
        return max(1, min(MAX_RAIN_DELAY_DAYS, math.floor(days + 0.5)))

    def _paused_until(self) -> datetime | None:
        value = self.device_data.get("wateringPausedUntil")
        until = dt_util.parse_datetime(value) if isinstance(value, str) else None
        return until if until is not None and until > dt_util.now() else None

    async def async_set_native_value(self, value: float) -> None:
        days = int(value)
        if days <= 0:
            await self._command(self.coordinator.api.async_end_rain_delay(self._device_id))
        else:
            await self._command(self.coordinator.api.async_set_rain_delay(self._device_id, days))
