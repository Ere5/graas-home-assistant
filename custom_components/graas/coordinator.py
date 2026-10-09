"""Polls the GRAAS state endpoint and shares it with every entity."""

from __future__ import annotations

import logging
from collections.abc import Awaitable
from dataclasses import dataclass, field
from typing import Any

from homeassistant.config_entries import ConfigEntry, ConfigEntryState
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed, HomeAssistantError
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .api import GraasApi, GraasAuthError, GraasCommandError, GraasError, GraasRateLimitError
from .const import DOMAIN, UPDATE_INTERVAL

_LOGGER = logging.getLogger(__name__)

# GRAAS error codes with their own translated message. Others show GRAAS's own words.
_COMMAND_ERROR_KEYS: dict[str, str] = {
    "api_token_read_only": "api_token_read_only",
    "device_offline": "device_offline",
    "zone_already_running": "zone_already_running",
    "litres_need_flow_meter": "litres_need_flow_meter",
    "no_next_run": "no_next_run",
    "zone_not_found": "not_found",
    "device_not_found": "not_found",
    "rate_limited": "rate_limited",
}
# The useful part of these is in GRAAS's message (which value, which limit).
_COMMAND_ERROR_KEYS_WITH_MESSAGE: dict[str, str] = {
    "validation_failed": "validation_failed",
}

# Entry states in which an entry keeps its claim on its controllers.
_MAY_PROVIDE_STATES = frozenset(
    {
        ConfigEntryState.LOADED,
        ConfigEntryState.SETUP_IN_PROGRESS,
        ConfigEntryState.SETUP_RETRY,
        ConfigEntryState.NOT_LOADED,
        ConfigEntryState.UNLOAD_IN_PROGRESS,
    }
)


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
        # Controllers left to another GRAAS entry (logged once each).
        self._skipped_serials: set[str] = set()
        # Serial -> whether this account owns it, for every controller of the last
        # successful poll (None before the first one).
        self.seen: dict[str, bool] | None = None
        # Controllers this poll left to another entry: their devices go at once.
        self.yielded: set[str] = set()
        # Successful polls so far; devices and entities count missed polls by it.
        self.poll_seq = 0
        # GRAAS reported no controller at all after reporting some: nothing is removed.
        self.suspicious_empty = False
        self._warned_empty = False
        # Set by the integration when this entry has controller devices registered.
        self.has_registered_controllers = False
        # Identifier -> consecutive successful polls a device has been missing from.
        self.missing_polls: dict[str, int] = {}
        # The poll whose missing devices were last counted (each poll counts once).
        self.counted_poll = 0
        # Controllers and zones whose devices are still registered (reported, or
        # missing for less than MISSING_POLLS_BEFORE_REMOVAL polls).
        self.kept_serials: set[str] = set()
        self.kept_zone_ids: set[int] = set()

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
            raise command_error(err) from err
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

        devices = state.get("devices", [])
        # Every controller this account sees (and whether it owns it), kept or not:
        # the other GRAAS entries decide from this which of them provides it.
        self.seen = {str(device["deviceId"]): device.get("isOwner") is True for device in devices}
        self.poll_seq += 1
        data = GraasData(account_id=(state.get("account") or {}).get("id"))
        self.yielded = set()
        for device in devices:
            serial = str(device["deviceId"])
            if self._yields(serial):
                # The same controller through a second account (owner + shared
                # user): one entry provides it, so its entities stay unique.
                self.yielded.add(serial)
                if serial not in self._skipped_serials:
                    self._skipped_serials.add(serial)
                    _LOGGER.info("Controller %s is provided by another GRAAS entry; skipping it here", serial)
                continue
            self._skipped_serials.discard(serial)
            device_id = int(device["id"])
            data.devices[device_id] = device
            for zone in device.get("zones", []):
                zone_id = int(zone["id"])
                data.zones[zone_id] = zone
                data.zone_device[zone_id] = device_id
        self._check_empty(bool(devices))
        return data

    def _check_empty(self, any_controllers: bool) -> None:
        """No controller at all after some: likely a GRAAS hiccup, so nothing is removed for it."""
        had_controllers = (self.data is not None and bool(self.data.devices)) or self.has_registered_controllers
        if any_controllers:
            self.suspicious_empty = False
            self._warned_empty = False
            return
        self.suspicious_empty = self.suspicious_empty or had_controllers
        if self.suspicious_empty and not self._warned_empty:
            self._warned_empty = True
            _LOGGER.warning(
                "GRAAS reported no controllers for %s; keeping its devices until it reports some again",
                self.config_entry.title,
            )

    def _yields(self, serial: str) -> bool:
        """Whether another GRAAS entry provides this controller (or will).

        Precedence, highest first: the entry whose account owns the controller,
        then the oldest entry (then the lowest entry id). An entry that has not
        polled yet (starting, reloading, waiting to retry its setup) may still
        report the controller as its owner, so it keeps its place. The decision
        never depends on which poll happens to come back first.
        """
        mine = _precedence(self.config_entry, (self.seen or {}).get(serial, False))
        best: tuple[tuple[bool, float, str], GraasCoordinator | None] | None = None
        lower_providers: list[GraasCoordinator] = []
        for entry in self.hass.config_entries.async_entries(DOMAIN):
            if entry.entry_id == self.config_entry.entry_id or not _may_provide(entry):
                continue
            other = getattr(entry, "runtime_data", None)
            if isinstance(other, GraasCoordinator) and other.seen is not None:
                if serial not in other.seen:
                    if other.provides(serial):
                        # Gone from its account, but its devices are still there (for
                        # a poll or two): taking it now would clash with them.
                        lower_providers.append(other)
                    continue
                rank = _precedence(entry, other.seen[serial])
                polled: GraasCoordinator | None = other
            else:
                rank = _precedence(entry, True)
                polled = None
            if rank < mine:
                if best is None or rank < best[0]:
                    best = (rank, polled)
            elif polled is not None and polled.provides(serial):
                lower_providers.append(polled)
        if best is not None:
            winner = best[1]
            # It has polled but left the controller to this entry: it takes it on
            # its next poll. A controller this entry still had is first let go
            # (its devices removed), so it is picked up on the winner's next regular poll.
            if winner is not None and not winner.provides(serial) and not self.provides(serial):
                self._request_refresh(winner)
            return True
        if lower_providers:
            # Still provided by an entry this one outranks (or that no longer sees it):
            # it lets go on its next poll(s), and this one takes the controller after that.
            for other in lower_providers:
                self._request_refresh(other)
            return True
        return False

    def provides(self, serial: str) -> bool:
        """Whether this entry currently has the controller's devices and entities."""
        if serial in self.kept_serials:
            return True
        return self.data is not None and any(str(device["deviceId"]) == serial for device in self.data.devices.values())

    def _request_refresh(self, other: GraasCoordinator) -> None:
        other.config_entry.async_create_task(
            self.hass, other.async_request_refresh(), f"{DOMAIN} handover refresh", eager_start=False
        )


def _may_provide(entry: ConfigEntry) -> bool:
    """A GRAAS entry that provides controllers now or will: not disabled, not failed.

    Includes an entry that is starting, reloading (briefly not loaded) or waiting
    to retry its setup, so a short outage never hands its controllers over.
    """
    return entry.disabled_by is None and entry.state in _MAY_PROVIDE_STATES


def _precedence(entry: ConfigEntry, owner: bool) -> tuple[bool, float, str]:
    """Lower sorts first: the owner's entry, then the oldest, then the lowest entry id."""
    return (not owner, entry.created_at.timestamp(), entry.entry_id)


def command_error(err: GraasCommandError) -> HomeAssistantError:
    """GRAAS's refusal as a translated error; its own message when the code is unknown."""
    if err.code in _COMMAND_ERROR_KEYS:
        return HomeAssistantError(translation_domain=DOMAIN, translation_key=_COMMAND_ERROR_KEYS[err.code])
    if err.code in _COMMAND_ERROR_KEYS_WITH_MESSAGE:
        return HomeAssistantError(
            translation_domain=DOMAIN,
            translation_key=_COMMAND_ERROR_KEYS_WITH_MESSAGE[err.code],
            translation_placeholders={"message": str(err)},
        )
    return HomeAssistantError(
        translation_domain=DOMAIN,
        translation_key="command_refused",
        translation_placeholders={"message": str(err)},
    )
