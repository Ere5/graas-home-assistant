"""Each GRAAS zone as a water valve: open starts a bounded run, close stops it."""

from __future__ import annotations

from typing import Any

from homeassistant.components.valve import ValveDeviceClass, ValveEntity, ValveEntityFeature
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ServiceValidationError
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .const import DEFAULT_RUN_MINUTES, DOMAIN, MAX_RUN_LITERS
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
        await self._command(
            self.coordinator.api.async_start_zone(self._zone_id, duration_minutes=duration_minutes, liters=liters)
        )
