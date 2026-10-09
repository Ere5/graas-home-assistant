"""Diagnostics download (Settings → Devices → GRAAS → Download diagnostics)."""

from __future__ import annotations

from dataclasses import asdict
from typing import Any

from homeassistant.components.diagnostics import async_redact_data
from homeassistant.const import CONF_API_TOKEN
from homeassistant.core import HomeAssistant

from .coordinator import GraasConfigEntry

# The token, and what identifies the account, its controllers or its owner.
TO_REDACT = {
    CONF_API_TOKEN,
    "account",
    "account_id",
    "deviceId",
    "serialNumber",
    "serial_number",
    "email",
    "name",
    "title",
    "unique_id",
}


async def async_get_config_entry_diagnostics(hass: HomeAssistant, entry: GraasConfigEntry) -> dict[str, Any]:
    coordinator = entry.runtime_data
    return {
        "entry": async_redact_data(dict(entry.data), TO_REDACT),
        "run_minutes": coordinator.run_minutes,
        "data": async_redact_data(asdict(coordinator.data), TO_REDACT) if coordinator.data else None,
    }
