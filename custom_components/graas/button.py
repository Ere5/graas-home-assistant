"""Stop every zone of a controller."""

from __future__ import annotations

from collections.abc import Callable

from homeassistant.components.button import ButtonEntity
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .coordinator import GraasConfigEntry, GraasCoordinator, GraasData
from .entity import GraasEntity, async_add_entities_dynamically

PARALLEL_UPDATES = 1


async def async_setup_entry(
    hass: HomeAssistant, entry: GraasConfigEntry, async_add_entities: AddConfigEntryEntitiesCallback
) -> None:
    coordinator = entry.runtime_data

    def candidates(data: GraasData) -> dict[str, Callable[[], ButtonEntity]]:
        return {
            f"device_{device['deviceId']}_stop_all": lambda device_id=device_id: GraasStopAll(coordinator, device_id)
            for device_id, device in data.devices.items()
        }

    async_add_entities_dynamically(hass, entry, async_add_entities, Platform.BUTTON, candidates)


class GraasStopAll(GraasEntity, ButtonEntity):
    _attr_translation_key = "stop_all"

    def __init__(self, coordinator: GraasCoordinator, device_id: int) -> None:
        super().__init__(coordinator, device_id, "stop_all")

    async def async_press(self) -> None:
        await self._command(self.coordinator.api.async_stop_all(self._device_id))
