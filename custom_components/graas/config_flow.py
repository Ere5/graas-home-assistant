"""Set up GRAAS with a personal API token from the GRAAS app."""

from __future__ import annotations

import ipaddress
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

import voluptuous as vol
from homeassistant.config_entries import ConfigFlow, ConfigFlowResult
from homeassistant.const import CONF_API_TOKEN
from homeassistant.data_entry_flow import section
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from yarl import URL

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

# Host names that only resolve on the local network: plain http is allowed there
# (a GRAAS server on a developer's machine); everywhere else the token needs https.
_LOCAL_SUFFIXES = (".local", ".localhost", ".internal", ".lan", ".home.arpa")


def normalize_api_url(value: str | None) -> str | None:
    """The server address without a trailing slash, or None when it is not a usable URL.

    https is required, except for a local server (localhost, a private address,
    a single-label or .local/.internal name), where http is allowed for development.
    """
    text = (value or DEFAULT_API_URL).strip().rstrip("/")
    try:
        url = URL(text)
    except ValueError:
        return None
    if url.scheme not in ("http", "https") or not url.host or url.user or url.query_string or url.fragment:
        return None
    if url.scheme == "http" and not _is_local_host(url.host):
        return None
    return text


def _is_local_host(host: str) -> bool:
    host = host.lower().rstrip(".")
    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        return host == "localhost" or "." not in host or host.endswith(_LOCAL_SUFFIXES)
    return address.is_private or address.is_loopback or address.is_link_local


def entry_unique_id(account_id: int, api_url: str) -> str:
    """The account id on GRAAS's own server (as since the first version); account@host elsewhere."""
    if api_url.rstrip("/") == DEFAULT_API_URL:
        return str(account_id)
    return f"{account_id}@{URL(api_url).host}"


@dataclass(frozen=True)
class _Account:
    id: int
    title: str
    # The account's e-mail or name; None when GRAAS sends neither.
    identity: str | None = None


class GraasConfigFlow(ConfigFlow, domain=DOMAIN):
    """Ask for the token, check it against GRAAS, one entry per account (and server)."""

    VERSION = 1

    async def async_step_user(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        errors: dict[str, str] = {}
        if user_input is not None:
            token = user_input[CONF_API_TOKEN].strip()
            advanced = user_input.get(SECTION_ADVANCED) or {}
            api_url = normalize_api_url(advanced.get(CONF_API_URL))
            if api_url is None:
                errors["base"] = "invalid_url"
            else:
                account, error = await self._validate(token, api_url)
                if error:
                    errors["base"] = error
                elif account is not None:
                    await self.async_set_unique_id(entry_unique_id(account.id, api_url))
                    self._abort_if_unique_id_configured()
                    return self.async_create_entry(
                        title=_title(account, api_url),
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
            api_url = entry.data.get(CONF_API_URL, DEFAULT_API_URL)
            account, error = await self._validate(token, api_url)
            if error:
                errors["base"] = error
            elif account is not None:
                # Compared by account: entries made before the server was part of the id have none.
                if _account_part(entry_unique_id(account.id, api_url)) != _account_part(entry.unique_id):
                    return self.async_abort(reason="wrong_account")
                return self.async_update_reload_and_abort(entry, data_updates={CONF_API_TOKEN: token})
        return self.async_show_form(step_id="reauth_confirm", data_schema=TOKEN_SCHEMA, errors=errors)

    async def async_step_reconfigure(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        """Change the server address or the token of an existing entry (same account)."""
        entry = self._get_reconfigure_entry()
        current_url = entry.data.get(CONF_API_URL, DEFAULT_API_URL)
        errors: dict[str, str] = {}
        if user_input is not None:
            token = (user_input.get(CONF_API_TOKEN) or "").strip() or entry.data[CONF_API_TOKEN]
            api_url = normalize_api_url(user_input.get(CONF_API_URL))
            if api_url is None:
                errors["base"] = "invalid_url"
            else:
                account, error = await self._validate(token, api_url)
                if error:
                    errors["base"] = error
                elif account is not None:
                    unique_id = entry_unique_id(account.id, api_url)
                    if _account_part(unique_id) != _account_part(entry.unique_id):
                        return self.async_abort(reason="wrong_account")
                    # Account ids are per server: on another server the same number can be
                    # someone else, so the e-mail (or name) must match the entry's too.
                    if not _same_server(current_url, api_url) and not _same_identity(account, entry.title, current_url):
                        return self.async_abort(reason="wrong_account")
                    if unique_id != entry.unique_id and any(
                        other.unique_id == unique_id
                        for other in self._async_current_entries(include_ignore=False)
                        if other.entry_id != entry.entry_id
                    ):
                        return self.async_abort(reason="already_configured")
                    return self.async_update_reload_and_abort(
                        entry,
                        unique_id=unique_id,
                        title=_title(account, api_url),
                        data_updates={CONF_API_TOKEN: token, CONF_API_URL: api_url},
                    )

        schema = vol.Schema(
            {
                vol.Required(CONF_API_URL, default=current_url): str,
                # Empty keeps the current token.
                vol.Optional(CONF_API_TOKEN): str,
            }
        )
        return self.async_show_form(step_id="reconfigure", data_schema=schema, errors=errors)

    async def _validate(self, token: str, api_url: str) -> tuple[_Account | None, str | None]:
        if not token.startswith(TOKEN_PREFIX):
            return None, "invalid_token_format"
        api = GraasApi(async_get_clientsession(self.hass), api_url, token)
        try:
            state = await api.async_get_state()
        except GraasAuthError:
            return None, "invalid_auth"
        except GraasError:
            return None, "cannot_connect"
        account = state.get("account") or {}
        account_id = account.get("id")
        if account_id is None:
            return None, "cannot_connect"
        # The account's e-mail or name when GRAAS sends one, so two accounts are told apart.
        identity = next(
            (str(account[key]).strip() for key in ("email", "name") if str(account.get(key) or "").strip()), None
        )
        return _Account(int(account_id), identity or "GRAAS", identity), None


def _title(account: _Account, api_url: str) -> str:
    if api_url == DEFAULT_API_URL:
        return account.title
    return f"{account.title} ({URL(api_url).host})"


def _account_part(unique_id: str | None) -> str:
    return (unique_id or "").split("@", 1)[0]


def _same_server(old_url: str, new_url: str) -> bool:
    old, new = URL(old_url.rstrip("/")), URL(new_url.rstrip("/"))
    return (old.host or "").lower() == (new.host or "").lower() and old.port == new.port


def _same_identity(account: _Account, entry_title: str, entry_url: str) -> bool:
    """Whether the account's e-mail or name is the one in the entry's title (as _title made it)."""
    if account.identity is None:
        return False
    stored = entry_title.strip().removesuffix(f" ({URL(entry_url).host})")
    return stored.casefold() == account.identity.casefold()
