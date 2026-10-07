"""Skip next run (per zone) and rain delay (per controller): holding scheduled watering."""

from __future__ import annotations

from datetime import timedelta
from typing import Any
from unittest.mock import AsyncMock

import pytest
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import entity_registry as er
from homeassistant.util import dt as dt_util
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.graas.api import GraasCommandError
from custom_components.graas.const import DOMAIN

SKIP = "zone_101_skip_next_run"
DELAY = "device_graas-0000000000a1_rain_delay"
PAUSED_UNTIL = "device_graas-0000000000a1_paused_until"


async def _setup(hass: HomeAssistant, entry: MockConfigEntry) -> None:
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()


def _eid(hass: HomeAssistant, domain: str, unique_id: str) -> str:
    entity_id = er.async_get(hass).async_get_entity_id(domain, DOMAIN, unique_id)
    assert entity_id is not None, unique_id
    return entity_id


async def test_scheduled_zones_get_a_skip_next_run_switch(
    hass: HomeAssistant, mock_api: dict[str, AsyncMock], config_entry: MockConfigEntry
) -> None:
    await _setup(hass, config_entry)

    assert hass.states.get(_eid(hass, "switch", SKIP)).state == "off"
    # Beds has no schedule: nothing to skip.
    assert er.async_get(hass).async_get_entity_id("switch", DOMAIN, "zone_102_skip_next_run") is None


async def test_turning_the_switch_on_skips_and_off_cancels(
    hass: HomeAssistant, mock_api: dict[str, AsyncMock], config_entry: MockConfigEntry
) -> None:
    await _setup(hass, config_entry)
    entity_id = _eid(hass, "switch", SKIP)

    await hass.services.async_call("switch", "turn_on", {"entity_id": entity_id}, blocking=True)
    mock_api["skip"].assert_awaited_once_with(101)
    await hass.services.async_call("switch", "turn_off", {"entity_id": entity_id}, blocking=True)
    mock_api["unskip"].assert_awaited_once_with(101)


async def test_a_skipped_zone_shows_until_when(
    hass: HomeAssistant, mock_api: dict[str, AsyncMock], config_entry: MockConfigEntry, state: dict[str, Any]
) -> None:
    lawn = state["devices"][0]["zones"][0]
    lawn.update(skipNextRun=True, skipUntil="2026-10-05T07:01:00+03:00", nextRun="2026-10-06T06:00:00+03:00")
    await _setup(hass, config_entry)

    switch = hass.states.get(_eid(hass, "switch", SKIP))
    assert switch.state == "on"
    assert switch.attributes["skip_until"] == "2026-10-05T07:01:00+03:00"


async def test_rain_delay_is_set_in_days_and_zero_ends_it(
    hass: HomeAssistant, mock_api: dict[str, AsyncMock], config_entry: MockConfigEntry
) -> None:
    await _setup(hass, config_entry)
    entity_id = _eid(hass, "number", DELAY)
    assert hass.states.get(entity_id).state == "0"

    await hass.services.async_call("number", "set_value", {"entity_id": entity_id, "value": 3}, blocking=True)
    mock_api["delay"].assert_awaited_once_with(7, 3)
    await hass.services.async_call("number", "set_value", {"entity_id": entity_id, "value": 0}, blocking=True)
    mock_api["undelay"].assert_awaited_once_with(7)


async def test_a_paused_controller_shows_days_left_and_until_when(
    hass: HomeAssistant, mock_api: dict[str, AsyncMock], config_entry: MockConfigEntry, state: dict[str, Any]
) -> None:
    until = dt_util.now() + timedelta(days=2, minutes=61)
    state["devices"][0]["wateringPausedUntil"] = until.isoformat()
    await _setup(hass, config_entry)

    assert hass.states.get(_eid(hass, "number", DELAY)).state == "2"
    paused = hass.states.get(_eid(hass, "sensor", PAUSED_UNTIL))
    assert dt_util.parse_datetime(paused.state) == until.replace(microsecond=0)


async def test_the_last_hours_of_a_delay_still_count_as_a_day(
    hass: HomeAssistant, mock_api: dict[str, AsyncMock], config_entry: MockConfigEntry, state: dict[str, Any]
) -> None:
    state["devices"][0]["wateringPausedUntil"] = (dt_util.now() + timedelta(hours=3)).isoformat()
    await _setup(hass, config_entry)

    # Still paused: never shown as 0 (which means "off").
    assert hass.states.get(_eid(hass, "number", DELAY)).state == "1"


async def test_holds_work_while_the_controller_is_offline(
    hass: HomeAssistant, mock_api: dict[str, AsyncMock], config_entry: MockConfigEntry, state: dict[str, Any]
) -> None:
    """GRAAS keeps the hold and sends it when the controller reconnects."""
    state["devices"][0]["online"] = False
    await _setup(hass, config_entry)

    assert hass.states.get(_eid(hass, "switch", SKIP)).state == "off"
    assert hass.states.get(_eid(hass, "number", DELAY)).state == "0"


async def test_a_refused_skip_is_shown_to_the_user(
    hass: HomeAssistant, mock_api: dict[str, AsyncMock], config_entry: MockConfigEntry
) -> None:
    mock_api["skip"].side_effect = GraasCommandError("This zone has no upcoming run to skip.", "no_next_run")
    await _setup(hass, config_entry)

    with pytest.raises(HomeAssistantError, match="no upcoming run"):
        await hass.services.async_call("switch", "turn_on", {"entity_id": _eid(hass, "switch", SKIP)}, blocking=True)


async def test_the_dashboard_has_the_holds(
    hass: HomeAssistant, mock_api: dict[str, AsyncMock], config_entry: MockConfigEntry
) -> None:
    await _setup(hass, config_entry)
    response = await hass.services.async_call(DOMAIN, "dashboard", {}, blocking=True, return_response=True)
    controller, lawn, beds = response["config"]["views"][0]["sections"]

    by_entity = {c.get("entity"): c for c in controller["cards"]}
    delay = by_entity[_eid(hass, "number", DELAY)]
    assert delay["name"] == "Rain delay"
    assert delay["features"] == [{"type": "numeric-input", "style": "buttons"}]
    paused = by_entity[_eid(hass, "sensor", PAUSED_UNTIL)]
    # Only while a delay is on.
    assert paused["visibility"] == [{"condition": "state", "entity": _eid(hass, "number", DELAY), "state_not": "0"}]

    skip = {c.get("entity"): c for c in lawn["cards"]}[_eid(hass, "switch", SKIP)]
    assert skip["name"] == "Skip next run"
    assert skip["features"] == [{"type": "toggle"}]
    assert all("skip_next_run" not in (c.get("entity") or "") for c in beds["cards"])
