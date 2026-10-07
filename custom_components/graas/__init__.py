"""GRAAS Irrigation: GRAAS controllers in Home Assistant, through the GRAAS cloud."""

from __future__ import annotations

import voluptuous as vol
from homeassistant.const import CONF_API_TOKEN, Platform
from homeassistant.core import HomeAssistant, ServiceCall, ServiceResponse, SupportsResponse
from homeassistant.exceptions import HomeAssistantError, ServiceValidationError
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.typing import ConfigType

from .api import GraasApi, GraasCommandError, GraasError
from .const import CONF_API_URL, DEFAULT_API_URL, DOMAIN, MAX_RUN_LITERS, MAX_RUN_MINUTES
from .coordinator import GraasConfigEntry, GraasCoordinator
from .dashboard import build_dashboard, dashboard_yaml

PLATFORMS: list[Platform] = [
    Platform.BINARY_SENSOR,
    Platform.BUTTON,
    Platform.NUMBER,
    Platform.SENSOR,
    Platform.SWITCH,
    Platform.VALVE,
]

SERVICE_START_ZONE = "start_zone"
SERVICE_DASHBOARD = "dashboard"
ATTR_DURATION_MINUTES = "duration_minutes"
ATTR_LITERS = "liters"

CONFIG_SCHEMA = cv.config_entry_only_config_schema(DOMAIN)

START_ZONE_SCHEMA = vol.All(
    cv.make_entity_service_schema(
        {
            vol.Exclusive(ATTR_DURATION_MINUTES, "amount"): vol.All(
                vol.Coerce(int), vol.Range(min=1, max=MAX_RUN_MINUTES)
            ),
            vol.Exclusive(ATTR_LITERS, "amount"): vol.All(
                vol.Coerce(float), vol.Range(min=0.1, max=MAX_RUN_LITERS)
            ),
        }
    ),
    cv.has_at_least_one_key(ATTR_DURATION_MINUTES, ATTR_LITERS),
)


async def async_setup(hass: HomeAssistant, config: ConfigType) -> bool:
    """Register graas.start_zone (works on GRAAS zone valves)."""

    async def start_zone(call: ServiceCall) -> None:
        registry = er.async_get(hass)
        for entity_id in call.data["entity_id"]:
            entry = registry.async_get(entity_id)
            if (
                entry is None
                or entry.platform != DOMAIN
                or entry.domain != "valve"
                or not entry.unique_id.endswith("_valve")
            ):
                raise ServiceValidationError(f"{entity_id} is not a GRAAS zone valve")
            config_entry = hass.config_entries.async_get_entry(entry.config_entry_id or "")
            if config_entry is None:
                raise ServiceValidationError(f"{entity_id} is not loaded")
            coordinator: GraasCoordinator = config_entry.runtime_data
            zone_id = int(entry.unique_id.removeprefix("zone_").removesuffix("_valve"))
            try:
                await coordinator.api.async_start_zone(
                    zone_id,
                    duration_minutes=call.data.get(ATTR_DURATION_MINUTES),
                    liters=call.data.get(ATTR_LITERS),
                )
            except GraasCommandError as err:
                raise HomeAssistantError(str(err)) from err
            except GraasError as err:
                raise HomeAssistantError(f"GRAAS is unreachable: {err}") from err
            await coordinator.async_request_refresh()

    hass.services.async_register(DOMAIN, SERVICE_START_ZONE, start_zone, schema=START_ZONE_SCHEMA)

    async def dashboard(call: ServiceCall) -> ServiceResponse:
        """A ready-made dashboard for this installation, to paste into a new dashboard."""
        if not hass.config_entries.async_loaded_entries(DOMAIN):
            raise ServiceValidationError("Add the GRAAS integration first")
        config = build_dashboard(hass)
        return {"config": config, "yaml": dashboard_yaml(config)}

    hass.services.async_register(
        DOMAIN, SERVICE_DASHBOARD, dashboard, schema=vol.Schema({}), supports_response=SupportsResponse.ONLY
    )
    return True


async def async_setup_entry(hass: HomeAssistant, entry: GraasConfigEntry) -> bool:
    api = GraasApi(
        async_get_clientsession(hass),
        entry.data.get(CONF_API_URL, DEFAULT_API_URL),
        entry.data[CONF_API_TOKEN],
    )
    coordinator = GraasCoordinator(hass, entry, api)
    await coordinator.async_config_entry_first_refresh()
    entry.runtime_data = coordinator
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True


async def async_unload_entry(hass: HomeAssistant, entry: GraasConfigEntry) -> bool:
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
