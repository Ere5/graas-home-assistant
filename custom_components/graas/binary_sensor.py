"""Controller connectivity and per-zone rain postponement."""

from __future__ import annotations

from homeassistant.components.binary_sensor import BinarySensorDeviceClass, BinarySensorEntity
from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .coordinator import GraasConfigEntry, GraasCoordinator
from .entity import GraasEntity, GraasZoneEntity


async def async_setup_entry(
    hass: HomeAssistant, entry: GraasConfigEntry, async_add_entities: AddConfigEntryEntitiesCallback
) -> None:
    coordinator = entry.runtime_data
    entities: list[BinarySensorEntity] = [GraasOnline(coordinator, d) for d in coordinator.data.devices]
    # Only zones where rain postponement exists and is on (lawncare family).
    rain_zones = [z for z, zone in coordinator.data.zones.items() if zone.get("rainPostponeEnabled")]
    entities += [GraasRainPostponed(coordinator, zone_id) for zone_id in rain_zones]
    async_add_entities(entities)

    # Drop rain sensors this entry no longer provides (feature turned off, or
    # created by an earlier version for a type without it), so they don't
    # linger as "Unavailable".
    wanted = {f"zone_{zone_id}_rain_postponed" for zone_id in rain_zones}
    registry = er.async_get(hass)
    for entry_ in er.async_entries_for_config_entry(registry, entry.entry_id):
        is_rain = entry_.domain == "binary_sensor" and entry_.unique_id.endswith("_rain_postponed")
        if is_rain and entry_.unique_id not in wanted:
            registry.async_remove(entry_.entity_id)


class GraasOnline(GraasEntity, BinarySensorEntity):
    """Whether the controller is connected to GRAAS (stays available while offline)."""

    _attr_device_class = BinarySensorDeviceClass.CONNECTIVITY
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
