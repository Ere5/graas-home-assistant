"""Entities created from the GRAAS state, and the commands they send."""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock

import pytest
import voluptuous as vol
from homeassistant.config_entries import ConfigEntryState
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError, ServiceValidationError
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.graas.api import GraasAuthError, GraasCommandError
from custom_components.graas.const import DEFAULT_RUN_MINUTES, DOMAIN


async def _setup(hass: HomeAssistant, entry: MockConfigEntry) -> None:
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()


def _entity_id(hass: HomeAssistant, domain: str, unique_id: str) -> str:
    entity_id = er.async_get(hass).async_get_entity_id(domain, DOMAIN, unique_id)
    assert entity_id is not None, unique_id
    return entity_id


async def test_zones_become_water_valves_showing_whether_they_water(
    hass: HomeAssistant, mock_api: dict[str, AsyncMock], config_entry: MockConfigEntry
) -> None:
    await _setup(hass, config_entry)

    lawn = hass.states.get(_entity_id(hass, "valve", "zone_1685_valve"))
    beds = hass.states.get(_entity_id(hass, "valve", "zone_1686_valve"))
    assert lawn.state == "closed"
    assert beds.state == "open"
    assert lawn.attributes["device_class"] == "water"
    assert beds.attributes["target_duration_minutes"] == 10.0


async def test_only_reported_sensors_get_entities(
    hass: HomeAssistant, mock_api: dict[str, AsyncMock], config_entry: MockConfigEntry
) -> None:
    await _setup(hass, config_entry)
    registry = er.async_get(hass)

    assert hass.states.get(_entity_id(hass, "sensor", "device_graas-209ba960ad38_air_temperature")).state == "11.9"
    assert hass.states.get(_entity_id(hass, "sensor", "zone_1685_soil_moisture")).state == "31.0"
    # No pressure reading, no soil sensor on Beds: no entities for them.
    assert registry.async_get_entity_id("sensor", DOMAIN, "device_graas-209ba960ad38_pressure") is None
    assert registry.async_get_entity_id("sensor", DOMAIN, "zone_1686_soil_moisture") is None
    # Flow rate exists but is off by default (most controllers have no meter).
    flow = registry.async_get(_entity_id(hass, "sensor", "zone_1685_flow_rate"))
    assert flow.disabled_by is er.RegistryEntryDisabler.INTEGRATION


async def test_controller_status_and_rain_postponement(
    hass: HomeAssistant, mock_api: dict[str, AsyncMock], config_entry: MockConfigEntry
) -> None:
    await _setup(hass, config_entry)

    assert hass.states.get(_entity_id(hass, "binary_sensor", "device_graas-209ba960ad38_online")).state == "on"
    assert hass.states.get(_entity_id(hass, "binary_sensor", "zone_1686_rain_postponed")).state == "on"
    # Lawn has rain postponement off: no sensor that could only ever say "off".
    assert er.async_get(hass).async_get_entity_id("binary_sensor", DOMAIN, "zone_1685_rain_postponed") is None


async def test_opening_a_valve_starts_a_run_of_its_run_time(
    hass: HomeAssistant, mock_api: dict[str, AsyncMock], config_entry: MockConfigEntry
) -> None:
    await _setup(hass, config_entry)
    valve = _entity_id(hass, "valve", "zone_1685_valve")

    # Starts from the zone's own schedule length (20 min) …
    await hass.services.async_call("valve", "open_valve", {"entity_id": valve}, blocking=True)
    mock_api["start"].assert_awaited_with(1685, duration_minutes=20)
    # … and a zone without a schedule falls back to the default.
    await hass.services.async_call(
        "valve", "open_valve", {"entity_id": _entity_id(hass, "valve", "zone_1686_valve")}, blocking=True
    )
    mock_api["start"].assert_awaited_with(1686, duration_minutes=DEFAULT_RUN_MINUTES)

    await hass.services.async_call(
        "number",
        "set_value",
        {"entity_id": _entity_id(hass, "number", "zone_1685_run_time"), "value": 25},
        blocking=True,
    )
    await hass.services.async_call("valve", "open_valve", {"entity_id": valve}, blocking=True)
    mock_api["start"].assert_awaited_with(1685, duration_minutes=25)


async def test_closing_a_valve_stops_the_zone(
    hass: HomeAssistant, mock_api: dict[str, AsyncMock], config_entry: MockConfigEntry
) -> None:
    await _setup(hass, config_entry)

    await hass.services.async_call(
        "valve", "close_valve", {"entity_id": _entity_id(hass, "valve", "zone_1686_valve")}, blocking=True
    )

    mock_api["stop"].assert_awaited_with(1686)


async def test_stop_all_button(
    hass: HomeAssistant, mock_api: dict[str, AsyncMock], config_entry: MockConfigEntry
) -> None:
    await _setup(hass, config_entry)

    await hass.services.async_call(
        "button",
        "press",
        {"entity_id": _entity_id(hass, "button", "device_graas-209ba960ad38_stop_all")},
        blocking=True,
    )

    mock_api["stop_all"].assert_awaited_with(7)


async def test_a_refused_command_is_shown_to_the_user(
    hass: HomeAssistant, mock_api: dict[str, AsyncMock], config_entry: MockConfigEntry
) -> None:
    await _setup(hass, config_entry)
    mock_api["start"].side_effect = GraasCommandError("Device is offline", "device_offline")

    with pytest.raises(HomeAssistantError, match="Device is offline"):
        await hass.services.async_call(
            "valve", "open_valve", {"entity_id": _entity_id(hass, "valve", "zone_1685_valve")}, blocking=True
        )


@pytest.mark.parametrize(
    ("data", "expected"),
    [
        ({"duration_minutes": 10}, {"duration_minutes": 10, "liters": None}),
        ({"liters": 40}, {"duration_minutes": None, "liters": 40.0}),
    ],
)
async def test_start_zone_service(
    hass: HomeAssistant, mock_api: dict[str, AsyncMock], config_entry: MockConfigEntry, data: dict, expected: dict
) -> None:
    await _setup(hass, config_entry)

    await hass.services.async_call(
        DOMAIN, "start_zone", {"entity_id": _entity_id(hass, "valve", "zone_1685_valve"), **data}, blocking=True
    )

    mock_api["start"].assert_awaited_with(1685, **expected)


@pytest.mark.parametrize("data", [{}, {"duration_minutes": 10, "liters": 5}, {"duration_minutes": 121}])
async def test_start_zone_service_only_takes_one_bounded_amount(
    hass: HomeAssistant, mock_api: dict[str, AsyncMock], config_entry: MockConfigEntry, data: dict
) -> None:
    await _setup(hass, config_entry)

    with pytest.raises(vol.Invalid):
        await hass.services.async_call(
            DOMAIN, "start_zone", {"entity_id": _entity_id(hass, "valve", "zone_1685_valve"), **data}, blocking=True
        )
    mock_api["start"].assert_not_called()


async def test_start_zone_refuses_entities_that_are_not_graas_valves(
    hass: HomeAssistant, mock_api: dict[str, AsyncMock], config_entry: MockConfigEntry
) -> None:
    await _setup(hass, config_entry)

    with pytest.raises(ServiceValidationError):
        await hass.services.async_call(
            DOMAIN, "start_zone",
            {"entity_id": _entity_id(hass, "sensor", "zone_1685_soil_moisture"), "duration_minutes": 5},
            blocking=True,
        )


async def test_a_revoked_token_asks_for_a_new_one(
    hass: HomeAssistant, mock_api: dict[str, AsyncMock], config_entry: MockConfigEntry
) -> None:
    mock_api["get_state"].side_effect = GraasAuthError("revoked")
    config_entry.add_to_hass(hass)

    assert not await hass.config_entries.async_setup(config_entry.entry_id)
    await hass.async_block_till_done()

    assert config_entry.state is ConfigEntryState.SETUP_ERROR
    flows = hass.config_entries.flow.async_progress()
    assert [f["context"]["source"] for f in flows] == ["reauth"]


async def test_an_offline_controller_makes_only_its_valves_unavailable(
    hass: HomeAssistant, mock_api: dict[str, AsyncMock], config_entry: MockConfigEntry, state: dict[str, Any]
) -> None:
    state["devices"][0]["online"] = False
    await _setup(hass, config_entry)

    assert hass.states.get(_entity_id(hass, "valve", "zone_1685_valve")).state == "unavailable"
    # What the zone last did and will do next is still known: no "!" on those tiles.
    assert hass.states.get(_entity_id(hass, "sensor", "zone_1685_next_run")).state == "2026-10-05T03:00:00+00:00"
    assert hass.states.get(_entity_id(hass, "sensor", "zone_1685_last_irrigation")).state != "unavailable"
    assert hass.states.get(_entity_id(hass, "number", "zone_1685_run_time")).state == "20"
    # The connectivity sensor itself stays available and says "off".
    assert hass.states.get(_entity_id(hass, "binary_sensor", "device_graas-209ba960ad38_online")).state == "off"


async def test_diagnostics_never_contain_the_token(
    hass: HomeAssistant, mock_api: dict[str, AsyncMock], config_entry: MockConfigEntry
) -> None:
    from custom_components.graas.diagnostics import async_get_config_entry_diagnostics

    await _setup(hass, config_entry)
    diag = await async_get_config_entry_diagnostics(hass, config_entry)

    assert config_entry.data["api_token"] not in str(diag)


async def test_irigator_zones_show_their_plant_count(
    hass: HomeAssistant, mock_api: dict[str, AsyncMock], config_entry: MockConfigEntry, state: dict[str, Any]
) -> None:
    # On an Irigator, a litres amount is per plant: the valve says how many plants.
    state["devices"][0]["capabilities"]["litersPerPlant"] = True
    state["devices"][0]["zones"][0]["plantCount"] = 3
    await _setup(hass, config_entry)

    lawn = hass.states.get(_entity_id(hass, "valve", "zone_1685_valve"))
    assert lawn.attributes["plant_count"] == 3
    assert lawn.attributes["liters_per_plant"] is True


async def test_entities_that_no_longer_apply_are_removed(
    hass: HomeAssistant, mock_api: dict[str, AsyncMock], config_entry: MockConfigEntry
) -> None:
    """A rain sensor from an earlier version (or a zone that turned the feature off) is cleaned up."""
    config_entry.add_to_hass(hass)
    registry = er.async_get(hass)
    stale = registry.async_get_or_create(
        "binary_sensor", DOMAIN, "zone_1685_rain_postponed", config_entry=config_entry
    )

    assert await hass.config_entries.async_setup(config_entry.entry_id)
    await hass.async_block_till_done()

    assert registry.async_get(stale.entity_id) is None
    # The zone that still has it keeps its sensor.
    assert registry.async_get_entity_id("binary_sensor", DOMAIN, "zone_1686_rain_postponed") is not None



async def test_each_zone_is_its_own_device_under_the_controller(
    hass: HomeAssistant, mock_api: dict[str, AsyncMock], config_entry: MockConfigEntry, state: dict[str, Any]
) -> None:
    state["devices"][0]["zones"][1]["name"] = "Zone 2"  # a default name, as most zones have
    await _setup(hass, config_entry)
    devices = dr.async_get(hass)
    controller = devices.async_get_device(identifiers={(DOMAIN, "graas-209ba960ad38")})
    lawn = devices.async_get_device(identifiers={(DOMAIN, "graas-209ba960ad38_zone_1685")})
    beds = devices.async_get_device(identifiers={(DOMAIN, "graas-209ba960ad38_zone_1686")})

    assert lawn is not None and beds is not None
    assert lawn.via_device_id == controller.id
    assert lawn.name == "Lawn"
    # "Zone 2" alone would be ambiguous between controllers: it gets the controller's name.
    assert beds.name == "Garden Zone 2"
    # The zone's entities live on the zone's device, named without repeating the zone.
    registry = er.async_get(hass)
    valve = registry.async_get(_entity_id(hass, "valve", "zone_1685_valve"))
    run_time = registry.async_get(_entity_id(hass, "number", "zone_1685_run_time"))
    assert valve.device_id == lawn.id and run_time.device_id == lawn.id
    assert hass.states.get(valve.entity_id).attributes["friendly_name"] == "Lawn"
    assert hass.states.get(run_time.entity_id).attributes["friendly_name"] == "Lawn Run time"
    # Controller-level entities stay on the controller.
    stop_all = registry.async_get(_entity_id(hass, "button", "device_graas-209ba960ad38_stop_all"))
    assert stop_all.device_id == controller.id


async def test_watering_ends_while_a_zone_runs(
    hass: HomeAssistant, mock_api: dict[str, AsyncMock], config_entry: MockConfigEntry
) -> None:
    await _setup(hass, config_entry)

    # Beds started 11:55 for 10 minutes.
    beds = hass.states.get(_entity_id(hass, "sensor", "zone_1686_watering_ends"))
    assert beds.state == "2026-10-04T09:05:00+00:00"
    # Lawn isn't watering: nothing to show.
    assert hass.states.get(_entity_id(hass, "sensor", "zone_1685_watering_ends")).state == "unknown"


async def test_next_run(hass: HomeAssistant, mock_api: dict[str, AsyncMock], config_entry: MockConfigEntry) -> None:
    await _setup(hass, config_entry)

    assert hass.states.get(_entity_id(hass, "sensor", "zone_1685_next_run")).state == "2026-10-05T03:00:00+00:00"
    assert hass.states.get(_entity_id(hass, "sensor", "zone_1686_next_run")).state == "unknown"
