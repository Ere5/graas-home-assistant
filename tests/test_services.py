"""graas.start_zone and graas.stop_all: targets resolved the Home Assistant way."""

from __future__ import annotations

import copy
from typing import Any
from unittest.mock import AsyncMock

import pytest
from homeassistant.config_entries import ConfigEntryState
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError, ServiceValidationError
from homeassistant.helpers import area_registry as ar
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er
from homeassistant.setup import async_setup_component
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.graas.api import GraasAuthError, GraasCommandError, GraasError, GraasRateLimitError
from custom_components.graas.const import DOMAIN

SERIAL = "graas-0000000000a1"


async def _setup(hass: HomeAssistant, entry: MockConfigEntry) -> None:
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()


def _eid(hass: HomeAssistant, domain: str, unique_id: str) -> str:
    entity_id = er.async_get(hass).async_get_entity_id(domain, DOMAIN, unique_id)
    assert entity_id is not None, unique_id
    return entity_id


def _device_id(hass: HomeAssistant, entry: MockConfigEntry, identifier: str) -> str:
    for device in dr.async_entries_for_config_entry(dr.async_get(hass), entry.entry_id):
        if (DOMAIN, identifier) in device.identifiers:
            return device.id
    raise AssertionError(identifier)


def _add_second_controller(state: dict[str, Any]) -> None:
    other = copy.deepcopy(state["devices"][0])
    other.update(id=8, deviceId="graas-0000000000b2", name="Greenhouse")
    for zone, zone_id in zip(other["zones"], (201, 202), strict=True):
        zone["id"] = zone_id
    state["devices"].append(other)


# --- graas.start_zone ---------------------------------------------------------------------------


async def test_start_zone_by_zone_device(
    hass: HomeAssistant, mock_api: dict[str, AsyncMock], config_entry: MockConfigEntry
) -> None:
    await _setup(hass, config_entry)

    await hass.services.async_call(
        DOMAIN,
        "start_zone",
        {"device_id": _device_id(hass, config_entry, f"{SERIAL}_zone_101"), "duration_minutes": 5},
        blocking=True,
    )

    mock_api["start"].assert_awaited_once_with(101, duration_minutes=5, liters=None)


async def test_start_zone_by_area(
    hass: HomeAssistant, mock_api: dict[str, AsyncMock], config_entry: MockConfigEntry
) -> None:
    await _setup(hass, config_entry)
    area = ar.async_get(hass).async_create("Back yard")
    dr.async_get(hass).async_update_device(_device_id(hass, config_entry, f"{SERIAL}_zone_102"), area_id=area.id)

    await hass.services.async_call(DOMAIN, "start_zone", {"area_id": area.id, "liters": 30}, blocking=True)

    mock_api["start"].assert_awaited_once_with(102, duration_minutes=None, liters=30.0)


async def test_start_zone_on_all_valves(
    hass: HomeAssistant, mock_api: dict[str, AsyncMock], config_entry: MockConfigEntry
) -> None:
    await _setup(hass, config_entry)

    await hass.services.async_call(DOMAIN, "start_zone", {"entity_id": "all", "duration_minutes": 3}, blocking=True)

    started = sorted(call.args[0] for call in mock_api["start"].await_args_list)
    assert started == [101, 102]


async def test_start_zone_ignores_entities_that_are_not_graas_valves(
    hass: HomeAssistant, mock_api: dict[str, AsyncMock], config_entry: MockConfigEntry
) -> None:
    await _setup(hass, config_entry)

    await hass.services.async_call(
        DOMAIN,
        "start_zone",
        {"entity_id": _eid(hass, "sensor", "zone_101_soil_moisture"), "duration_minutes": 5},
        blocking=True,
    )

    mock_api["start"].assert_not_called()


async def test_start_zone_shows_a_refusal(
    hass: HomeAssistant, mock_api: dict[str, AsyncMock], config_entry: MockConfigEntry
) -> None:
    await _setup(hass, config_entry)
    mock_api["start"].side_effect = GraasCommandError("Device is offline", "device_offline")

    with pytest.raises(HomeAssistantError, match="controller is offline"):
        await hass.services.async_call(
            DOMAIN,
            "start_zone",
            {"entity_id": _eid(hass, "valve", "zone_101_valve"), "duration_minutes": 5},
            blocking=True,
        )


# --- graas.stop_all -----------------------------------------------------------------------------


async def test_stop_all_without_a_target_stops_every_controller(
    hass: HomeAssistant, mock_api: dict[str, AsyncMock], config_entry: MockConfigEntry, state: dict[str, Any]
) -> None:
    _add_second_controller(state)
    await _setup(hass, config_entry)

    await hass.services.async_call(DOMAIN, "stop_all", {}, blocking=True)

    assert sorted(call.args[0] for call in mock_api["stop_all"].await_args_list) == [7, 8]


async def test_stop_all_on_one_controller_device(
    hass: HomeAssistant, mock_api: dict[str, AsyncMock], config_entry: MockConfigEntry, state: dict[str, Any]
) -> None:
    _add_second_controller(state)
    await _setup(hass, config_entry)

    await hass.services.async_call(
        DOMAIN, "stop_all", {"device_id": _device_id(hass, config_entry, "graas-0000000000b2")}, blocking=True
    )

    mock_api["stop_all"].assert_awaited_once_with(8)


@pytest.mark.parametrize("target", ["zone_device", "zone_entity", "area"])
async def test_stop_all_through_a_zone_stops_its_controller(
    hass: HomeAssistant,
    mock_api: dict[str, AsyncMock],
    config_entry: MockConfigEntry,
    state: dict[str, Any],
    target: str,
) -> None:
    _add_second_controller(state)
    await _setup(hass, config_entry)
    zone_device = _device_id(hass, config_entry, "graas-0000000000b2_zone_201")
    if target == "zone_device":
        data = {"device_id": zone_device}
    elif target == "zone_entity":
        data = {"entity_id": _eid(hass, "valve", "zone_201_valve")}
    else:
        area = ar.async_get(hass).async_create("Greenhouse")
        dr.async_get(hass).async_update_device(zone_device, area_id=area.id)
        data = {"area_id": area.id}

    await hass.services.async_call(DOMAIN, "stop_all", data, blocking=True)

    mock_api["stop_all"].assert_awaited_once_with(8)


async def test_stop_all_with_a_target_that_is_not_graas(
    hass: HomeAssistant, mock_api: dict[str, AsyncMock], config_entry: MockConfigEntry
) -> None:
    await _setup(hass, config_entry)
    area = ar.async_get(hass).async_create("Kitchen")

    with pytest.raises(ServiceValidationError) as err:
        await hass.services.async_call(DOMAIN, "stop_all", {"area_id": area.id}, blocking=True)

    assert err.value.translation_key == "no_controllers"
    mock_api["stop_all"].assert_not_called()


async def test_stop_all_refuses_a_controller_whose_entry_is_not_loaded(
    hass: HomeAssistant, mock_api: dict[str, AsyncMock], config_entry: MockConfigEntry
) -> None:
    await _setup(hass, config_entry)
    controller = _device_id(hass, config_entry, SERIAL)
    assert await hass.config_entries.async_unload(config_entry.entry_id)
    await hass.async_block_till_done()

    with pytest.raises(ServiceValidationError) as err:
        await hass.services.async_call(DOMAIN, "stop_all", {"device_id": controller}, blocking=True)

    assert err.value.translation_key == "entry_not_loaded"
    mock_api["stop_all"].assert_not_called()


async def test_stop_all_without_any_loaded_entry(hass: HomeAssistant, mock_api: dict[str, AsyncMock]) -> None:
    assert await async_setup_component(hass, DOMAIN, {})

    with pytest.raises(ServiceValidationError) as err:
        await hass.services.async_call(DOMAIN, "stop_all", {}, blocking=True)

    assert err.value.translation_key == "no_controllers"


async def test_stop_all_reports_an_unreachable_server(
    hass: HomeAssistant, mock_api: dict[str, AsyncMock], config_entry: MockConfigEntry
) -> None:
    await _setup(hass, config_entry)
    mock_api["stop_all"].side_effect = GraasError("Cannot reach GRAAS: boom")

    with pytest.raises(HomeAssistantError, match="unreachable"):
        await hass.services.async_call(DOMAIN, "stop_all", {}, blocking=True)


# --- commands and a revoked token ---------------------------------------------------------------


async def test_a_rejected_token_during_a_command_asks_for_a_new_one(
    hass: HomeAssistant, mock_api: dict[str, AsyncMock], config_entry: MockConfigEntry
) -> None:
    await _setup(hass, config_entry)
    mock_api["stop"].side_effect = GraasAuthError("API token was rejected")

    with pytest.raises(HomeAssistantError):
        await hass.services.async_call(
            "valve", "close_valve", {"entity_id": _eid(hass, "valve", "zone_101_valve")}, blocking=True
        )
    await hass.async_block_till_done()

    flows = hass.config_entries.flow.async_progress()
    assert [f["context"]["source"] for f in flows] == ["reauth"]


# --- rate limits and litres per plant -----------------------------------------------------------


async def test_a_rate_limited_poll_backs_off_without_reauth(
    hass: HomeAssistant, mock_api: dict[str, AsyncMock], config_entry: MockConfigEntry
) -> None:
    await _setup(hass, config_entry)
    coordinator = config_entry.runtime_data
    mock_api["get_state"].side_effect = GraasRateLimitError("Too many requests.", "rate_limited", 60)

    await coordinator.async_refresh()
    await hass.async_block_till_done()

    assert coordinator.last_update_success is False
    assert hass.config_entries.flow.async_progress() == []
    assert config_entry.state is ConfigEntryState.LOADED


async def test_a_rate_limited_command_says_too_many_requests(
    hass: HomeAssistant, mock_api: dict[str, AsyncMock], config_entry: MockConfigEntry
) -> None:
    await _setup(hass, config_entry)
    mock_api["stop"].side_effect = GraasRateLimitError("Too many requests.", "rate_limited", 60)

    with pytest.raises(HomeAssistantError, match="(?i)too many requests") as err:
        await hass.services.async_call(
            "valve", "close_valve", {"entity_id": _eid(hass, "valve", "zone_101_valve")}, blocking=True
        )

    assert err.value.translation_key == "rate_limited"
    assert hass.config_entries.flow.async_progress() == []


async def test_a_token_without_read_scope_asks_for_a_new_one(
    hass: HomeAssistant, mock_api: dict[str, AsyncMock], config_entry: MockConfigEntry
) -> None:
    await _setup(hass, config_entry)
    mock_api["get_state"].side_effect = GraasAuthError("This API token cannot read.")

    await config_entry.runtime_data.async_refresh()
    await hass.async_block_till_done()

    assert [f["context"]["source"] for f in hass.config_entries.flow.async_progress()] == ["reauth"]


@pytest.mark.parametrize(("plants", "liters", "allowed"), [(3, 300, True), (3, 400, False), (None, 1000, True)])
async def test_litres_per_plant_are_checked_against_the_limit(
    hass: HomeAssistant,
    mock_api: dict[str, AsyncMock],
    config_entry: MockConfigEntry,
    state: dict[str, Any],
    plants: int | None,
    liters: float,
    allowed: bool,
) -> None:
    """On an Irigator, litres are per plant: the whole run (litres × plants) must stay within 1000 L."""
    state["devices"][0]["capabilities"]["litersPerPlant"] = True
    state["devices"][0]["zones"][0]["plantCount"] = plants
    await _setup(hass, config_entry)
    call = {"entity_id": _eid(hass, "valve", "zone_101_valve"), "liters": liters}

    if allowed:
        await hass.services.async_call(DOMAIN, "start_zone", call, blocking=True)
        mock_api["start"].assert_awaited_once_with(101, duration_minutes=None, liters=float(liters))
    else:
        with pytest.raises(ServiceValidationError) as err:
            await hass.services.async_call(DOMAIN, "start_zone", call, blocking=True)
        assert err.value.translation_key == "too_many_liters"
        mock_api["start"].assert_not_called()


async def test_litres_are_not_multiplied_on_other_controllers(
    hass: HomeAssistant, mock_api: dict[str, AsyncMock], config_entry: MockConfigEntry, state: dict[str, Any]
) -> None:
    state["devices"][0]["zones"][0]["plantCount"] = 10  # ignored: not litres per plant
    await _setup(hass, config_entry)

    await hass.services.async_call(
        DOMAIN, "start_zone", {"entity_id": _eid(hass, "valve", "zone_101_valve"), "liters": 900}, blocking=True
    )

    mock_api["start"].assert_awaited_once()


async def test_a_server_validation_error_is_shown(
    hass: HomeAssistant, mock_api: dict[str, AsyncMock], config_entry: MockConfigEntry
) -> None:
    await _setup(hass, config_entry)
    mock_api["start"].side_effect = GraasCommandError("liters: This value is too high.", "validation_failed")

    with pytest.raises(HomeAssistantError, match="too high"):
        await hass.services.async_call(
            DOMAIN, "start_zone", {"entity_id": _eid(hass, "valve", "zone_101_valve"), "liters": 50}, blocking=True
        )


# --- stop_all keeps going past a failing controller -------------------------------------------


async def test_stop_all_tries_every_controller_and_names_the_ones_that_failed(
    hass: HomeAssistant, mock_api: dict[str, AsyncMock], config_entry: MockConfigEntry, state: dict[str, Any]
) -> None:
    _add_second_controller(state)
    await _setup(hass, config_entry)

    async def stop_all(device_id: int) -> None:
        if device_id == 7:
            raise GraasError("Cannot reach GRAAS: boom")

    mock_api["stop_all"].side_effect = stop_all

    with pytest.raises(HomeAssistantError) as err:
        await hass.services.async_call(DOMAIN, "stop_all", {}, blocking=True)

    # The second controller still got its stop, after the first one failed.
    assert sorted(call.args[0] for call in mock_api["stop_all"].await_args_list) == [7, 8]
    assert err.value.translation_key == "stop_all_failed"
    assert err.value.translation_placeholders["failed"] == "1"
    assert err.value.translation_placeholders["total"] == "2"
    assert "Garden" in str(err.value)
    assert "Greenhouse" not in str(err.value)


async def test_stop_all_on_one_controller_keeps_its_own_error(
    hass: HomeAssistant, mock_api: dict[str, AsyncMock], config_entry: MockConfigEntry
) -> None:
    await _setup(hass, config_entry)
    mock_api["stop_all"].side_effect = GraasRateLimitError("Too many requests.", "rate_limited", 60)

    with pytest.raises(HomeAssistantError) as err:
        await hass.services.async_call(DOMAIN, "stop_all", {}, blocking=True)

    assert err.value.translation_key == "rate_limited"


# --- the same controller through two GRAAS accounts ---------------------------------------------


def _second_account_entry() -> MockConfigEntry:
    return MockConfigEntry(
        domain=DOMAIN,
        title="GRAAS shared",
        unique_id="43",
        data={"api_token": "graas_pat_" + "c3" * 24, "api_url": "https://ss.graasautomation.com"},
    )


async def test_a_controller_two_accounts_see_is_provided_once(
    hass: HomeAssistant,
    mock_api: dict[str, AsyncMock],
    config_entry: MockConfigEntry,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Owner and shared user both add GRAAS: the controller's entities exist once, under the older entry."""
    shared = _second_account_entry()
    await _setup(hass, config_entry)
    await _setup(hass, shared)

    assert shared.state is ConfigEntryState.LOADED
    assert shared.runtime_data.data.devices == {}
    registry = er.async_get(hass)
    valve = registry.async_get(_eid(hass, "valve", "zone_101_valve"))
    assert valve.config_entry_id == config_entry.entry_id
    assert er.async_entries_for_config_entry(registry, shared.entry_id) == []
    assert dr.async_entries_for_config_entry(dr.async_get(hass), shared.entry_id) == []
    # Logged once, not on every poll.
    await shared.runtime_data.async_refresh()
    assert caplog.text.count("is provided by another GRAAS entry") == 1

    # One stop per controller, not one per account.
    await hass.services.async_call(DOMAIN, "stop_all", {}, blocking=True)
    mock_api["stop_all"].assert_awaited_once_with(7)


async def test_the_other_account_takes_over_when_the_first_is_removed(
    hass: HomeAssistant, mock_api: dict[str, AsyncMock], config_entry: MockConfigEntry
) -> None:
    shared = _second_account_entry()
    await _setup(hass, config_entry)
    await _setup(hass, shared)

    assert await hass.config_entries.async_remove(config_entry.entry_id)
    await shared.runtime_data.async_refresh()
    await hass.async_block_till_done()

    valve = er.async_get(hass).async_get(_eid(hass, "valve", "zone_101_valve"))
    assert valve.config_entry_id == shared.entry_id
    assert hass.states.get(valve.entity_id).state == "closed"
