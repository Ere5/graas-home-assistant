"""Skip a zone's next scheduled run."""

from __future__ import annotations

from typing import Any

from homeassistant.components.switch import SwitchEntity
from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .coordinator import GraasConfigEntry, GraasCoordinator
from .entity import GraasZoneEntity

PARALLEL_UPDATES = 1


def _has_schedule(zone: dict[str, Any]) -> bool:
    return zone.get("scheduleStatus") not in (None, "no_schedule") or bool(zone.get("skipNextRun"))


async def async_setup_entry(
    hass: HomeAssistant, entry: GraasConfigEntry, async_add_entities: AddConfigEntryEntitiesCallback
) -> None:
    coordinator = entry.runtime_data
    # Only zones with a schedule or program: the others have nothing to skip.
    zone_ids = [z for z, zone in coordinator.data.zones.items() if _has_schedule(zone)]
    async_add_entities(GraasSkipNextRun(coordinator, zone_id) for zone_id in zone_ids)

    # A zone whose schedule was removed loses its switch instead of leaving it "Unavailable".
    wanted = {f"zone_{zone_id}_skip_next_run" for zone_id in zone_ids}
    registry = er.async_get(hass)
    for entry_ in er.async_entries_for_config_entry(registry, entry.entry_id):
        if entry_.domain == "switch" and entry_.unique_id not in wanted:
            registry.async_remove(entry_.entity_id)


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
