"""Per-zone run time used when a valve is opened, and a controller's rain delay."""

from __future__ import annotations

import math
from collections.abc import Callable
from datetime import datetime

from homeassistant.components.number import NumberEntity, NumberMode, RestoreNumber
from homeassistant.const import EntityCategory, Platform, UnitOfTime
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.util import dt as dt_util

from .const import MAX_RAIN_DELAY_DAYS, MAX_RUN_MINUTES
from .coordinator import GraasConfigEntry, GraasCoordinator, GraasData
from .entity import (
    GraasEntity,
    GraasZoneEntity,
    async_add_entities_dynamically,
    clamp_run_minutes,
    initial_run_minutes,
)

PARALLEL_UPDATES = 1


async def async_setup_entry(
    hass: HomeAssistant, entry: GraasConfigEntry, async_add_entities: AddConfigEntryEntitiesCallback
) -> None:
    coordinator = entry.runtime_data

    def candidates(data: GraasData) -> dict[str, Callable[[], NumberEntity]]:
        found: dict[str, Callable[[], NumberEntity]] = {
            f"zone_{zone_id}_run_time": lambda zone_id=zone_id: GraasRunTime(coordinator, zone_id)
            for zone_id in data.zones
        }
        for device_id, device in data.devices.items():
            found[f"device_{device['deviceId']}_rain_delay"] = lambda device_id=device_id: GraasRainDelay(
                coordinator, device_id
            )
        return found

    async_add_entities_dynamically(hass, entry, async_add_entities, Platform.NUMBER, candidates)


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
        self._attr_native_value = initial_run_minutes(self.zone_data)
        # The valve uses it even before (or without, when disabled) this entity is added.
        coordinator.run_minutes.setdefault(zone_id, self._attr_native_value)

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        last = await self.async_get_last_number_data()
        if last is not None and last.native_value is not None:
            # Within today's limits, whatever an older version (or a hand edit) stored.
            self._attr_native_value = clamp_run_minutes(last.native_value)
        self.coordinator.run_minutes[self._zone_id] = int(self._attr_native_value)

    async def async_set_native_value(self, value: float) -> None:
        self._attr_native_value = clamp_run_minutes(value)
        self.coordinator.run_minutes[self._zone_id] = self._attr_native_value
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
