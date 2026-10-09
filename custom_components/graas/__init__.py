"""GRAAS Irrigation: GRAAS controllers in Home Assistant, through the GRAAS cloud."""

from __future__ import annotations

from collections.abc import Iterable

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
from homeassistant.core import HomeAssistant, ServiceCall, ServiceResponse, SupportsResponse, callback
from homeassistant.exceptions import HomeAssistantError, ServiceValidationError
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers import service
from homeassistant.helpers import target as target_helpers
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.typing import ConfigType

from .api import GraasApi
from .config_flow import entry_unique_id
from .const import (
    CONF_API_URL,
    DEFAULT_API_URL,
    DOMAIN,
    MAX_RUN_LITERS,
    MAX_RUN_MINUTES,
    MISSING_POLLS_BEFORE_REMOVAL,
)
from .coordinator import GraasConfigEntry, GraasCoordinator
from .dashboard import build_dashboard, dashboard_yaml
from .entity import (
    controller_device_info,
    controller_identifier,
    controller_name,
    zone_device_info,
    zone_identifier,
)

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
        # Every controller gets its stop, even when an earlier one fails.
        failures: list[tuple[str, HomeAssistantError]] = []
        for coordinator, device_id in targets:
            try:
                await coordinator.async_command(coordinator.api.async_stop_all(device_id))
            except HomeAssistantError as err:
                controller = coordinator.data.devices.get(device_id)
                failures.append((controller_name(controller) if controller else str(device_id), err))
        if len(targets) == 1 and failures:
            raise failures[0][1]
        if failures:
            raise HomeAssistantError(
                translation_domain=DOMAIN,
                translation_key="stop_all_failed",
                translation_placeholders={
                    "failed": str(len(failures)),
                    "total": str(len(targets)),
                    "controllers": "; ".join(f"{name}: {err}" for name, err in failures),
                },
            )

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
        return _unique_controllers(
            (entry.runtime_data, device_id)
            for entry in hass.config_entries.async_loaded_entries(DOMAIN)
            for device_id in entry.runtime_data.data.devices
        )

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
        found = False
        not_loaded: str | None = None
        for config_entry_id in _device_entry_ids(device):
            config_entry = hass.config_entries.async_get_entry(config_entry_id)
            if config_entry is None or config_entry.domain != DOMAIN:
                continue
            if config_entry.state is not ConfigEntryState.LOADED:
                not_loaded = config_entry.title
                continue
            coordinator: GraasCoordinator = config_entry.runtime_data
            for graas_id, controller in coordinator.data.devices.items():
                if (DOMAIN, controller_identifier(controller)) in device.identifiers:
                    found = True
                    targets.append((coordinator, graas_id))
        if not found and not_loaded is not None:
            raise ServiceValidationError(
                translation_domain=DOMAIN,
                translation_key="entry_not_loaded",
                translation_placeholders={"entry": not_loaded},
            )
    return _unique_controllers(targets)


def _unique_controllers(
    targets: Iterable[tuple[GraasCoordinator, int]],
) -> list[tuple[GraasCoordinator, int]]:
    """Each controller once, even when two GRAAS accounts both see it."""
    seen: set[str] = set()
    unique: list[tuple[GraasCoordinator, int]] = []
    for coordinator, device_id in targets:
        serial = controller_identifier(coordinator.data.devices[device_id])
        if serial not in seen:
            seen.add(serial)
            unique.append((coordinator, device_id))
    return unique


async def async_setup_entry(hass: HomeAssistant, entry: GraasConfigEntry) -> bool:
    _async_add_host_to_unique_id(hass, entry)
    api = GraasApi(
        async_get_clientsession(hass),
        entry.data.get(CONF_API_URL, DEFAULT_API_URL),
        entry.data[CONF_API_TOKEN],
    )
    coordinator = GraasCoordinator(hass, entry, api)
    # Set before the first poll: another GRAAS entry polling at the same time
    # then sees which controllers this one already provides.
    entry.runtime_data = coordinator
    # Controllers from before a restart: an empty first poll does not remove them.
    coordinator.has_registered_controllers = any(
        domain == DOMAIN and "_zone_" not in identifier
        for device in dr.async_entries_for_config_entry(dr.async_get(hass), entry.entry_id)
        for domain, identifier in device.identifiers
    )
    await coordinator.async_config_entry_first_refresh()
    _async_register_devices(hass, entry, coordinator)

    @callback
    def _async_update_devices() -> None:
        # Before the platforms' listeners (added later): new zones find their device in place.
        if coordinator.last_update_success:
            _async_register_devices(hass, entry, coordinator)

    entry.async_on_unload(coordinator.async_add_listener(_async_update_devices))
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True


@callback
def _async_add_host_to_unique_id(hass: HomeAssistant, entry: GraasConfigEntry) -> None:
    """Entries for another server, made before its host was part of the id, get it now.

    Entries for GRAAS's own server keep the bare account id.
    """
    api_url = entry.data.get(CONF_API_URL, DEFAULT_API_URL)
    if not entry.unique_id or "@" in entry.unique_id or api_url.rstrip("/") == DEFAULT_API_URL:
        return
    if not entry.unique_id.isdigit():
        return
    unique_id = entry_unique_id(int(entry.unique_id), api_url)
    if hass.config_entries.async_entry_for_domain_unique_id(DOMAIN, unique_id) is None:
        hass.config_entries.async_update_entry(entry, unique_id=unique_id)


def _device_entry_ids(device: dr.DeviceEntry) -> set[str]:
    """The config entries a device belongs to.

    Home Assistant 2026.10 gives a device a single config_entry_id (config_entries is
    deprecated there); older versions only have the config_entries set.
    """
    entry_id = getattr(device, "config_entry_id", None)
    if isinstance(entry_id, str):
        return {entry_id}
    return set(device.config_entries)


def _current_identifiers(coordinator: GraasCoordinator) -> set[str]:
    """GRAAS device identifiers of every controller and zone in the last poll."""
    identifiers: set[str] = set()
    for controller in coordinator.data.devices.values():
        identifiers.add(controller_identifier(controller))
        identifiers.update(zone_identifier(controller, int(zone["id"])) for zone in controller.get("zones", []))
    return identifiers


def _serial_of(identifier: str) -> str:
    """The controller serial of a controller or zone device identifier."""
    return identifier.partition("_zone_")[0]


def _async_remove_gone_devices(
    hass: HomeAssistant, entry: GraasConfigEntry, coordinator: GraasCoordinator, current: set[str]
) -> None:
    """Remove this entry's devices for controllers and zones GRAAS no longer reports.

    Only after MISSING_POLLS_BEFORE_REMOVAL successful polls in a row without them,
    and never while GRAAS reports no controller at all after reporting some. A
    controller left to another GRAAS entry goes at once: that entry needs its ids.
    """
    devices = dr.async_get(hass)
    count = coordinator.counted_poll != coordinator.poll_seq
    coordinator.counted_poll = coordinator.poll_seq
    kept: set[str] = set(current)
    for device in dr.async_entries_for_config_entry(devices, entry.entry_id):
        graas_ids = {identifier for domain, identifier in device.identifiers if domain == DOMAIN}
        if not graas_ids:
            continue
        if graas_ids & current:
            for identifier in graas_ids:
                coordinator.missing_polls.pop(identifier, None)
            continue
        key = min(graas_ids)
        if not any(_serial_of(identifier) in coordinator.yielded for identifier in graas_ids):
            if coordinator.suspicious_empty:
                kept |= graas_ids
                continue
            if count:
                coordinator.missing_polls[key] = coordinator.missing_polls.get(key, 0) + 1
            if coordinator.missing_polls.get(key, 0) < MISSING_POLLS_BEFORE_REMOVAL:
                kept |= graas_ids
                continue
        coordinator.missing_polls.pop(key, None)
        if _device_entry_ids(device) == {entry.entry_id}:
            devices.async_remove_device(device.id)
        else:
            # Before Home Assistant 2026.10 a device could also belong to another entry: keep it for that one.
            devices.async_update_device(device.id, remove_config_entry_id=entry.entry_id)
    coordinator.kept_serials = {identifier for identifier in kept if "_zone_" not in identifier}
    coordinator.kept_zone_ids = {
        int(zone_id) for identifier in kept if (zone_id := identifier.partition("_zone_")[2]).isdigit()
    }
    coordinator.has_registered_controllers = bool(coordinator.kept_serials)


def _async_register_devices(hass: HomeAssistant, entry: GraasConfigEntry, coordinator: GraasCoordinator) -> None:
    """Drop the devices gone for good; then controllers, and their zones linked to them by device id.

    Done before the platforms so entities find their devices in place and the
    deprecated via_device (a (domain, identifier) pair) is never needed.
    """
    devices = dr.async_get(hass)
    current = _current_identifiers(coordinator)
    _async_remove_gone_devices(hass, entry, coordinator, current)
    coordinator.device_entry_ids = {k: v for k, v in coordinator.device_entry_ids.items() if k in current}
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


async def async_remove_config_entry_device(
    hass: HomeAssistant, entry: GraasConfigEntry, device: dr.DeviceEntry
) -> bool:
    """Let the user delete a device GRAAS no longer reports; the ones it does would come back."""
    coordinator = getattr(entry, "runtime_data", None)
    if not isinstance(coordinator, GraasCoordinator) or coordinator.data is None:
        return True
    current = _current_identifiers(coordinator)
    return not any(domain == DOMAIN and identifier in current for domain, identifier in device.identifiers)
