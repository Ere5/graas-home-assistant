"""The same controller seen by two GRAAS entries: which one provides it, and that it stays there."""

from __future__ import annotations

import asyncio
import copy
from datetime import timedelta
from typing import Any
from unittest.mock import AsyncMock, patch

import pytest
from homeassistant.config_entries import ConfigEntryState
from homeassistant.core import HomeAssistant
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er
from homeassistant.setup import async_setup_component
from homeassistant.util import dt as dt_util
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.graas.api import GraasApi, GraasError
from custom_components.graas.const import CONF_API_URL, DEFAULT_API_URL, DOMAIN

SERIAL = "graas-0000000000a1"
TOKEN_OLDER = "graas_pat_" + "a1" * 24
TOKEN_NEWER = "graas_pat_" + "b2" * 24
CREATED = dt_util.utcnow() - timedelta(days=30)


def _entry(unique_id: str, token: str, created_at: Any) -> MockConfigEntry:
    entry = MockConfigEntry(
        domain=DOMAIN,
        title=f"GRAAS {unique_id}",
        unique_id=unique_id,
        data={"api_token": token, CONF_API_URL: DEFAULT_API_URL},
    )
    object.__setattr__(entry, "created_at", created_at)
    return entry


@pytest.fixture
def older() -> MockConfigEntry:
    return _entry("42", TOKEN_OLDER, CREATED)


@pytest.fixture
def newer() -> MockConfigEntry:
    return _entry("43", TOKEN_NEWER, CREATED + timedelta(days=1))


class _Server:
    """/state per token; a token's answer can be held back to order the polls."""

    def __init__(self, state: dict[str, Any]) -> None:
        self.states = {TOKEN_OLDER: copy.deepcopy(state), TOKEN_NEWER: copy.deepcopy(state)}
        self.gates: dict[str, asyncio.Event] = {}
        self.down: set[str] = set()

    async def get_state(self, api: GraasApi) -> dict[str, Any]:
        token = api._token
        if (gate := self.gates.get(token)) is not None:
            await gate.wait()
        if token in self.down:
            raise GraasError("down")
        return copy.deepcopy(self.states[token])


@pytest.fixture
def server(mock_api: dict[str, AsyncMock], state: dict[str, Any]) -> Any:
    server = _Server(state)

    async def get_state(api: GraasApi) -> dict[str, Any]:
        return await server.get_state(api)

    with patch.object(GraasApi, "async_get_state", get_state):
        yield server


async def _setup(hass: HomeAssistant, entry: MockConfigEntry) -> None:
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()


async def _poll(hass: HomeAssistant, *entries: MockConfigEntry) -> None:
    for entry in entries:
        await entry.runtime_data.async_refresh()
        await hass.async_block_till_done()


async def _until(condition: Any) -> None:
    for _ in range(200):
        if condition():
            return
        await asyncio.sleep(0)
    raise AssertionError("condition never met")


def _valve_entry(hass: HomeAssistant) -> str | None:
    registry = er.async_get(hass)
    entity_id = registry.async_get_entity_id("valve", DOMAIN, "zone_101_valve")
    return None if entity_id is None else registry.async_get(entity_id).config_entry_id


def _controller(hass: HomeAssistant, entry: MockConfigEntry) -> dr.DeviceEntry | None:
    for device in dr.async_entries_for_config_entry(dr.async_get(hass), entry.entry_id):
        if (DOMAIN, SERIAL) in device.identifiers:
            return device
    return None


async def test_the_older_entry_keeps_the_controller_when_the_newer_one_answers_first(
    hass: HomeAssistant, server: _Server, older: MockConfigEntry, newer: MockConfigEntry
) -> None:
    """Both start together (Home Assistant start-up); the newer entry's poll comes back first."""
    assert await async_setup_component(hass, DOMAIN, {})
    server.gates[TOKEN_OLDER] = asyncio.Event()
    older.add_to_hass(hass)
    newer.add_to_hass(hass)
    setup_older = hass.async_create_task(hass.config_entries.async_setup(older.entry_id))
    await _until(lambda: older.state is ConfigEntryState.SETUP_IN_PROGRESS)

    assert await hass.config_entries.async_setup(newer.entry_id)
    assert newer.runtime_data.data.devices == {}

    server.gates[TOKEN_OLDER].set()
    assert await setup_older
    await hass.async_block_till_done()

    assert _valve_entry(hass) == older.entry_id
    assert _controller(hass, newer) is None
    device_id = _controller(hass, older).id

    # And it stays there, whichever polls next.
    await _poll(hass, newer, older, newer)
    assert _valve_entry(hass) == older.entry_id
    assert _controller(hass, older).id == device_id
    assert _controller(hass, newer) is None


async def test_reloading_the_providing_entry_does_not_hand_the_controller_over(
    hass: HomeAssistant, server: _Server, older: MockConfigEntry, newer: MockConfigEntry
) -> None:
    await _setup(hass, older)
    await _setup(hass, newer)
    device_id = _controller(hass, older).id

    # The other entry polls while the first one is being reloaded.
    server.gates[TOKEN_OLDER] = asyncio.Event()
    reload = hass.async_create_task(hass.config_entries.async_reload(older.entry_id))
    await _until(lambda: older.state is ConfigEntryState.SETUP_IN_PROGRESS)
    await newer.runtime_data.async_refresh()
    assert newer.runtime_data.data.devices == {}

    server.gates[TOKEN_OLDER].set()
    assert await reload
    await hass.async_block_till_done()
    await _poll(hass, newer, older)

    assert older.state is ConfigEntryState.LOADED
    assert _valve_entry(hass) == older.entry_id
    assert _controller(hass, older).id == device_id
    assert _controller(hass, newer) is None


async def test_an_entry_waiting_to_retry_setup_keeps_its_claim(
    hass: HomeAssistant, server: _Server, older: MockConfigEntry, newer: MockConfigEntry
) -> None:
    """GRAAS (or its network) is down for the first entry: the second does not take its controller."""
    server.down.add(TOKEN_OLDER)
    older.add_to_hass(hass)
    await hass.config_entries.async_setup(older.entry_id)
    assert older.state is ConfigEntryState.SETUP_RETRY

    await _setup(hass, newer)

    assert newer.runtime_data.data.devices == {}
    assert _controller(hass, newer) is None


async def test_the_controllers_owner_takes_precedence_over_an_older_shared_entry(
    hass: HomeAssistant, server: _Server, older: MockConfigEntry, newer: MockConfigEntry
) -> None:
    server.states[TOKEN_OLDER]["devices"][0]["isOwner"] = False
    server.states[TOKEN_NEWER]["devices"][0]["isOwner"] = True
    await _setup(hass, older)
    assert _valve_entry(hass) == older.entry_id

    # The owner's entry is added: the shared one lets go, then the owner's takes the controller, once.
    await _setup(hass, newer)
    assert _controller(hass, older) is None
    await _poll(hass, newer)
    assert _valve_entry(hass) == newer.entry_id
    device_id = _controller(hass, newer).id
    assert hass.states.get(er.async_get(hass).async_get_entity_id("valve", DOMAIN, "zone_101_valve")).state == "closed"

    await _poll(hass, older, newer, older)
    assert _valve_entry(hass) == newer.entry_id
    assert _controller(hass, newer).id == device_id
    assert _controller(hass, older) is None


async def test_the_owner_keeps_the_controller_when_the_shared_entry_answers_first(
    hass: HomeAssistant, server: _Server, older: MockConfigEntry, newer: MockConfigEntry
) -> None:
    server.states[TOKEN_OLDER]["devices"][0]["isOwner"] = True
    server.states[TOKEN_NEWER]["devices"][0]["isOwner"] = False
    assert await async_setup_component(hass, DOMAIN, {})
    server.gates[TOKEN_OLDER] = asyncio.Event()
    older.add_to_hass(hass)
    newer.add_to_hass(hass)
    setup_older = hass.async_create_task(hass.config_entries.async_setup(older.entry_id))
    await _until(lambda: older.state is ConfigEntryState.SETUP_IN_PROGRESS)
    assert await hass.config_entries.async_setup(newer.entry_id)
    server.gates[TOKEN_OLDER].set()
    assert await setup_older
    await hass.async_block_till_done()
    await _poll(hass, newer, older)

    assert _valve_entry(hass) == older.entry_id
    assert _controller(hass, newer) is None


async def test_a_newer_owner_answering_after_an_older_shared_entry_still_gets_the_controller(
    hass: HomeAssistant, server: _Server, older: MockConfigEntry, newer: MockConfigEntry
) -> None:
    """Start-up: the older (shared) entry waits for the newer one, which may own the controller."""
    server.states[TOKEN_OLDER]["devices"][0]["isOwner"] = False
    server.states[TOKEN_NEWER]["devices"][0]["isOwner"] = True
    assert await async_setup_component(hass, DOMAIN, {})
    server.gates[TOKEN_NEWER] = asyncio.Event()
    older.add_to_hass(hass)
    newer.add_to_hass(hass)
    setup_newer = hass.async_create_task(hass.config_entries.async_setup(newer.entry_id))
    await _until(lambda: newer.state is ConfigEntryState.SETUP_IN_PROGRESS)
    assert await hass.config_entries.async_setup(older.entry_id)
    assert older.runtime_data.data.devices == {}
    server.gates[TOKEN_NEWER].set()
    assert await setup_newer
    await hass.async_block_till_done()

    assert _valve_entry(hass) == newer.entry_id
    assert _controller(hass, older) is None


async def test_a_controller_moving_to_another_account_waits_until_the_first_entry_lets_go(
    hass: HomeAssistant, server: _Server, older: MockConfigEntry, newer: MockConfigEntry
) -> None:
    """Unshared from one account, shared with the other: no two entries ever hold its entities."""
    controller = server.states[TOKEN_NEWER]["devices"].pop()
    await _setup(hass, older)
    await _setup(hass, newer)
    assert _valve_entry(hass) == older.entry_id

    server.states[TOKEN_OLDER]["devices"].clear()
    server.states[TOKEN_OLDER]["devices"].append(
        copy.deepcopy(controller) | {"deviceId": "graas-0000000000ff", "id": 9, "zones": []}
    )
    server.states[TOKEN_NEWER]["devices"].append(controller)
    # The first entry still has the controller's devices (one poll without it is not enough):
    # the second waits, and asks the first to poll again.
    await older.runtime_data.async_refresh()
    await hass.async_block_till_done()
    assert _controller(hass, older) is not None
    await _poll(hass, newer)
    assert newer.runtime_data.data.devices == {}
    assert _controller(hass, older) is None

    await _poll(hass, newer)
    assert _valve_entry(hass) == newer.entry_id
    assert hass.states.get(er.async_get(hass).async_get_entity_id("valve", DOMAIN, "zone_101_valve")).state == "closed"
