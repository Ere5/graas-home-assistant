"""Controllers, zones and readings that appear or go away while Home Assistant runs."""

from __future__ import annotations

import copy
from typing import Any
from unittest.mock import AsyncMock

import pytest
from homeassistant.core import HomeAssistant
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.graas import async_remove_config_entry_device
from custom_components.graas.const import DOMAIN

from .conftest import find_device

SERIAL = "graas-0000000000a1"
OTHER = "graas-0000000000b2"


@pytest.fixture
def polled_state(mock_api: dict[str, AsyncMock], state: dict[str, Any]) -> dict[str, Any]:
    mock_api["get_state"].side_effect = lambda: copy.deepcopy(state)
    return state


async def _setup(hass: HomeAssistant, entry: MockConfigEntry) -> None:
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()


async def _poll(hass: HomeAssistant, entry: MockConfigEntry) -> None:
    await entry.runtime_data.async_refresh()
    await hass.async_block_till_done()


def _eid(hass: HomeAssistant, domain: str, unique_id: str) -> str | None:
    return er.async_get(hass).async_get_entity_id(domain, DOMAIN, unique_id)


def _second_controller(state: dict[str, Any]) -> dict[str, Any]:
    other = copy.deepcopy(state["devices"][0])
    other.update(id=8, deviceId=OTHER, name="Greenhouse")
    for zone, zone_id in zip(other["zones"], (201, 202), strict=True):
        zone["id"] = zone_id
    return other


async def test_a_new_controller_gets_its_devices_and_entities_without_a_reload(
    hass: HomeAssistant, mock_api: dict[str, AsyncMock], config_entry: MockConfigEntry, polled_state: dict[str, Any]
) -> None:
    await _setup(hass, config_entry)
    assert _eid(hass, "valve", "zone_201_valve") is None

    polled_state["devices"].append(_second_controller(polled_state))
    await _poll(hass, config_entry)

    controller = find_device(hass, OTHER)
    zone = find_device(hass, f"{OTHER}_zone_201")
    assert controller is not None and zone is not None
    assert zone.via_device_id == controller.id
    valve = _eid(hass, "valve", "zone_201_valve")
    assert hass.states.get(valve).state == "closed"
    assert er.async_get(hass).async_get(valve).device_id == zone.id
    for domain, unique_id in (
        ("button", f"device_{OTHER}_stop_all"),
        ("binary_sensor", f"device_{OTHER}_online"),
        ("number", f"device_{OTHER}_rain_delay"),
        ("number", "zone_201_run_time"),
        ("sensor", "zone_201_soil_moisture"),
        ("switch", "zone_201_skip_next_run"),
        ("binary_sensor", "zone_202_rain_postponed"),
    ):
        assert _eid(hass, domain, unique_id) is not None, unique_id
    # Nothing that already existed is added twice.
    assert len(hass.states.async_entity_ids("valve")) == 4


async def test_a_removed_controller_loses_its_devices_and_entities(
    hass: HomeAssistant, mock_api: dict[str, AsyncMock], config_entry: MockConfigEntry, polled_state: dict[str, Any]
) -> None:
    polled_state["devices"].append(_second_controller(polled_state))
    await _setup(hass, config_entry)
    assert _eid(hass, "valve", "zone_201_valve") is not None

    polled_state["devices"].pop()
    await _poll(hass, config_entry)
    # One poll without it could be a GRAAS hiccup: kept (unavailable) until the next one.
    assert find_device(hass, OTHER) is not None
    assert hass.states.get(_eid(hass, "valve", "zone_201_valve")).state == "unavailable"
    await _poll(hass, config_entry)

    assert find_device(hass, OTHER) is None
    assert find_device(hass, f"{OTHER}_zone_201") is None
    assert _eid(hass, "valve", "zone_201_valve") is None
    assert _eid(hass, "button", f"device_{OTHER}_stop_all") is None
    # The remaining controller is untouched.
    assert hass.states.get(_eid(hass, "valve", "zone_101_valve")).state == "closed"


async def test_a_new_zone_appears_and_a_deleted_one_goes(
    hass: HomeAssistant, mock_api: dict[str, AsyncMock], config_entry: MockConfigEntry, polled_state: dict[str, Any]
) -> None:
    await _setup(hass, config_entry)
    zones = polled_state["devices"][0]["zones"]
    new_zone = copy.deepcopy(zones[0])
    new_zone.update(id=103, valve=3, name="Hedge")
    zones.append(new_zone)

    await _poll(hass, config_entry)
    assert hass.states.get(_eid(hass, "valve", "zone_103_valve")) is not None

    zones.pop()
    await _poll(hass, config_entry)
    assert hass.states.get(_eid(hass, "valve", "zone_103_valve")).state == "unavailable"
    await _poll(hass, config_entry)
    assert _eid(hass, "valve", "zone_103_valve") is None
    assert find_device(hass, f"{SERIAL}_zone_103") is None


async def test_the_skip_switch_follows_the_zones_schedule(
    hass: HomeAssistant, mock_api: dict[str, AsyncMock], config_entry: MockConfigEntry, polled_state: dict[str, Any]
) -> None:
    await _setup(hass, config_entry)
    beds = polled_state["devices"][0]["zones"][1]
    assert _eid(hass, "switch", "zone_102_skip_next_run") is None

    beds["scheduleStatus"] = "scheduled"
    await _poll(hass, config_entry)
    assert hass.states.get(_eid(hass, "switch", "zone_102_skip_next_run")).state == "off"

    beds["scheduleStatus"] = "no_schedule"
    await _poll(hass, config_entry)
    assert _eid(hass, "switch", "zone_102_skip_next_run") is not None
    await _poll(hass, config_entry)
    assert _eid(hass, "switch", "zone_102_skip_next_run") is None

    # And back again: the switch can return after it was removed.
    beds["scheduleStatus"] = "scheduled"
    await _poll(hass, config_entry)
    assert _eid(hass, "switch", "zone_102_skip_next_run") is not None


async def test_the_rain_sensor_follows_the_zones_setting(
    hass: HomeAssistant, mock_api: dict[str, AsyncMock], config_entry: MockConfigEntry, polled_state: dict[str, Any]
) -> None:
    await _setup(hass, config_entry)
    lawn = polled_state["devices"][0]["zones"][0]

    lawn["rainPostponeEnabled"] = True
    await _poll(hass, config_entry)
    assert hass.states.get(_eid(hass, "binary_sensor", "zone_101_rain_postponed")).state == "off"

    lawn["rainPostponeEnabled"] = False
    await _poll(hass, config_entry)
    await _poll(hass, config_entry)
    assert _eid(hass, "binary_sensor", "zone_101_rain_postponed") is None


async def test_a_reading_that_appears_later_gets_its_sensor(
    hass: HomeAssistant, mock_api: dict[str, AsyncMock], config_entry: MockConfigEntry, polled_state: dict[str, Any]
) -> None:
    await _setup(hass, config_entry)
    assert _eid(hass, "sensor", f"device_{SERIAL}_pressure") is None

    polled_state["devices"][0]["sensors"]["pressure"] = 2.1
    await _poll(hass, config_entry)
    assert hass.states.get(_eid(hass, "sensor", f"device_{SERIAL}_pressure")).state == "2.1"

    # A reading that stops reporting keeps its sensor (and history): unknown, not deleted.
    del polled_state["devices"][0]["sensors"]["pressure"]
    await _poll(hass, config_entry)
    assert hass.states.get(_eid(hass, "sensor", f"device_{SERIAL}_pressure")).state == "unknown"


async def test_a_controller_missing_from_one_poll_only_keeps_everything(
    hass: HomeAssistant, mock_api: dict[str, AsyncMock], config_entry: MockConfigEntry, polled_state: dict[str, Any]
) -> None:
    second = _second_controller(polled_state)
    polled_state["devices"].append(second)
    await _setup(hass, config_entry)
    device_id = find_device(hass, OTHER).id
    valve = _eid(hass, "valve", "zone_201_valve")

    # Missing, back, missing again: never two polls in a row, so never removed.
    polled_state["devices"].pop()
    await _poll(hass, config_entry)
    polled_state["devices"].append(second)
    await _poll(hass, config_entry)
    assert hass.states.get(valve).state == "closed"
    polled_state["devices"].pop()
    await _poll(hass, config_entry)

    assert find_device(hass, OTHER).id == device_id
    assert _eid(hass, "valve", "zone_201_valve") == valve
    assert _eid(hass, "switch", "zone_201_skip_next_run") is not None
    # Back again: the same entities, nothing added twice.
    polled_state["devices"].append(second)
    await _poll(hass, config_entry)
    assert hass.states.get(valve).state == "closed"
    assert len(hass.states.async_entity_ids("valve")) == 4


async def test_no_controllers_at_all_after_some_removes_nothing(
    hass: HomeAssistant,
    mock_api: dict[str, AsyncMock],
    config_entry: MockConfigEntry,
    polled_state: dict[str, Any],
    caplog: pytest.LogCaptureFixture,
) -> None:
    await _setup(hass, config_entry)
    controllers = polled_state["devices"][:]
    polled_state["devices"].clear()

    for _ in range(3):
        await _poll(hass, config_entry)

    assert find_device(hass, SERIAL) is not None
    assert find_device(hass, f"{SERIAL}_zone_101") is not None
    assert _eid(hass, "switch", "zone_101_skip_next_run") is not None
    assert hass.states.get(_eid(hass, "valve", "zone_101_valve")).state == "unavailable"
    assert caplog.text.count("reported no controllers") == 1

    # GRAAS reports them again: everything is back as it was.
    polled_state["devices"].extend(controllers)
    await _poll(hass, config_entry)
    assert hass.states.get(_eid(hass, "valve", "zone_101_valve")).state == "closed"
    assert len(hass.states.async_entity_ids("valve")) == 2


async def test_no_controllers_after_a_restart_removes_nothing_either(
    hass: HomeAssistant, mock_api: dict[str, AsyncMock], config_entry: MockConfigEntry, polled_state: dict[str, Any]
) -> None:
    """The first poll after Home Assistant starts is empty: the devices from before stay."""
    await _setup(hass, config_entry)
    polled_state["devices"].clear()
    assert await hass.config_entries.async_reload(config_entry.entry_id)
    await hass.async_block_till_done()

    assert config_entry.runtime_data.suspicious_empty
    for _ in range(3):
        await _poll(hass, config_entry)

    assert find_device(hass, SERIAL) is not None


async def test_a_failed_poll_removes_nothing(
    hass: HomeAssistant, mock_api: dict[str, AsyncMock], config_entry: MockConfigEntry, polled_state: dict[str, Any]
) -> None:
    from custom_components.graas.api import GraasError

    await _setup(hass, config_entry)
    mock_api["get_state"].side_effect = GraasError("down")
    await _poll(hass, config_entry)

    assert _eid(hass, "switch", "zone_101_skip_next_run") is not None
    assert find_device(hass, SERIAL) is not None


async def test_only_devices_graas_no_longer_reports_can_be_deleted(
    hass: HomeAssistant, mock_api: dict[str, AsyncMock], config_entry: MockConfigEntry
) -> None:
    await _setup(hass, config_entry)
    controller = find_device(hass, SERIAL)
    zone = find_device(hass, f"{SERIAL}_zone_101")
    stale = dr.async_get(hass).async_get_or_create(
        config_entry_id=config_entry.entry_id, identifiers={(DOMAIN, "graas-gone")}
    )

    assert await async_remove_config_entry_device(hass, config_entry, controller) is False
    assert await async_remove_config_entry_device(hass, config_entry, zone) is False
    assert await async_remove_config_entry_device(hass, config_entry, stale) is True
