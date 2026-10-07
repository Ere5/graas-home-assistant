"""GRAAS Irrigation: GRAAS controllers in Home Assistant, through the GRAAS cloud."""

from __future__ import annotations

import voluptuous as vol
from homeassistant.config_entries import ConfigEntryState
from homeassistant.const import (
    ATTR_AREA_ID,
    ATTR_DEVICE_ID,
    ATTR_ENTITY_ID,
    ATTR_FLOOR_ID,
    ATTR_LABEL_ID,
    CONF_API_TOKEN,
    ENTITY_MATCH_ALL,
    Platform,
)
from homeassistant.core import HomeAssistant, ServiceCall, ServiceResponse, SupportsResponse
from homeassistant.exceptions import ServiceValidationError
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers import service
from homeassistant.helpers import target as target_helpers
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.typing import ConfigType

from .api import GraasApi
from .const import CONF_API_URL, DEFAULT_API_URL, DOMAIN, MAX_RUN_LITERS, MAX_RUN_MINUTES
from .coordinator import GraasConfigEntry, GraasCoordinator
from .dashboard import build_dashboard, dashboard_yaml
from .entity import controller_device_info, controller_identifier, zone_device_info, zone_identifier

PLATFORMS: list[Platform] = [
    Platform.BINARY_SENSOR,
    Platform.BUTTON,
    Platform.NUMBER,
    Platform.SENSOR,
    Platform.SWITCH,
    Platform.VALVE,
]

SERVICE_START_ZONE = "start_zone"
SERVICE_STOP_ALL = "stop_all"
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
            vol.Exclusive(ATTR_LITERS, "amount"): vol.All(vol.Coerce(float), vol.Range(min=0.1, max=MAX_RUN_LITERS)),
        }
    ),
    cv.has_at_least_one_key(ATTR_DURATION_MINUTES, ATTR_LITERS),
)

# Target optional: none means every controller of every GRAAS account.
STOP_ALL_SCHEMA = vol.Schema(cv.ENTITY_SERVICE_FIELDS)

_TARGET_KEYS = (ATTR_ENTITY_ID, ATTR_DEVICE_ID, ATTR_AREA_ID, ATTR_FLOOR_ID, ATTR_LABEL_ID)
# Newer Home Assistant versions renamed TargetSelectorData to TargetSelection.
_TargetSelection = getattr(target_helpers, "TargetSelection", None) or target_helpers.TargetSelectorData


async def async_setup(hass: HomeAssistant, config: ConfigType) -> bool:
    """Register the integration's actions (once, for every account)."""
    # Home Assistant resolves the target (entities, devices, areas, labels) to GRAAS zone valves.
    service.async_register_platform_entity_service(
        hass,
        DOMAIN,
        SERVICE_START_ZONE,
        entity_domain=Platform.VALVE,
        func="async_start_zone",
        schema=START_ZONE_SCHEMA,
    )

    async def stop_all(call: ServiceCall) -> None:
        """Stop every zone on the targeted controllers (or on all of them)."""
        targets = _stop_all_targets(hass, call)
        if not targets:
            raise ServiceValidationError(translation_domain=DOMAIN, translation_key="no_controllers")
        for coordinator, device_id in targets:
            await coordinator.async_command(coordinator.api.async_stop_all(device_id))

    hass.services.async_register(DOMAIN, SERVICE_STOP_ALL, stop_all, schema=STOP_ALL_SCHEMA)

    async def dashboard(call: ServiceCall) -> ServiceResponse:
        """A ready-made dashboard for this installation, to paste into a new dashboard."""
        if not hass.config_entries.async_loaded_entries(DOMAIN):
            raise ServiceValidationError(translation_domain=DOMAIN, translation_key="no_entries")
        config = build_dashboard(hass)
        return {"config": config, "yaml": dashboard_yaml(config)}

    hass.services.async_register(
        DOMAIN, SERVICE_DASHBOARD, dashboard, schema=vol.Schema({}), supports_response=SupportsResponse.ONLY
    )
    return True


def _stop_all_targets(hass: HomeAssistant, call: ServiceCall) -> list[tuple[GraasCoordinator, int]]:
    """(coordinator, GRAAS device id) of each controller the call targets.

    A zone (its device or any of its entities, directly or through an area or a
    label) stands for its controller. Without a target, or with entity_id: all,
    every controller of every loaded GRAAS entry.
    """
    entity_ids = call.data.get(ATTR_ENTITY_ID)
    has_target = any(call.data.get(key) not in (None, []) for key in _TARGET_KEYS)
    if entity_ids == ENTITY_MATCH_ALL or not has_target:
        return [
            (entry.runtime_data, device_id)
            for entry in hass.config_entries.async_loaded_entries(DOMAIN)
            for device_id in entry.runtime_data.data.devices
        ]

    selection = _TargetSelection(call.data)
    selected = target_helpers.async_extract_referenced_entity_ids(hass, selection)
    entities = er.async_get(hass)
    devices = dr.async_get(hass)
    device_ids = set(selected.referenced_devices)
    for entity_id in selected.referenced | selected.indirectly_referenced:
        entry = entities.async_get(entity_id)
        if entry is not None and entry.platform == DOMAIN and entry.device_id:
            device_ids.add(entry.device_id)

    targets: list[tuple[GraasCoordinator, int]] = []
    for device_id in device_ids:
        device = devices.async_get(device_id)
        if device is None or not any(domain == DOMAIN for domain, _ in device.identifiers):
            continue
        # A zone device stands for its controller.
        if device.via_device_id and (parent := devices.async_get(device.via_device_id)) is not None:
            device = parent
        for config_entry_id in device.config_entries:
            config_entry = hass.config_entries.async_get_entry(config_entry_id)
            if config_entry is None or config_entry.domain != DOMAIN:
                continue
            if config_entry.state is not ConfigEntryState.LOADED:
                raise ServiceValidationError(
                    translation_domain=DOMAIN,
                    translation_key="entry_not_loaded",
                    translation_placeholders={"entry": config_entry.title},
                )
            coordinator: GraasCoordinator = config_entry.runtime_data
            for graas_id, controller in coordinator.data.devices.items():
                matches = (DOMAIN, controller_identifier(controller)) in device.identifiers
                if matches and (coordinator, graas_id) not in targets:
                    targets.append((coordinator, graas_id))
    return targets


async def async_setup_entry(hass: HomeAssistant, entry: GraasConfigEntry) -> bool:
    api = GraasApi(
        async_get_clientsession(hass),
        entry.data.get(CONF_API_URL, DEFAULT_API_URL),
        entry.data[CONF_API_TOKEN],
    )
    coordinator = GraasCoordinator(hass, entry, api)
    await coordinator.async_config_entry_first_refresh()
    entry.runtime_data = coordinator
    _async_register_devices(hass, entry, coordinator)
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True


def _async_register_devices(hass: HomeAssistant, entry: GraasConfigEntry, coordinator: GraasCoordinator) -> None:
    """Controllers first, then their zones linked to them by device id.

    Done before the platforms so entities find their devices in place and the
    deprecated via_device (a (domain, identifier) pair) is never needed.
    """
    devices = dr.async_get(hass)
    for controller in coordinator.data.devices.values():
        parent = devices.async_get_or_create(config_entry_id=entry.entry_id, **controller_device_info(controller))
        coordinator.device_entry_ids[controller_identifier(controller)] = parent.id
        for zone in controller.get("zones", []):
            zone_id = int(zone["id"])
            child = devices.async_get_or_create(
                config_entry_id=entry.entry_id, **zone_device_info(controller, zone_id, zone)
            )
            if child.via_device_id != parent.id:
                child = devices.async_update_device(child.id, via_device_id=parent.id) or child
            coordinator.device_entry_ids[zone_identifier(controller, zone_id)] = child.id


async def async_unload_entry(hass: HomeAssistant, entry: GraasConfigEntry) -> bool:
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
