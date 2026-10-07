"""Adding a GRAAS account with a token, and replacing a revoked token."""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest
from homeassistant import config_entries
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.graas.api import GraasAuthError, GraasError
from custom_components.graas.const import CONF_API_URL, DEFAULT_API_URL, DOMAIN

from .conftest import TOKEN


async def _start(hass: HomeAssistant) -> str:
    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": config_entries.SOURCE_USER})
    assert result["type"] is FlowResultType.FORM
    return result["flow_id"]


async def test_a_valid_token_creates_the_entry(hass: HomeAssistant, mock_api: dict[str, AsyncMock]) -> None:
    flow_id = await _start(hass)

    result = await hass.config_entries.flow.async_configure(flow_id, {"api_token": f"  {TOKEN}  "})

    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["data"]["api_token"] == TOKEN
    assert result["result"].unique_id == "42"


async def test_the_same_account_cannot_be_added_twice(
    hass: HomeAssistant, mock_api: dict[str, AsyncMock], config_entry: MockConfigEntry
) -> None:
    config_entry.add_to_hass(hass)
    flow_id = await _start(hass)

    result = await hass.config_entries.flow.async_configure(flow_id, {"api_token": TOKEN})

    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "already_configured"


async def test_a_token_without_the_prefix_is_refused_before_calling_graas(
    hass: HomeAssistant, mock_api: dict[str, AsyncMock]
) -> None:
    flow_id = await _start(hass)

    result = await hass.config_entries.flow.async_configure(flow_id, {"api_token": "not-a-token"})

    assert result["errors"] == {"base": "invalid_token_format"}
    mock_api["get_state"].assert_not_called()


@pytest.mark.parametrize(
    ("error", "reason"),
    [(GraasAuthError("no"), "invalid_auth"), (GraasError("down"), "cannot_connect")],
)
async def test_errors_are_shown_and_the_form_stays(
    hass: HomeAssistant, mock_api: dict[str, AsyncMock], error: Exception, reason: str
) -> None:
    mock_api["get_state"].side_effect = error
    flow_id = await _start(hass)

    result = await hass.config_entries.flow.async_configure(flow_id, {"api_token": TOKEN})

    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {"base": reason}


async def test_reauth_replaces_the_token(
    hass: HomeAssistant, mock_api: dict[str, AsyncMock], config_entry: MockConfigEntry
) -> None:
    config_entry.add_to_hass(hass)
    result = await config_entry.start_reauth_flow(hass)
    new_token = "graas_pat_" + "b2" * 24

    result = await hass.config_entries.flow.async_configure(result["flow_id"], {"api_token": new_token})

    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "reauth_successful"
    assert config_entry.data["api_token"] == new_token


async def test_reauth_refuses_another_accounts_token(
    hass: HomeAssistant, mock_api: dict[str, AsyncMock], config_entry: MockConfigEntry, state: dict
) -> None:
    config_entry.add_to_hass(hass)
    result = await config_entry.start_reauth_flow(hass)
    state["account"]["id"] = 99

    result = await hass.config_entries.flow.async_configure(result["flow_id"], {"api_token": TOKEN})

    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "wrong_account"


async def test_the_api_address_defaults_to_graas_cloud(hass: HomeAssistant, mock_api: dict[str, AsyncMock]) -> None:
    flow_id = await _start(hass)

    result = await hass.config_entries.flow.async_configure(flow_id, {"api_token": TOKEN})

    assert result["data"][CONF_API_URL] == DEFAULT_API_URL


async def test_another_server_can_be_set_under_advanced(hass: HomeAssistant, mock_api: dict[str, AsyncMock]) -> None:
    """For development and staging: a collapsed "Advanced" section in the form."""
    flow_id = await _start(hass)

    result = await hass.config_entries.flow.async_configure(
        flow_id, {"api_token": TOKEN, "advanced": {CONF_API_URL: "https://graas.example.com/"}}
    )

    assert result["data"][CONF_API_URL] == "https://graas.example.com"


async def test_the_server_field_sits_in_a_collapsed_section(hass: HomeAssistant) -> None:
    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": config_entries.SOURCE_USER})

    assert "advanced" in result["data_schema"].schema
    assert CONF_API_URL not in result["data_schema"].schema
