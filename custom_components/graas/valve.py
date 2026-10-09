"""Each GRAAS zone as a water valve: open starts a bounded run, close stops it."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from datetime import datetime
from typing import Any

from homeassistant.components.valve import ValveDeviceClass, ValveEntity, ValveEntityFeature
from homeassistant.const import Platform
from homeassistant.core import CALLBACK_TYPE, HassJob, HomeAssistant, callback
from homeassistant.exceptions import ServiceValidationError
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.helpers.event import async_call_later

from .const import DOMAIN, FOLLOW_UP_REFRESH_DELAYS, MAX_RUN_LITERS, OPTIMISTIC_TIMEOUT
from .coordinator import GraasConfigEntry, GraasCoordinator, GraasData
from .entity import GraasZoneEntity, async_add_entities_dynamically, initial_run_minutes

PARALLEL_UPDATES = 1


async def async_setup_entry(
    hass: HomeAssistant, entry: GraasConfigEntry, async_add_entities: AddConfigEntryEntitiesCallback
) -> None:
    coordinator = entry.runtime_data

    def candidates(data: GraasData) -> dict[str, Callable[[], ValveEntity]]:
        return {
            f"zone_{zone_id}_valve": lambda zone_id=zone_id: GraasZoneValve(coordinator, zone_id)
            for zone_id in data.zones
        }

    async_add_entities_dynamically(hass, entry, async_add_entities, Platform.VALVE, candidates)


class GraasZoneValve(GraasZoneEntity, ValveEntity):
    """Open = water this zone for its run time (never open-ended)."""

    _attr_device_class = ValveDeviceClass.WATER
    _attr_supported_features = ValveEntityFeature.OPEN | ValveEntityFeature.CLOSE
    _attr_reports_position = False
    _attr_translation_key = "zone"
    _needs_online = True
    # The zone's main control: named after the zone device itself ("Lawn").
    _attr_name = None

    def __init__(self, coordinator: GraasCoordinator, zone_id: int) -> None:
        super().__init__(coordinator, zone_id, "valve")
        # After a command: True = expecting the zone to water, False = to stop.
        # GRAAS reports a run only once the controller has picked the task up,
        # so until then the valve says "opening"/"closing" instead of snapping back.
        self._expected: bool | None = None
        self._timers: list[CALLBACK_TYPE] = []

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        self.async_on_remove(self._async_cancel_timers)

    @property
    def _watering(self) -> bool:
        return bool(self.zone_data.get("irrigating", False))

    @property
    def is_closed(self) -> bool:
        return not self._watering

    @property
    def is_opening(self) -> bool:
        return self._expected is True and not self._watering

    @property
    def is_closing(self) -> bool:
        return self._expected is False and self._watering

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        zone = self.zone_data
        per_plant = bool((self.device_data.get("capabilities") or {}).get("litersPerPlant"))
        return {
            "valve_number": zone.get("valve"),
            # Irigator: a litres amount is per plant, times this many plants.
            "liters_per_plant": per_plant,
            "plant_count": zone.get("plantCount") if per_plant else None,
            "running_since": zone.get("runningSince"),
            "target_duration_minutes": zone.get("targetDurationMinutes"),
            "target_liters": zone.get("targetLiters"),
            "last_irrigation": zone.get("lastIrrigation"),
        }

    @callback
    def _handle_coordinator_update(self) -> None:
        # GRAAS confirms what was asked for: the real state takes over.
        zones = self.coordinator.data.zones
        if self._expected is not None and self._zone_id in zones and self._watering == self._expected:
            self._async_end_optimistic()
        super()._handle_coordinator_update()

    async def async_open_valve(self) -> None:
        minutes = self.coordinator.run_minutes.get(self._zone_id) or initial_run_minutes(self.zone_data)
        await self._valve_command(
            self.coordinator.api.async_start_zone(self._zone_id, duration_minutes=minutes), expect_open=True
        )

    async def async_close_valve(self) -> None:
        await self._valve_command(self.coordinator.api.async_stop_zone(self._zone_id), expect_open=False)

    async def async_start_zone(self, duration_minutes: int | None = None, liters: float | None = None) -> None:
        """graas.start_zone: a run of the given minutes or litres (exactly one, checked by the schema)."""
        if liters is not None and (self.device_data.get("capabilities") or {}).get("litersPerPlant"):
            # Irigator: the amount is per plant, so the run is litres × plants.
            plants = self.zone_data.get("plantCount")
            plants = max(1, int(plants)) if isinstance(plants, (int, float)) else 1
            if liters * plants > MAX_RUN_LITERS:
                raise ServiceValidationError(
                    translation_domain=DOMAIN,
                    translation_key="too_many_liters",
                    translation_placeholders={
                        "liters": f"{liters:g}",
                        "plants": str(plants),
                        "total": f"{liters * plants:g}",
                        "max": str(MAX_RUN_LITERS),
                    },
                )
        await self._valve_command(
            self.coordinator.api.async_start_zone(self._zone_id, duration_minutes=duration_minutes, liters=liters),
            expect_open=True,
        )

    async def _valve_command(self, call: Awaitable[Any], *, expect_open: bool) -> None:
        """Send the command; on success show opening/closing until GRAAS confirms it (or a timeout)."""
        await self._command(call)
        self._async_cancel_timers()
        if self._watering == expect_open:
            # Already as asked (the refresh straight after the command saw it).
            self._expected = None
        else:
            self._expected = expect_open
            self._timers.append(
                async_call_later(
                    self.hass, OPTIMISTIC_TIMEOUT, HassJob(self._async_optimistic_timeout, cancel_on_shutdown=True)
                )
            )
        for delay in FOLLOW_UP_REFRESH_DELAYS:
            self._timers.append(
                async_call_later(self.hass, delay, HassJob(self._async_follow_up_refresh, cancel_on_shutdown=True))
            )
        self.async_write_ha_state()

    @callback
    def _async_follow_up_refresh(self, _now: datetime) -> None:
        self.hass.async_create_task(self.coordinator.async_request_refresh(), eager_start=True)

    @callback
    def _async_optimistic_timeout(self, _now: datetime) -> None:
        # GRAAS never confirmed it: show what GRAAS reports.
        self._async_end_optimistic()
        self.async_write_ha_state()

    @callback
    def _async_end_optimistic(self) -> None:
        self._expected = None
        self._async_cancel_timers()

    @callback
    def _async_cancel_timers(self) -> None:
        for cancel in self._timers:
            cancel()
        self._timers.clear()
