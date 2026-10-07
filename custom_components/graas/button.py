"""Stop every zone of a controller."""

from __future__ import annotations

from homeassistant.components.button import ButtonEntity
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .api import GraasCommandError, GraasError
from .coordinator import GraasConfigEntry, GraasCoordinator
from .entity import GraasEntity

PARALLEL_UPDATES = 1


async def async_setup_entry(
    hass: HomeAssistant, entry: GraasConfigEntry, async_add_entities: AddConfigEntryEntitiesCallback
) -> None:
    coordinator = entry.runtime_data
    async_add_entities(GraasStopAll(coordinator, device_id) for device_id in coordinator.data.devices)


class GraasStopAll(GraasEntity, ButtonEntity):
    _attr_translation_key = "stop_all"

    def __init__(self, coordinator: GraasCoordinator, device_id: int) -> None:
        super().__init__(coordinator, device_id, "stop_all")

    async def async_press(self) -> None:
        try:
            await self.coordinator.api.async_stop_all(self._device_id)
        except GraasCommandError as err:
            raise HomeAssistantError(str(err)) from err
        except GraasError as err:
            raise HomeAssistantError(f"GRAAS is unreachable: {err}") from err
        await self.coordinator.async_request_refresh()
