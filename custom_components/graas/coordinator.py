"""Polls the GRAAS state endpoint and shares it with every entity."""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .api import GraasApi, GraasAuthError, GraasError
from .const import DOMAIN, UPDATE_INTERVAL

_LOGGER = logging.getLogger(__name__)


@dataclass
class GraasData:
    """One poll, indexed for the entities."""

    account_id: int | None = None
    devices: dict[int, dict[str, Any]] = field(default_factory=dict)
    zones: dict[int, dict[str, Any]] = field(default_factory=dict)
    zone_device: dict[int, int] = field(default_factory=dict)


type GraasConfigEntry = ConfigEntry[GraasCoordinator]


class GraasCoordinator(DataUpdateCoordinator[GraasData]):
    """Fetches /api/ha/state every UPDATE_INTERVAL."""

    config_entry: GraasConfigEntry

    def __init__(self, hass: HomeAssistant, entry: GraasConfigEntry, api: GraasApi) -> None:
        super().__init__(hass, _LOGGER, config_entry=entry, name=DOMAIN, update_interval=UPDATE_INTERVAL)
        self.api = api
        # Per-zone run time for "open valve", set by the zone's number entity.
        self.run_minutes: dict[int, int] = {}

    async def _async_update_data(self) -> GraasData:
        try:
            state = await self.api.async_get_state()
        except GraasAuthError as err:
            # Token revoked in the GRAAS app: ask the user for a new one.
            raise ConfigEntryAuthFailed(str(err)) from err
        except GraasError as err:
            raise UpdateFailed(str(err)) from err

        data = GraasData(account_id=(state.get("account") or {}).get("id"))
        for device in state.get("devices", []):
            device_id = int(device["id"])
            data.devices[device_id] = device
            for zone in device.get("zones", []):
                zone_id = int(zone["id"])
                data.zones[zone_id] = zone
                data.zone_device[zone_id] = device_id
        return data
