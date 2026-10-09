"""After open/close the valve shows opening/closing until GRAAS confirms it, instead of snapping back."""

from __future__ import annotations

import copy
from datetime import timedelta
from typing import Any
from unittest.mock import AsyncMock

import pytest
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import entity_registry as er
from homeassistant.util import dt as dt_util
from pytest_homeassistant_custom_component.common import MockConfigEntry, async_fire_time_changed

from custom_components.graas.api import GraasCommandError
from custom_components.graas.const import DOMAIN


@pytest.fixture
def polled_state(mock_api: dict[str, AsyncMock], state: dict[str, Any]) -> dict[str, Any]:
    """Each poll returns a copy: changing `state` takes effect at the next poll only."""
    mock_api["get_state"].side_effect = lambda: copy.deepcopy(state)
    return state


async def _setup(hass: HomeAssistant, entry: MockConfigEntry) -> str:
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    return er.async_get(hass).async_get_entity_id("valve", DOMAIN, "zone_101_valve")


async def _later(hass: HomeAssistant, seconds: float) -> None:
    async_fire_time_changed(hass, dt_util.utcnow() + timedelta(seconds=seconds))
    await hass.async_block_till_done()


async def test_an_opened_valve_shows_opening_until_graas_reports_the_run(
    hass: HomeAssistant, mock_api: dict[str, AsyncMock], config_entry: MockConfigEntry, polled_state: dict[str, Any]
) -> None:
    valve = await _setup(hass, config_entry)

    await hass.services.async_call("valve", "open_valve", {"entity_id": valve}, blocking=True)
    # The refresh right after the command doesn't show the run yet (the task is still pending).
    assert hass.states.get(valve).state == "opening"

    polls = mock_api["get_state"].await_count
    polled_state["devices"][0]["zones"][0]["irrigating"] = True
    await _later(hass, 11)

    # A follow-up poll a few seconds later saw it.
    assert mock_api["get_state"].await_count > polls
    assert hass.states.get(valve).state == "open"


async def test_a_closed_valve_shows_closing_until_graas_reports_the_stop(
    hass: HomeAssistant, mock_api: dict[str, AsyncMock], config_entry: MockConfigEntry, polled_state: dict[str, Any]
) -> None:
    await _setup(hass, config_entry)
    beds = er.async_get(hass).async_get_entity_id("valve", DOMAIN, "zone_102_valve")
    assert hass.states.get(beds).state == "open"

    await hass.services.async_call("valve", "close_valve", {"entity_id": beds}, blocking=True)
    assert hass.states.get(beds).state == "closing"

    polled_state["devices"][0]["zones"][1]["irrigating"] = False
    await config_entry.runtime_data.async_refresh()
    await hass.async_block_till_done()
    assert hass.states.get(beds).state == "closed"


async def test_an_unconfirmed_open_falls_back_to_what_graas_reports(
    hass: HomeAssistant, mock_api: dict[str, AsyncMock], config_entry: MockConfigEntry, polled_state: dict[str, Any]
) -> None:
    """The controller never started (e.g. it went offline): after a minute the valve shows closed again."""
    valve = await _setup(hass, config_entry)

    await hass.services.async_call("valve", "open_valve", {"entity_id": valve}, blocking=True)
    await _later(hass, 31)
    assert hass.states.get(valve).state == "opening"

    await _later(hass, 61)
    assert hass.states.get(valve).state == "closed"


async def test_a_refused_open_does_not_show_opening(
    hass: HomeAssistant, mock_api: dict[str, AsyncMock], config_entry: MockConfigEntry, polled_state: dict[str, Any]
) -> None:
    valve = await _setup(hass, config_entry)
    mock_api["start"].side_effect = GraasCommandError("Nope", "some_future_code")

    with pytest.raises(HomeAssistantError):
        await hass.services.async_call("valve", "open_valve", {"entity_id": valve}, blocking=True)

    assert hass.states.get(valve).state == "closed"


async def test_start_zone_action_also_shows_opening(
    hass: HomeAssistant, mock_api: dict[str, AsyncMock], config_entry: MockConfigEntry, polled_state: dict[str, Any]
) -> None:
    valve = await _setup(hass, config_entry)

    await hass.services.async_call(DOMAIN, "start_zone", {"entity_id": valve, "duration_minutes": 5}, blocking=True)

    assert hass.states.get(valve).state == "opening"
