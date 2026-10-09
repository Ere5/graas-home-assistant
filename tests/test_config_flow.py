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


# --- the server address -------------------------------------------------------------------------


@pytest.mark.parametrize(
    "api_url",
    [
        "http://graas.example.com",  # a public server needs https: the token would travel in clear text
        "ftp://graas.example.com",
        "not a url",
        "https://",
        "graas.example.com",
        "https://user:pass@graas.example.com",
    ],
)
async def test_an_unusable_server_address_is_refused_before_calling_graas(
    hass: HomeAssistant, mock_api: dict[str, AsyncMock], api_url: str
) -> None:
    flow_id = await _start(hass)

    result = await hass.config_entries.flow.async_configure(
        flow_id, {"api_token": TOKEN, "advanced": {CONF_API_URL: api_url}}
    )

    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {"base": "invalid_url"}
    mock_api["get_state"].assert_not_called()


@pytest.mark.parametrize(
    "api_url",
    [
        "http://localhost:8080",
        "http://127.0.0.1:8080",
        "http://192.168.1.20",
        "http://host.docker.internal:8080",
        "http://graas-dev.local",
        "http://web:8080",
    ],
)
async def test_plain_http_is_allowed_for_a_local_development_server(
    hass: HomeAssistant, mock_api: dict[str, AsyncMock], api_url: str
) -> None:
    flow_id = await _start(hass)

    result = await hass.config_entries.flow.async_configure(
        flow_id, {"api_token": TOKEN, "advanced": {CONF_API_URL: api_url}}
    )

    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["data"][CONF_API_URL] == api_url


async def test_another_server_is_part_of_the_unique_id(hass: HomeAssistant, mock_api: dict[str, AsyncMock]) -> None:
    """The same account id on a test server is another account, not a duplicate."""
    MockConfigEntry(domain=DOMAIN, unique_id="42", data={"api_token": TOKEN}).add_to_hass(hass)
    flow_id = await _start(hass)

    result = await hass.config_entries.flow.async_configure(
        flow_id, {"api_token": TOKEN, "advanced": {CONF_API_URL: "https://staging.example.com"}}
    )

    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["result"].unique_id == "42@staging.example.com"
    assert result["title"] == "GRAAS (staging.example.com)"


# --- the entry's title ---------------------------------------------------------------------------


async def test_the_title_is_the_accounts_email_when_graas_sends_it(
    hass: HomeAssistant, mock_api: dict[str, AsyncMock], state: dict
) -> None:
    state["account"]["email"] = "demo@example.com"
    flow_id = await _start(hass)

    result = await hass.config_entries.flow.async_configure(flow_id, {"api_token": TOKEN})

    assert result["title"] == "demo@example.com"


async def test_the_title_is_graas_without_account_details(hass: HomeAssistant, mock_api: dict[str, AsyncMock]) -> None:
    flow_id = await _start(hass)

    result = await hass.config_entries.flow.async_configure(flow_id, {"api_token": TOKEN})

    assert result["title"] == "GRAAS"


# --- reauth on an entry from an earlier version ------------------------------------------------------


async def test_reauth_works_for_an_older_entry_on_another_server(
    hass: HomeAssistant, mock_api: dict[str, AsyncMock]
) -> None:
    """Made before the host was part of the unique id: still the same account."""
    entry = MockConfigEntry(
        domain=DOMAIN, unique_id="42", data={"api_token": TOKEN, CONF_API_URL: "https://staging.example.com"}
    )
    entry.add_to_hass(hass)
    result = await entry.start_reauth_flow(hass)
    new_token = "graas_pat_" + "b2" * 24

    result = await hass.config_entries.flow.async_configure(result["flow_id"], {"api_token": new_token})

    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "reauth_successful"
    assert entry.data["api_token"] == new_token


# --- reconfigure -------------------------------------------------------------------------------------


async def test_reconfigure_changes_the_server_and_keeps_the_token(
    hass: HomeAssistant, mock_api: dict[str, AsyncMock], config_entry: MockConfigEntry, state: dict
) -> None:
    state["account"]["email"] = "ona@example.com"
    config_entry.add_to_hass(hass)
    hass.config_entries.async_update_entry(config_entry, title="ona@example.com")
    result = await config_entry.start_reconfigure_flow(hass)
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "reconfigure"

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_API_URL: "https://staging.example.com/"}
    )

    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "reconfigure_successful"
    assert config_entry.data[CONF_API_URL] == "https://staging.example.com"
    assert config_entry.data["api_token"] == TOKEN
    assert config_entry.unique_id == "42@staging.example.com"
    # The title follows the server.
    assert config_entry.title == "ona@example.com (staging.example.com)"


async def test_reconfigure_back_to_graas_cloud_drops_the_host_from_the_title(
    hass: HomeAssistant, mock_api: dict[str, AsyncMock], state: dict
) -> None:
    state["account"]["email"] = "Ona@Example.com"
    entry = MockConfigEntry(
        domain=DOMAIN,
        title="ona@example.com (staging.example.com)",
        unique_id="42@staging.example.com",
        data={"api_token": TOKEN, CONF_API_URL: "https://staging.example.com"},
    )
    entry.add_to_hass(hass)
    result = await entry.start_reconfigure_flow(hass)

    result = await hass.config_entries.flow.async_configure(result["flow_id"], {CONF_API_URL: DEFAULT_API_URL})

    assert result["reason"] == "reconfigure_successful"
    assert entry.unique_id == "42"
    assert entry.title == "Ona@Example.com"


@pytest.mark.parametrize(
    "account",
    [
        # Same number, but another person on the other server.
        {"id": 42, "email": "someone.else@example.com"},
        # The other server does not say whose account it is: it cannot be told apart.
        {"id": 42},
    ],
)
async def test_reconfigure_to_another_server_refuses_an_account_that_only_shares_the_number(
    hass: HomeAssistant, mock_api: dict[str, AsyncMock], state: dict, account: dict
) -> None:
    entry = MockConfigEntry(
        domain=DOMAIN,
        title="ona@example.com",
        unique_id="42",
        data={"api_token": TOKEN, CONF_API_URL: DEFAULT_API_URL},
    )
    entry.add_to_hass(hass)
    result = await entry.start_reconfigure_flow(hass)
    state["account"] = account

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_API_URL: "https://staging.example.com"}
    )

    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "wrong_account"
    assert entry.unique_id == "42"
    assert entry.data[CONF_API_URL] == DEFAULT_API_URL
    assert entry.title == "ona@example.com"


async def test_reconfigure_on_the_same_server_needs_no_account_details(
    hass: HomeAssistant, mock_api: dict[str, AsyncMock], state: dict
) -> None:
    """GRAAS sends no e-mail or name: a new token on the same server is still accepted (same account id)."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        title="GRAAS (staging.example.com)",
        unique_id="42@staging.example.com",
        data={"api_token": TOKEN, CONF_API_URL: "https://staging.example.com"},
    )
    entry.add_to_hass(hass)
    result = await entry.start_reconfigure_flow(hass)
    new_token = "graas_pat_" + "d4" * 24

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_API_URL: "https://staging.example.com/", "api_token": new_token}
    )

    assert result["reason"] == "reconfigure_successful"
    assert entry.data["api_token"] == new_token
    assert entry.title == "GRAAS (staging.example.com)"


async def test_reconfigure_replaces_the_token(
    hass: HomeAssistant, mock_api: dict[str, AsyncMock], config_entry: MockConfigEntry
) -> None:
    config_entry.add_to_hass(hass)
    result = await config_entry.start_reconfigure_flow(hass)
    new_token = "graas_pat_" + "d4" * 24

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_API_URL: DEFAULT_API_URL, "api_token": new_token}
    )

    assert result["reason"] == "reconfigure_successful"
    assert config_entry.data["api_token"] == new_token
    assert config_entry.unique_id == "42"


async def test_reconfigure_refuses_another_account(
    hass: HomeAssistant, mock_api: dict[str, AsyncMock], config_entry: MockConfigEntry, state: dict
) -> None:
    config_entry.add_to_hass(hass)
    result = await config_entry.start_reconfigure_flow(hass)
    state["account"]["id"] = 99

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_API_URL: DEFAULT_API_URL, "api_token": TOKEN}
    )

    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "wrong_account"
    assert config_entry.data["api_token"] == TOKEN


@pytest.mark.parametrize(
    ("data", "error"),
    [
        ({CONF_API_URL: "http://graas.example.com"}, "invalid_url"),
        ({CONF_API_URL: DEFAULT_API_URL, "api_token": "not-a-token"}, "invalid_token_format"),
    ],
)
async def test_reconfigure_errors_keep_the_form(
    hass: HomeAssistant, mock_api: dict[str, AsyncMock], config_entry: MockConfigEntry, data: dict, error: str
) -> None:
    config_entry.add_to_hass(hass)
    result = await config_entry.start_reconfigure_flow(hass)

    result = await hass.config_entries.flow.async_configure(result["flow_id"], data)

    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {"base": error}
