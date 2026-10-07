"""Entities created from the GRAAS state, and the commands they send."""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, patch

import pytest
import voluptuous as vol
from homeassistant.config_entries import ConfigEntryState
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
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

    lawn = hass.states.get(_entity_id(hass, "valve", "zone_101_valve"))
    beds = hass.states.get(_entity_id(hass, "valve", "zone_102_valve"))
    assert lawn.state == "closed"
    assert beds.state == "open"
    assert lawn.attributes["device_class"] == "water"
    assert beds.attributes["target_duration_minutes"] == 10.0


async def test_only_reported_sensors_get_entities(
    hass: HomeAssistant, mock_api: dict[str, AsyncMock], config_entry: MockConfigEntry
) -> None:
    await _setup(hass, config_entry)
    registry = er.async_get(hass)

    assert hass.states.get(_entity_id(hass, "sensor", "device_graas-0000000000a1_air_temperature")).state == "11.9"
    assert hass.states.get(_entity_id(hass, "sensor", "zone_101_soil_moisture")).state == "31.0"
    # No pressure reading, no soil sensor on Beds: no entities for them.
    assert registry.async_get_entity_id("sensor", DOMAIN, "device_graas-0000000000a1_pressure") is None
    assert registry.async_get_entity_id("sensor", DOMAIN, "zone_102_soil_moisture") is None
    # Flow rate exists but is off by default (most controllers have no meter).
    flow = registry.async_get(_entity_id(hass, "sensor", "zone_101_flow_rate"))
    assert flow.disabled_by is er.RegistryEntryDisabler.INTEGRATION


async def test_controller_status_and_rain_postponement(
    hass: HomeAssistant, mock_api: dict[str, AsyncMock], config_entry: MockConfigEntry
) -> None:
    await _setup(hass, config_entry)

    assert hass.states.get(_entity_id(hass, "binary_sensor", "device_graas-0000000000a1_online")).state == "on"
    assert hass.states.get(_entity_id(hass, "binary_sensor", "zone_102_rain_postponed")).state == "on"
    # Lawn has rain postponement off: no sensor that could only ever say "off".
    assert er.async_get(hass).async_get_entity_id("binary_sensor", DOMAIN, "zone_101_rain_postponed") is None


async def test_opening_a_valve_starts_a_run_of_its_run_time(
    hass: HomeAssistant, mock_api: dict[str, AsyncMock], config_entry: MockConfigEntry
) -> None:
    await _setup(hass, config_entry)
    valve = _entity_id(hass, "valve", "zone_101_valve")

    # Starts from the zone's own schedule length (20 min) …
    await hass.services.async_call("valve", "open_valve", {"entity_id": valve}, blocking=True)
    mock_api["start"].assert_awaited_with(101, duration_minutes=20)
    # … and a zone without a schedule falls back to the default.
    await hass.services.async_call(
        "valve", "open_valve", {"entity_id": _entity_id(hass, "valve", "zone_102_valve")}, blocking=True
    )
    mock_api["start"].assert_awaited_with(102, duration_minutes=DEFAULT_RUN_MINUTES)

    await hass.services.async_call(
        "number",
        "set_value",
        {"entity_id": _entity_id(hass, "number", "zone_101_run_time"), "value": 25},
        blocking=True,
    )
    await hass.services.async_call("valve", "open_valve", {"entity_id": valve}, blocking=True)
    mock_api["start"].assert_awaited_with(101, duration_minutes=25)


async def test_closing_a_valve_stops_the_zone(
    hass: HomeAssistant, mock_api: dict[str, AsyncMock], config_entry: MockConfigEntry
) -> None:
    await _setup(hass, config_entry)

    await hass.services.async_call(
        "valve", "close_valve", {"entity_id": _entity_id(hass, "valve", "zone_102_valve")}, blocking=True
    )

    mock_api["stop"].assert_awaited_with(102)


async def test_stop_all_button(
    hass: HomeAssistant, mock_api: dict[str, AsyncMock], config_entry: MockConfigEntry
) -> None:
    await _setup(hass, config_entry)

    await hass.services.async_call(
        "button",
        "press",
        {"entity_id": _entity_id(hass, "button", "device_graas-0000000000a1_stop_all")},
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
            "valve", "open_valve", {"entity_id": _entity_id(hass, "valve", "zone_101_valve")}, blocking=True
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
        DOMAIN, "start_zone", {"entity_id": _entity_id(hass, "valve", "zone_101_valve"), **data}, blocking=True
    )

    mock_api["start"].assert_awaited_with(101, **expected)


@pytest.mark.parametrize("data", [{}, {"duration_minutes": 10, "liters": 5}, {"duration_minutes": 121}])
async def test_start_zone_service_only_takes_one_bounded_amount(
    hass: HomeAssistant, mock_api: dict[str, AsyncMock], config_entry: MockConfigEntry, data: dict
) -> None:
    await _setup(hass, config_entry)

    with pytest.raises(vol.Invalid):
        await hass.services.async_call(
            DOMAIN, "start_zone", {"entity_id": _entity_id(hass, "valve", "zone_101_valve"), **data}, blocking=True
        )
    mock_api["start"].assert_not_called()


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

    assert hass.states.get(_entity_id(hass, "valve", "zone_101_valve")).state == "unavailable"
    # What the zone last did and will do next is still known: no "!" on those tiles.
    assert hass.states.get(_entity_id(hass, "sensor", "zone_101_next_run")).state == "2026-10-05T03:00:00+00:00"
    assert hass.states.get(_entity_id(hass, "sensor", "zone_101_last_irrigation")).state != "unavailable"
    assert hass.states.get(_entity_id(hass, "number", "zone_101_run_time")).state == "20"
    # The connectivity sensor itself stays available and says "off".
    assert hass.states.get(_entity_id(hass, "binary_sensor", "device_graas-0000000000a1_online")).state == "off"


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

    lawn = hass.states.get(_entity_id(hass, "valve", "zone_101_valve"))
    assert lawn.attributes["plant_count"] == 3
    assert lawn.attributes["liters_per_plant"] is True


async def test_entities_that_no_longer_apply_are_removed(
    hass: HomeAssistant, mock_api: dict[str, AsyncMock], config_entry: MockConfigEntry
) -> None:
    """A rain sensor from an earlier version (or a zone that turned the feature off) is cleaned up."""
    config_entry.add_to_hass(hass)
    registry = er.async_get(hass)
    stale = registry.async_get_or_create("binary_sensor", DOMAIN, "zone_101_rain_postponed", config_entry=config_entry)

    assert await hass.config_entries.async_setup(config_entry.entry_id)
    await hass.async_block_till_done()

    assert registry.async_get(stale.entity_id) is None
    # The zone that still has it keeps its sensor.
    assert registry.async_get_entity_id("binary_sensor", DOMAIN, "zone_102_rain_postponed") is not None


async def test_each_zone_is_its_own_device_under_the_controller(
    hass: HomeAssistant, mock_api: dict[str, AsyncMock], config_entry: MockConfigEntry, state: dict[str, Any]
) -> None:
    state["devices"][0]["zones"][1]["name"] = "Zone 2"  # a default name, as most zones have
    await _setup(hass, config_entry)
    devices = dr.async_get(hass)
    controller = devices.async_get_device(identifiers={(DOMAIN, "graas-0000000000a1")})
    lawn = devices.async_get_device(identifiers={(DOMAIN, "graas-0000000000a1_zone_101")})
    beds = devices.async_get_device(identifiers={(DOMAIN, "graas-0000000000a1_zone_102")})

    assert lawn is not None and beds is not None
    assert lawn.via_device_id == controller.id
    assert lawn.name == "Lawn"
    # "Zone 2" alone would be ambiguous between controllers: it gets the controller's name.
    assert beds.name == "Garden Zone 2"
    # The zone's entities live on the zone's device, named without repeating the zone.
    registry = er.async_get(hass)
    valve = registry.async_get(_entity_id(hass, "valve", "zone_101_valve"))
    run_time = registry.async_get(_entity_id(hass, "number", "zone_101_run_time"))
    assert valve.device_id == lawn.id and run_time.device_id == lawn.id
    assert hass.states.get(valve.entity_id).attributes["friendly_name"] == "Lawn"
    assert hass.states.get(run_time.entity_id).attributes["friendly_name"] == "Lawn Run time"
    # Controller-level entities stay on the controller.
    stop_all = registry.async_get(_entity_id(hass, "button", "device_graas-0000000000a1_stop_all"))
    assert stop_all.device_id == controller.id


async def test_watering_ends_while_a_zone_runs(
    hass: HomeAssistant, mock_api: dict[str, AsyncMock], config_entry: MockConfigEntry
) -> None:
    await _setup(hass, config_entry)

    # Beds started 11:55 for 10 minutes.
    beds = hass.states.get(_entity_id(hass, "sensor", "zone_102_watering_ends"))
    assert beds.state == "2026-10-04T09:05:00+00:00"
    # Lawn isn't watering: nothing to show.
    assert hass.states.get(_entity_id(hass, "sensor", "zone_101_watering_ends")).state == "unknown"


async def test_next_run(hass: HomeAssistant, mock_api: dict[str, AsyncMock], config_entry: MockConfigEntry) -> None:
    await _setup(hass, config_entry)

    assert hass.states.get(_entity_id(hass, "sensor", "zone_101_next_run")).state == "2026-10-05T03:00:00+00:00"
    assert hass.states.get(_entity_id(hass, "sensor", "zone_102_next_run")).state == "unknown"


async def test_soil_moisture_only_when_the_sensor_reports_it(
    hass: HomeAssistant, mock_api: dict[str, AsyncMock], config_entry: MockConfigEntry, state: dict[str, Any]
) -> None:
    # A temperature-only probe: soil exists, but there is no moisture reading.
    state["devices"][0]["zones"][0]["soil"] = {"temperature": 12.5}
    await _setup(hass, config_entry)
    registry = er.async_get(hass)

    assert registry.async_get_entity_id("sensor", DOMAIN, "zone_101_soil_moisture") is None
    assert hass.states.get(_entity_id(hass, "sensor", "zone_101_zone_soil_temperature")).state == "12.5"


async def test_extra_soil_readings_and_second_pressure(
    hass: HomeAssistant, mock_api: dict[str, AsyncMock], config_entry: MockConfigEntry, state: dict[str, Any]
) -> None:
    state["devices"][0]["sensors"]["pressure2"] = 2.4
    state["devices"][0]["zones"][0]["soil"] = {
        "moisture": 31.0,
        "nitrogen": 40.0,
        "phosphorus": 12.0,
        "potassium": 88.0,
        "salinity": 150.0,
        "tds": 230.0,
    }
    await _setup(hass, config_entry)

    expected = {
        "zone_101_soil_nitrogen": ("40.0", "mg/kg"),
        "zone_101_soil_phosphorus": ("12.0", "mg/kg"),
        "zone_101_soil_potassium": ("88.0", "mg/kg"),
        "zone_101_soil_salinity": ("150.0", "mg/L"),
        "zone_101_soil_tds": ("230.0", "mg/L"),
        "device_graas-0000000000a1_pressure2": ("2.4", "bar"),
    }
    for unique_id, (value, unit) in expected.items():
        entity = hass.states.get(_entity_id(hass, "sensor", unique_id))
        assert entity.state == value, unique_id
        assert entity.attributes["unit_of_measurement"] == unit, unique_id
    pressure2 = hass.states.get(_entity_id(hass, "sensor", "device_graas-0000000000a1_pressure2"))
    assert pressure2.attributes["device_class"] == "pressure"
    assert pressure2.attributes["friendly_name"] == "Garden Water pressure 2"
    # Zones without these readings (and Beds, without soil) get none of them.
    registry = er.async_get(hass)
    assert registry.async_get_entity_id("sensor", DOMAIN, "zone_102_soil_nitrogen") is None


async def test_extra_soil_readings_absent_without_data(
    hass: HomeAssistant, mock_api: dict[str, AsyncMock], config_entry: MockConfigEntry
) -> None:
    await _setup(hass, config_entry)
    registry = er.async_get(hass)

    for key in ("soil_nitrogen", "soil_phosphorus", "soil_potassium", "soil_salinity", "soil_tds"):
        assert registry.async_get_entity_id("sensor", DOMAIN, f"zone_101_{key}") is None
    assert registry.async_get_entity_id("sensor", DOMAIN, "device_graas-0000000000a1_pressure2") is None


async def test_devices_are_registered_without_the_deprecated_via_device(
    hass: HomeAssistant, mock_api: dict[str, AsyncMock], config_entry: MockConfigEntry
) -> None:
    """Zones point at their controller by its device id, not by the deprecated (domain, identifier)."""
    calls: list[dict[str, Any]] = []
    original = dr.DeviceRegistry.async_get_or_create

    def spy(self: dr.DeviceRegistry, **kwargs: Any) -> dr.DeviceEntry:
        calls.append(kwargs)
        return original(self, **kwargs)

    with patch.object(dr.DeviceRegistry, "async_get_or_create", spy):
        await _setup(hass, config_entry)

    assert calls
    assert all("via_device" not in kwargs for kwargs in calls)
    devices = dr.async_get(hass)
    controller = devices.async_get_device(identifiers={(DOMAIN, "graas-0000000000a1")})
    for zone_id in (101, 102):
        zone = devices.async_get_device(identifiers={(DOMAIN, f"graas-0000000000a1_zone_{zone_id}")})
        assert zone.via_device_id == controller.id
