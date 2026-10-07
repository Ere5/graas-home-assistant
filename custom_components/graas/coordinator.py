"""Polls the GRAAS state endpoint and shares it with every entity."""

from __future__ import annotations

import logging
from collections.abc import Awaitable
from dataclasses import dataclass, field
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed, HomeAssistantError
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .api import GraasApi, GraasAuthError, GraasCommandError, GraasError, GraasRateLimitError
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
        # Home Assistant device registry ids, by GRAAS device identifier.
        self.device_entry_ids: dict[str, str] = {}

    async def async_command(self, call: Awaitable[Any]) -> Any:
        """Send a command; GRAAS's refusal is shown to the user. Then refresh.

        A rejected token also starts re-authentication, as a failed poll would.
        """
        try:
            result = await call
        except GraasAuthError as err:
            self.config_entry.async_start_reauth(self.hass)
            raise HomeAssistantError(translation_domain=DOMAIN, translation_key="auth_failed") from err
        except GraasRateLimitError as err:
            raise HomeAssistantError(translation_domain=DOMAIN, translation_key="rate_limited") from err
        except GraasCommandError as err:
            raise HomeAssistantError(
                translation_domain=DOMAIN,
                translation_key="command_refused",
                translation_placeholders={"message": str(err)},
            ) from err
        except GraasError as err:
            raise HomeAssistantError(
                translation_domain=DOMAIN,
                translation_key="cannot_connect",
                translation_placeholders={"error": str(err)},
            ) from err
        await self.async_request_refresh()
        return result

    async def _async_update_data(self) -> GraasData:
        try:
            state = await self.api.async_get_state()
        except GraasAuthError as err:
            # Token revoked in the GRAAS app: ask the user for a new one.
            raise ConfigEntryAuthFailed(str(err)) from err
        except GraasRateLimitError as err:
            # Back off; never a reason to ask for a new token.
            raise UpdateFailed(str(err), retry_after=err.retry_after) from err
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
