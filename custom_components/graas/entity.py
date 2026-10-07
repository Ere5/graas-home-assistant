"""Shared entity bases: a GRAAS controller is an HA device; zones belong to it."""

from __future__ import annotations

import re
from collections.abc import Awaitable
from typing import Any

from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN
from .coordinator import GraasCoordinator

DEVICE_MODELS = {
    "lawncare": "Lawncare",
    "lawncare_16": "Lawncare 16",
    "irigator": "Irigator",
    "agrogator": "Agrogator",
}


def controller_identifier(controller: dict[str, Any]) -> str:
    return str(controller["deviceId"])


def zone_identifier(controller: dict[str, Any], zone_id: int) -> str:
    return f"{controller['deviceId']}_zone_{zone_id}"


def controller_name(controller: dict[str, Any]) -> str:
    return controller.get("name") or controller["deviceId"]


def controller_device_info(controller: dict[str, Any]) -> DeviceInfo:
    """The controller as a Home Assistant device."""
    return DeviceInfo(
        identifiers={(DOMAIN, controller_identifier(controller))},
        name=controller_name(controller),
        manufacturer="GRAAS Automation",
        model=DEVICE_MODELS.get(str(controller.get("type")), str(controller.get("type") or "Controller")),
        serial_number=controller["deviceId"],
        sw_version=controller.get("firmwareVersion"),
    )


def zone_device_info(controller: dict[str, Any], zone_id: int, zone: dict[str, Any]) -> DeviceInfo:
    """A zone as its own device. Its link to the controller (via_device_id) is set when
    the integration registers the devices, before any entity is added."""
    return DeviceInfo(
        identifiers={(DOMAIN, zone_identifier(controller, zone_id))},
        name=zone_device_name(controller_name(controller), zone.get("name"), zone.get("valve")),
        manufacturer="GRAAS Automation",
        model=f"Irrigation zone (valve {zone.get('valve')})",
    )


class GraasEntity(CoordinatorEntity[GraasCoordinator]):
    """An entity of one GRAAS controller."""

    _attr_has_entity_name = True

    def __init__(self, coordinator: GraasCoordinator, device_id: int, key: str) -> None:
        super().__init__(coordinator)
        self._device_id = device_id
        device = self.device_data
        self._attr_unique_id = f"device_{device['deviceId']}_{key}"
        self._attr_device_info = controller_device_info(device)

    async def _command(self, call: Awaitable[Any]) -> None:
        """Send a command; GRAAS's refusal is shown to the user. Then refresh."""
        await self.coordinator.async_command(call)

    @property
    def device_data(self) -> dict[str, Any]:
        return self.coordinator.data.devices[self._device_id]

    @property
    def available(self) -> bool:
        return super().available and self._device_id in self.coordinator.data.devices


# Names GRAAS gives zones by default ("Zone 3"); alone they don't say which controller.
_DEFAULT_ZONE_NAME = re.compile(r"^(zone|zona)\s*\d+$", re.IGNORECASE)


def zone_device_name(parent_name: str, zone_name: str | None, valve: Any) -> str:
    """'Lawn' for a named zone, 'Garden Zone 2' for a default-named one."""
    name = (zone_name or "").strip() or f"Zone {valve}"
    return f"{parent_name} {name}" if _DEFAULT_ZONE_NAME.match(name) else name


class GraasZoneEntity(GraasEntity):
    """An entity of one zone: each zone is its own device, linked to its controller."""

    # Only the valve needs the controller online. Last watered, next run, run time
    # and holds still mean something while it is offline, so they stay available.
    _needs_online = False

    def __init__(self, coordinator: GraasCoordinator, zone_id: int, key: str) -> None:
        self._zone_id = zone_id
        super().__init__(coordinator, coordinator.data.zone_device[zone_id], key)
        # Keyed by the database zone id: survives renames on either side.
        self._attr_unique_id = f"zone_{zone_id}_{key}"
        self._attr_device_info = zone_device_info(self.device_data, zone_id, self.zone_data)

    @property
    def zone_data(self) -> dict[str, Any]:
        return self.coordinator.data.zones[self._zone_id]

    @property
    def available(self) -> bool:
        return (
            super().available
            and self._zone_id in self.coordinator.data.zones
            and (not self._needs_online or bool(self.device_data.get("online")))
        )
