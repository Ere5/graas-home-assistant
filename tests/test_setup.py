"""Entry setup, unload and failures; entity details; diagnostics; translated refusals."""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock

import pytest
from homeassistant.config_entries import ConfigEntryState
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant, State
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import entity_registry as er
from pytest_homeassistant_custom_component.common import MockConfigEntry, mock_restore_cache_with_extra_data

from custom_components.graas import binary_sensor, sensor
from custom_components.graas.api import GraasCommandError, GraasError
from custom_components.graas.const import CONF_API_URL, DEFAULT_API_URL, DOMAIN, MAX_RUN_MINUTES

from .conftest import TOKEN

SERIAL = "graas-0000000000a1"


async def _setup(hass: HomeAssistant, entry: MockConfigEntry) -> None:
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()


def _eid(hass: HomeAssistant, domain: str, unique_id: str) -> str:
    entity_id = er.async_get(hass).async_get_entity_id(domain, DOMAIN, unique_id)
    assert entity_id is not None, unique_id
    return entity_id


# --- setup, failures, unload --------------------------------------------------------------------


async def test_graas_unreachable_at_startup_retries_later(
    hass: HomeAssistant, mock_api: dict[str, AsyncMock], config_entry: MockConfigEntry
) -> None:
    mock_api["get_state"].side_effect = GraasError("Cannot reach GRAAS: boom")
    config_entry.add_to_hass(hass)

    assert not await hass.config_entries.async_setup(config_entry.entry_id)
    await hass.async_block_till_done()

    assert config_entry.state is ConfigEntryState.SETUP_RETRY
    assert hass.config_entries.flow.async_progress() == []


async def test_entities_are_unavailable_while_graas_cannot_be_polled(
    hass: HomeAssistant, mock_api: dict[str, AsyncMock], config_entry: MockConfigEntry
) -> None:
    await _setup(hass, config_entry)
    mock_api["get_state"].side_effect = GraasError("Cannot reach GRAAS: boom")

    await config_entry.runtime_data.async_refresh()
    await hass.async_block_till_done()

    for domain, unique_id in (
        ("valve", "zone_101_valve"),
        ("sensor", "zone_101_soil_moisture"),
        ("binary_sensor", f"device_{SERIAL}_online"),
        ("number", f"device_{SERIAL}_rain_delay"),
    ):
        assert hass.states.get(_eid(hass, domain, unique_id)).state == "unavailable", unique_id

    # Back as soon as a poll works again.
    mock_api["get_state"].side_effect = None
    await config_entry.runtime_data.async_refresh()
    await hass.async_block_till_done()
    assert hass.states.get(_eid(hass, "valve", "zone_101_valve")).state == "closed"


async def test_unloading_the_entry(
    hass: HomeAssistant, mock_api: dict[str, AsyncMock], config_entry: MockConfigEntry
) -> None:
    await _setup(hass, config_entry)
    coordinator = config_entry.runtime_data

    assert await hass.config_entries.async_unload(config_entry.entry_id)
    await hass.async_block_till_done()

    assert config_entry.state is ConfigEntryState.NOT_LOADED
    assert hass.states.get(_eid(hass, "valve", "zone_101_valve")).state == "unavailable"
    # No listeners left: the coordinator stops polling.
    assert not coordinator._listeners


async def test_an_older_entry_for_another_server_gets_the_host_in_its_unique_id(
    hass: HomeAssistant, mock_api: dict[str, AsyncMock]
) -> None:
    entry = MockConfigEntry(
        domain=DOMAIN, unique_id="42", data={"api_token": TOKEN, CONF_API_URL: "https://staging.example.com"}
    )
    await _setup(hass, entry)

    assert entry.unique_id == "42@staging.example.com"


async def test_an_entry_for_graas_cloud_keeps_its_unique_id(
    hass: HomeAssistant, mock_api: dict[str, AsyncMock], config_entry: MockConfigEntry
) -> None:
    await _setup(hass, config_entry)

    assert config_entry.data[CONF_API_URL] == DEFAULT_API_URL
    assert config_entry.unique_id == "42"


# --- run time ------------------------------------------------------------------------------------


@pytest.mark.parametrize(("stored", "expected"), [(999, MAX_RUN_MINUTES), (0, 1), (45, 45)])
async def test_a_restored_run_time_stays_within_the_limits(
    hass: HomeAssistant,
    mock_api: dict[str, AsyncMock],
    config_entry: MockConfigEntry,
    stored: int,
    expected: int,
) -> None:
    mock_restore_cache_with_extra_data(
        hass,
        (
            (
                State("number.lawn_run_time", str(stored)),
                {
                    "native_value": stored,
                    "native_unit_of_measurement": "min",
                    "native_min_value": 1,
                    "native_max_value": 120,
                    "native_step": 1,
                },
            ),
        ),
    )
    await _setup(hass, config_entry)
    run_time = _eid(hass, "number", "zone_101_run_time")
    assert run_time == "number.lawn_run_time"

    assert hass.states.get(run_time).state == str(expected)
    await hass.services.async_call(
        "valve", "open_valve", {"entity_id": _eid(hass, "valve", "zone_101_valve")}, blocking=True
    )
    mock_api["start"].assert_awaited_with(101, duration_minutes=expected)


async def test_a_disabled_run_time_still_uses_the_zones_schedule_length(
    hass: HomeAssistant, mock_api: dict[str, AsyncMock], config_entry: MockConfigEntry
) -> None:
    config_entry.add_to_hass(hass)
    er.async_get(hass).async_get_or_create(
        "number",
        DOMAIN,
        "zone_101_run_time",
        config_entry=config_entry,
        disabled_by=er.RegistryEntryDisabler.USER,
    )
    assert await hass.config_entries.async_setup(config_entry.entry_id)
    await hass.async_block_till_done()

    await hass.services.async_call(
        "valve", "open_valve", {"entity_id": _eid(hass, "valve", "zone_101_valve")}, blocking=True
    )

    # Lawn's schedule is 20 minutes; without the fix it fell back to the 15-minute default.
    mock_api["start"].assert_awaited_with(101, duration_minutes=20)


# --- entity details --------------------------------------------------------------------------------


async def test_soil_ec_is_a_conductivity_sensor(
    hass: HomeAssistant, mock_api: dict[str, AsyncMock], config_entry: MockConfigEntry, state: dict[str, Any]
) -> None:
    state["devices"][0]["zones"][0]["soil"] = {"moisture": 31.0, "ec": 850.0}
    await _setup(hass, config_entry)

    ec = hass.states.get(_eid(hass, "sensor", "zone_101_soil_ec"))
    assert ec.state == "850.0"
    assert ec.attributes["device_class"] == "conductivity"
    assert ec.attributes["unit_of_measurement"] == "μS/cm"


async def test_connectivity_is_a_diagnostic(
    hass: HomeAssistant, mock_api: dict[str, AsyncMock], config_entry: MockConfigEntry
) -> None:
    await _setup(hass, config_entry)

    online = er.async_get(hass).async_get(_eid(hass, "binary_sensor", f"device_{SERIAL}_online"))
    assert online.entity_category is EntityCategory.DIAGNOSTIC


def test_read_only_platforms_do_not_limit_parallel_updates() -> None:
    assert sensor.PARALLEL_UPDATES == 0
    assert binary_sensor.PARALLEL_UPDATES == 0


# --- diagnostics -----------------------------------------------------------------------------------


async def test_diagnostics_hide_what_identifies_the_account_and_its_controllers(
    hass: HomeAssistant, mock_api: dict[str, AsyncMock], config_entry: MockConfigEntry
) -> None:
    from custom_components.graas.diagnostics import async_get_config_entry_diagnostics

    await _setup(hass, config_entry)
    diag = await async_get_config_entry_diagnostics(hass, config_entry)
    text = str(diag)

    for secret in (TOKEN, SERIAL, "Garden", "Lawn"):
        assert secret not in text, secret
    assert diag["data"]["account_id"] == "**REDACTED**"
    # What helps debugging stays.
    assert diag["data"]["devices"][7]["online"] is True
    assert diag["data"]["zones"][101]["scheduleDurationMinutes"] == 20


# --- GRAAS's refusals, translated ------------------------------------------------------------------


@pytest.mark.parametrize(
    ("code", "key", "text"),
    [
        ("api_token_read_only", "api_token_read_only", "can only read"),
        ("device_offline", "device_offline", "controller is offline"),
        ("zone_already_running", "zone_already_running", "already watering"),
        ("litres_need_flow_meter", "litres_need_flow_meter", "no flow meter"),
        ("zone_not_found", "not_found", "no longer has"),
        ("validation_failed", "validation_failed", "liters: too high"),
        ("rate_limited", "rate_limited", "Too many requests"),
        (None, "command_refused", "liters: too high"),
    ],
)
async def test_graas_refusals_are_translated_by_code(
    hass: HomeAssistant,
    mock_api: dict[str, AsyncMock],
    config_entry: MockConfigEntry,
    code: str | None,
    key: str,
    text: str,
) -> None:
    await _setup(hass, config_entry)
    mock_api["start"].side_effect = GraasCommandError("liters: too high", code)

    with pytest.raises(HomeAssistantError, match=text) as err:
        await hass.services.async_call(
            DOMAIN,
            "start_zone",
            {"entity_id": _eid(hass, "valve", "zone_101_valve"), "duration_minutes": 5},
            blocking=True,
        )

    assert err.value.translation_key == key
