"""Controller connectivity and per-zone rain postponement."""

from __future__ import annotations

from collections.abc import Callable

from homeassistant.components.binary_sensor import BinarySensorDeviceClass, BinarySensorEntity
from homeassistant.const import EntityCategory, Platform
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .coordinator import GraasConfigEntry, GraasCoordinator, GraasData
from .entity import GraasEntity, GraasZoneEntity, async_add_entities_dynamically

# Read-only: entities only read the coordinator's data.
PARALLEL_UPDATES = 0


async def async_setup_entry(
    hass: HomeAssistant, entry: GraasConfigEntry, async_add_entities: AddConfigEntryEntitiesCallback
) -> None:
    coordinator = entry.runtime_data

    def candidates(data: GraasData) -> dict[str, Callable[[], BinarySensorEntity]]:
        found: dict[str, Callable[[], BinarySensorEntity]] = {
            f"device_{device['deviceId']}_online": lambda device_id=device_id: GraasOnline(coordinator, device_id)
            for device_id, device in data.devices.items()
        }
        # Only zones where rain postponement exists and is on (lawncare family).
        for zone_id, zone in data.zones.items():
            if zone.get("rainPostponeEnabled"):
                found[f"zone_{zone_id}_rain_postponed"] = lambda zone_id=zone_id: GraasRainPostponed(
                    coordinator, zone_id
                )
        return found

    # Rain sensors this entry no longer provides (feature turned off, or created
    # by an earlier version for a type without it) are dropped, so they don't
    # linger as "Unavailable"; turned on again, the sensor comes back.
    async_add_entities_dynamically(
        hass,
        entry,
        async_add_entities,
        Platform.BINARY_SENSOR,
        candidates,
        removable=lambda unique_id: unique_id.endswith("_rain_postponed"),
    )


class GraasOnline(GraasEntity, BinarySensorEntity):
    """Whether the controller is connected to GRAAS (stays available while offline)."""

    _attr_device_class = BinarySensorDeviceClass.CONNECTIVITY
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_translation_key = "online"

    def __init__(self, coordinator: GraasCoordinator, device_id: int) -> None:
        super().__init__(coordinator, device_id, "online")

    @property
    def is_on(self) -> bool:
        return bool(self.device_data.get("online"))


class GraasRainPostponed(GraasZoneEntity, BinarySensorEntity):
    """On while rain in the forecast holds this zone's watering back."""

    _attr_translation_key = "rain_postponed"

    def __init__(self, coordinator: GraasCoordinator, zone_id: int) -> None:
        super().__init__(coordinator, zone_id, "rain_postponed")

    @property
    def is_on(self) -> bool:
        return bool(self.zone_data.get("rainPostponed"))
