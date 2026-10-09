"""The run limits mirror the GRAAS server's, everywhere a user can enter an amount."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock

import pytest
import voluptuous as vol
import yaml
from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.graas import START_ZONE_SCHEMA
from custom_components.graas.const import DOMAIN, MAX_RUN_LITERS, MAX_RUN_MINUTES

COMPONENT = Path(__file__).parent.parent / "custom_components" / "graas"


def test_limits_mirror_the_server() -> None:
    # Must equal web-server src/Service/Irrigation/IrrigationLimits.php
    # (MAX_RUN_MINUTES = 300, MAX_RUN_LITERS = 1000). Change both together.
    assert MAX_RUN_MINUTES == 300
    assert MAX_RUN_LITERS == 1000


async def test_the_action_form_and_the_run_time_use_the_same_limits(
    hass: HomeAssistant, mock_api: dict[str, AsyncMock], config_entry: MockConfigEntry
) -> None:
    fields = yaml.safe_load((COMPONENT / "services.yaml").read_text())["start_zone"]["fields"]
    assert fields["duration_minutes"]["selector"]["number"]["max"] == MAX_RUN_MINUTES
    assert fields["liters"]["selector"]["number"]["max"] == MAX_RUN_LITERS

    config_entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(config_entry.entry_id)
    await hass.async_block_till_done()
    run_time = er.async_get(hass).async_get_entity_id("number", DOMAIN, "zone_101_run_time")
    assert hass.states.get(run_time).attributes["max"] == MAX_RUN_MINUTES


def test_the_action_schema_accepts_the_limits_and_nothing_above() -> None:
    entity = {"entity_id": "valve.lawn"}

    START_ZONE_SCHEMA({**entity, "duration_minutes": MAX_RUN_MINUTES})
    START_ZONE_SCHEMA({**entity, "liters": MAX_RUN_LITERS})
    for data in ({"duration_minutes": MAX_RUN_MINUTES + 1}, {"liters": MAX_RUN_LITERS + 1}):
        with pytest.raises(vol.Invalid):
            START_ZONE_SCHEMA({**entity, **data})
