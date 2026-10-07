"""graas.dashboard: a ready-made dashboard built from this installation's own controllers and zones."""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, patch

import yaml
from homeassistant.core import HomeAssistant
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.graas.const import DOMAIN


async def _dashboard(hass: HomeAssistant, entry: MockConfigEntry) -> dict[str, Any]:
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    return await hass.services.async_call(DOMAIN, "dashboard", {}, blocking=True, return_response=True)


def _eid(hass: HomeAssistant, domain: str, unique_id: str) -> str:
    entity_id = er.async_get(hass).async_get_entity_id(domain, DOMAIN, unique_id)
    assert entity_id is not None
    return entity_id


def _cards(section: dict[str, Any]) -> list[dict[str, Any]]:
    return section["cards"]


async def test_one_sections_view_with_a_section_per_controller_and_zone(
    hass: HomeAssistant, mock_api: dict[str, AsyncMock], config_entry: MockConfigEntry
) -> None:
    response = await _dashboard(hass, config_entry)
    config = response["config"]

    assert len(config["views"]) == 1
    view = config["views"][0]
    assert view["type"] == "sections"
    # Controller first, then its zones in valve order.
    headings = [s["cards"][0]["heading"] for s in view["sections"]]
    assert headings == ["Garden", "Lawn", "Beds"]


async def test_the_controller_section_has_status_and_stop_all(
    hass: HomeAssistant, mock_api: dict[str, AsyncMock], config_entry: MockConfigEntry
) -> None:
    controller = (await _dashboard(hass, config_entry))["config"]["views"][0]["sections"][0]

    heading = controller["cards"][0]
    badge_entities = [b["entity"] for b in heading["badges"]]
    assert _eid(hass, "binary_sensor", "device_graas-0000000000a1_online") in badge_entities
    stop = controller["cards"][1]
    assert stop["entity"] == _eid(hass, "button", "device_graas-0000000000a1_stop_all")
    assert stop["tap_action"] == {"action": "toggle"}


async def test_a_zone_section_has_open_close_run_time_and_its_times(
    hass: HomeAssistant, mock_api: dict[str, AsyncMock], config_entry: MockConfigEntry
) -> None:
    lawn = (await _dashboard(hass, config_entry))["config"]["views"][0]["sections"][1]
    by_entity = {c.get("entity"): c for c in _cards(lawn)}

    valve = by_entity[_eid(hass, "valve", "zone_101_valve")]
    assert valve["features"] == [{"type": "valve-open-close"}]
    run_time = by_entity[_eid(hass, "number", "zone_101_run_time")]
    assert run_time["features"] == [{"type": "numeric-input", "style": "buttons"}]
    for unique_id in (
        "zone_101_next_run",
        "zone_101_watering_ends",
        "zone_101_last_irrigation",
        "zone_101_soil_moisture",
    ):
        assert _eid(hass, "sensor", unique_id) in by_entity
    # Lawn has no rain postponement: no card for it; disabled flow rate: no card either.
    assert all("rain_postponed" not in (c.get("entity") or "") for c in _cards(lawn))
    assert all("flow_rate" not in (c.get("entity") or "") for c in _cards(lawn))


async def test_the_answer_includes_paste_ready_yaml(
    hass: HomeAssistant, mock_api: dict[str, AsyncMock], config_entry: MockConfigEntry
) -> None:
    response = await _dashboard(hass, config_entry)

    assert yaml.safe_load(response["yaml"]) == response["config"]


async def test_tiles_have_short_titles_and_the_end_time_shows_only_while_watering(
    hass: HomeAssistant, mock_api: dict[str, AsyncMock], config_entry: MockConfigEntry
) -> None:
    sections = (await _dashboard(hass, config_entry))["config"]["views"][0]["sections"]
    lawn = {c.get("entity"): c for c in sections[1]["cards"]}
    valve = _eid(hass, "valve", "zone_101_valve")

    assert lawn[_eid(hass, "number", "zone_101_run_time")]["name"] == "Run time"
    assert lawn[_eid(hass, "sensor", "zone_101_next_run")]["name"] == "Next run"
    ends = lawn[_eid(hass, "sensor", "zone_101_watering_ends")]
    assert ends["name"] == "Watering ends"
    assert ends["visibility"] == [{"condition": "state", "entity": valve, "state": "open"}]
    assert sections[0]["cards"][1]["name"] == "Stop all zones"


async def test_titles_follow_the_home_assistant_language(
    hass: HomeAssistant, mock_api: dict[str, AsyncMock], config_entry: MockConfigEntry
) -> None:
    hass.config.language = "lt"
    sections = (await _dashboard(hass, config_entry))["config"]["views"][0]["sections"]
    lawn = {c.get("entity"): c for c in sections[1]["cards"]}

    assert lawn[_eid(hass, "number", "zone_101_run_time")]["name"] == "Laistymo trukmė"


async def test_the_dashboard_avoids_the_deprecated_device_lookup(
    hass: HomeAssistant, mock_api: dict[str, AsyncMock], config_entry: MockConfigEntry
) -> None:
    """async_get_device(identifiers=…) is deprecated (identifiers aren't unique across entries)."""
    with patch.object(dr.DeviceRegistry, "async_get_device", side_effect=AssertionError("deprecated")):
        response = await _dashboard(hass, config_entry)

    headings = [s["cards"][0]["heading"] for s in response["config"]["views"][0]["sections"]]
    assert headings == ["Garden", "Lawn", "Beds"]


async def test_the_dashboard_uses_names_given_in_home_assistant(
    hass: HomeAssistant, mock_api: dict[str, AsyncMock], config_entry: MockConfigEntry
) -> None:
    await _dashboard(hass, config_entry)
    devices = dr.async_get(hass)
    lawn = next(
        d
        for d in dr.async_entries_for_config_entry(devices, config_entry.entry_id)
        if (DOMAIN, "graas-0000000000a1_zone_101") in d.identifiers
    )
    devices.async_update_device(lawn.id, name_by_user="Front lawn")

    response = await hass.services.async_call(DOMAIN, "dashboard", {}, blocking=True, return_response=True)

    headings = [s["cards"][0]["heading"] for s in response["config"]["views"][0]["sections"]]
    assert headings == ["Garden", "Front lawn", "Beds"]
