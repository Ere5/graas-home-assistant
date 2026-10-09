"""Skip a zone's next scheduled run."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from homeassistant.components.switch import SwitchEntity
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .coordinator import GraasConfigEntry, GraasCoordinator, GraasData
from .entity import GraasZoneEntity, async_add_entities_dynamically

PARALLEL_UPDATES = 1


def _has_schedule(zone: dict[str, Any]) -> bool:
    return zone.get("scheduleStatus") not in (None, "no_schedule") or bool(zone.get("skipNextRun"))


async def async_setup_entry(
    hass: HomeAssistant, entry: GraasConfigEntry, async_add_entities: AddConfigEntryEntitiesCallback
) -> None:
    coordinator = entry.runtime_data

    def candidates(data: GraasData) -> dict[str, Callable[[], SwitchEntity]]:
        # Only zones with a schedule or program: the others have nothing to skip.
        return {
            f"zone_{zone_id}_skip_next_run": lambda zone_id=zone_id: GraasSkipNextRun(coordinator, zone_id)
            for zone_id, zone in data.zones.items()
            if _has_schedule(zone)
        }

    # A zone whose schedule was removed loses its switch instead of keeping a useless one;
    # one that gets a schedule gains it.
    async_add_entities_dynamically(
        hass, entry, async_add_entities, Platform.SWITCH, candidates, removable=lambda unique_id: True
    )


class GraasSkipNextRun(GraasZoneEntity, SwitchEntity):
    """On: the zone's next scheduled or program run is skipped. Turns itself off once that run has passed."""

    _attr_translation_key = "skip_next_run"

    def __init__(self, coordinator: GraasCoordinator, zone_id: int) -> None:
        super().__init__(coordinator, zone_id, "skip_next_run")

    @property
    def is_on(self) -> bool:
        return bool(self.zone_data.get("skipNextRun"))

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        return {"skip_until": self.zone_data.get("skipUntil")}

    async def async_turn_on(self, **kwargs: Any) -> None:
        await self._command(self.coordinator.api.async_skip_next_run(self._zone_id))

    async def async_turn_off(self, **kwargs: Any) -> None:
        await self._command(self.coordinator.api.async_cancel_skip(self._zone_id))
