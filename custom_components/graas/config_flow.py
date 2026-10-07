"""Set up GRAAS with a personal API token from the GRAAS app."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

import voluptuous as vol
from homeassistant.config_entries import ConfigFlow, ConfigFlowResult
from homeassistant.const import CONF_API_TOKEN
from homeassistant.data_entry_flow import section
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .api import GraasApi, GraasAuthError, GraasError
from .const import CONF_API_URL, DEFAULT_API_URL, DOMAIN, TOKEN_PREFIX

TOKEN_SCHEMA = vol.Schema({vol.Required(CONF_API_TOKEN): str})
SECTION_ADVANCED = "advanced"
# Another GRAAS server (development, staging) sits in a collapsed section.
USER_SCHEMA = TOKEN_SCHEMA.extend(
    {
        vol.Optional(SECTION_ADVANCED): section(
            vol.Schema({vol.Optional(CONF_API_URL, default=DEFAULT_API_URL): str}),
            {"collapsed": True},
        )
    }
)


class GraasConfigFlow(ConfigFlow, domain=DOMAIN):
    """Ask for the token, check it against GRAAS, one entry per account."""

    VERSION = 1

    async def async_step_user(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        errors: dict[str, str] = {}
        if user_input is not None:
            token = user_input[CONF_API_TOKEN].strip()
            advanced = user_input.get(SECTION_ADVANCED) or {}
            api_url = (advanced.get(CONF_API_URL) or DEFAULT_API_URL).strip().rstrip("/")
            account_id, error = await self._validate(token, api_url)
            if error:
                errors["base"] = error
            else:
                await self.async_set_unique_id(str(account_id))
                self._abort_if_unique_id_configured()
                return self.async_create_entry(
                    title="GRAAS",
                    data={CONF_API_TOKEN: token, CONF_API_URL: api_url},
                )

        return self.async_show_form(step_id="user", data_schema=USER_SCHEMA, errors=errors)

    async def async_step_reauth(self, entry_data: Mapping[str, Any]) -> ConfigFlowResult:
        """The token was revoked or expired in the GRAAS app."""
        return await self.async_step_reauth_confirm()

    async def async_step_reauth_confirm(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        errors: dict[str, str] = {}
        if user_input is not None:
            entry = self._get_reauth_entry()
            token = user_input[CONF_API_TOKEN].strip()
            account_id, error = await self._validate(token, entry.data.get(CONF_API_URL, DEFAULT_API_URL))
            if error:
                errors["base"] = error
            else:
                await self.async_set_unique_id(str(account_id))
                self._abort_if_unique_id_mismatch(reason="wrong_account")
                return self.async_update_reload_and_abort(entry, data_updates={CONF_API_TOKEN: token})
        return self.async_show_form(step_id="reauth_confirm", data_schema=TOKEN_SCHEMA, errors=errors)

    async def _validate(self, token: str, api_url: str) -> tuple[int | None, str | None]:
        if not token.startswith(TOKEN_PREFIX):
            return None, "invalid_token_format"
        api = GraasApi(async_get_clientsession(self.hass), api_url, token)
        try:
            state = await api.async_get_state()
        except GraasAuthError:
            return None, "invalid_auth"
        except GraasError:
            return None, "cannot_connect"
        account_id = (state.get("account") or {}).get("id")
        if account_id is None:
            return None, "cannot_connect"
        return int(account_id), None
