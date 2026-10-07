"""Shared fixtures: a fake GRAAS account answered without any network."""

from __future__ import annotations

import copy
from collections.abc import Generator
from typing import Any
from unittest.mock import AsyncMock, patch

import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.graas.const import CONF_API_URL, DEFAULT_API_URL, DOMAIN

TOKEN = "graas_pat_" + "a1" * 24

STATE: dict[str, Any] = {
    "account": {"id": 42},
    "generatedAt": "2026-10-04T12:00:00+03:00",
    "devices": [
        {
            "id": 7,
            "deviceId": "graas-209ba960ad38",
            "name": "Garden",
            "type": "lawncare",
            "isOwner": True,
            "online": True,
            "status": "online",
            "lastSeen": "2026-10-04T11:59:30+03:00",
            "firmwareVersion": "1.6.19",
            "signalStrength": -61,
            "connectionType": "wifi",
            "failedSensors": [],
            "capabilities": {"rainPostpone": True, "litersPerPlant": False},
            "wateringPausedUntil": None,
            "sensors": {"airTemperature": 11.9, "airHumidity": 86.0},
            "zones": [
                {
                    "id": 1685, "valve": 1, "name": "Lawn", "irrigating": False,
                    "runningSince": None, "targetDurationMinutes": None, "targetLiters": None,
                    "elapsedSeconds": None, "flowRate": 0.0,
                    "lastIrrigation": "2026-10-03T06:00:00+03:00", "rainPostponed": False,
                    "rainPostponeEnabled": False, "plantCount": None, "scheduleDurationMinutes": 20,
                    "nextRun": "2026-10-05T06:00:00+03:00", "scheduleStatus": "scheduled",
                    "skipNextRun": False, "skipUntil": None,
                    "soil": {"moisture": 31.0, "temperature": 12.5},
                },
                {
                    "id": 1686, "valve": 2, "name": "Beds", "irrigating": True,
                    "runningSince": "2026-10-04T11:55:00+03:00", "targetDurationMinutes": 10.0,
                    "targetLiters": None, "elapsedSeconds": 300.0, "flowRate": None,
                    "lastIrrigation": None, "rainPostponed": True, "soil": None,
                    "rainPostponeEnabled": True, "plantCount": None, "scheduleDurationMinutes": None,
                    "nextRun": None, "scheduleStatus": "no_schedule",
                    "skipNextRun": False, "skipUntil": None,
                },
            ],
        }
    ],
}


@pytest.fixture(autouse=True)
def auto_enable_custom_integrations(enable_custom_integrations: None) -> None:
    """Load custom_components/ in every test."""


@pytest.fixture
def state() -> dict[str, Any]:
    return copy.deepcopy(STATE)


@pytest.fixture
def mock_api(state: dict[str, Any]) -> Generator[dict[str, AsyncMock]]:
    """Patch every GraasApi call; tests inspect the mocks."""
    with (
        patch("custom_components.graas.api.GraasApi.async_get_state", AsyncMock(return_value=state)) as get_state,
        patch("custom_components.graas.api.GraasApi.async_start_zone", AsyncMock(return_value={"taskId": 1})) as start,
        patch("custom_components.graas.api.GraasApi.async_stop_zone", AsyncMock(return_value=None)) as stop,
        patch("custom_components.graas.api.GraasApi.async_stop_all", AsyncMock(return_value=None)) as stop_all,
        patch("custom_components.graas.api.GraasApi.async_skip_next_run", AsyncMock(return_value=None)) as skip,
        patch("custom_components.graas.api.GraasApi.async_cancel_skip", AsyncMock(return_value=None)) as unskip,
        patch("custom_components.graas.api.GraasApi.async_set_rain_delay", AsyncMock(return_value=None)) as delay,
        patch("custom_components.graas.api.GraasApi.async_end_rain_delay", AsyncMock(return_value=None)) as undelay,
    ):
        yield {
            "get_state": get_state, "start": start, "stop": stop, "stop_all": stop_all,
            "skip": skip, "unskip": unskip, "delay": delay, "undelay": undelay,
        }


@pytest.fixture
def config_entry() -> MockConfigEntry:
    return MockConfigEntry(
        domain=DOMAIN,
        title="GRAAS",
        unique_id="42",
        data={"api_token": TOKEN, CONF_API_URL: DEFAULT_API_URL},
    )
