"""Each GRAAS zone as a water valve: open starts a bounded run, close stops it."""

from __future__ import annotations

from typing import Any

from homeassistant.components.valve import ValveDeviceClass, ValveEntity, ValveEntityFeature
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .api import GraasCommandError, GraasError
from .const import DEFAULT_RUN_MINUTES
from .coordinator import GraasConfigEntry, GraasCoordinator
from .entity import GraasZoneEntity

PARALLEL_UPDATES = 1


async def async_setup_entry(
    hass: HomeAssistant, entry: GraasConfigEntry, async_add_entities: AddConfigEntryEntitiesCallback
) -> None:
    coordinator = entry.runtime_data
    async_add_entities(GraasZoneValve(coordinator, zone_id) for zone_id in coordinator.data.zones)


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

    @property
    def is_closed(self) -> bool:
        return not self.zone_data.get("irrigating", False)

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

    async def async_open_valve(self) -> None:
        minutes = self.coordinator.run_minutes.get(self._zone_id, DEFAULT_RUN_MINUTES)
        await self._command(self.coordinator.api.async_start_zone(self._zone_id, duration_minutes=minutes))

    async def async_close_valve(self) -> None:
        await self._command(self.coordinator.api.async_stop_zone(self._zone_id))

    async def _command(self, call: Any) -> None:
        try:
            await call
        except GraasCommandError as err:
            raise HomeAssistantError(str(err)) from err
        except GraasError as err:
            raise HomeAssistantError(f"GRAAS is unreachable: {err}") from err
        await self.coordinator.async_request_refresh()
