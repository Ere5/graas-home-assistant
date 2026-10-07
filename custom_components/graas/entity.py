"""Shared entity bases: a GRAAS controller is an HA device; zones belong to it."""

from __future__ import annotations

import re
from collections.abc import Awaitable
from typing import Any

from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .api import GraasCommandError, GraasError
from .const import DOMAIN
from .coordinator import GraasCoordinator

DEVICE_MODELS = {
    "lawncare": "Lawncare",
    "lawncare_16": "Lawncare 16",
    "irigator": "Irigator",
    "agrogator": "Agrogator",
}


class GraasEntity(CoordinatorEntity[GraasCoordinator]):
    """An entity of one GRAAS controller."""

    _attr_has_entity_name = True

    def __init__(self, coordinator: GraasCoordinator, device_id: int, key: str) -> None:
        super().__init__(coordinator)
        self._device_id = device_id
        device = self.device_data
        self._attr_unique_id = f"device_{device['deviceId']}_{key}"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, device["deviceId"])},
            name=device.get("name") or device["deviceId"],
            manufacturer="GRAAS Automation",
            model=DEVICE_MODELS.get(str(device.get("type")), str(device.get("type") or "Controller")),
            serial_number=device["deviceId"],
            sw_version=device.get("firmwareVersion"),
        )

    async def _command(self, call: Awaitable[Any]) -> None:
        """Send a command; GRAAS's refusal is shown to the user. Then refresh."""
        try:
            await call
        except GraasCommandError as err:
            raise HomeAssistantError(str(err)) from err
        except GraasError as err:
            raise HomeAssistantError(f"GRAAS is unreachable: {err}") from err
        await self.coordinator.async_request_refresh()

    @property
    def device_data(self) -> dict[str, Any]:
        return self.coordinator.data.devices[self._device_id]

    @property
    def available(self) -> bool:
        return super().available and self._device_id in self.coordinator.data.devices


# Names GRAAS gives zones by default ("Zone 3"); alone they don't say which controller.
_DEFAULT_ZONE_NAME = re.compile(r"^(zone|zona)\s*\d+$", re.IGNORECASE)


def zone_device_name(controller_name: str, zone_name: str | None, valve: Any) -> str:
    """'Lawn' for a named zone, 'Garden Zone 2' for a default-named one."""
    name = (zone_name or "").strip() or f"Zone {valve}"
    return f"{controller_name} {name}" if _DEFAULT_ZONE_NAME.match(name) else name


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
        controller = self.device_data
        zone = self.zone_data
        controller_name = controller.get("name") or controller["deviceId"]
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, f"{controller['deviceId']}_zone_{zone_id}")},
            name=zone_device_name(controller_name, zone.get("name"), zone.get("valve")),
            manufacturer="GRAAS Automation",
            model=f"Irrigation zone (valve {zone.get('valve')})",
            via_device=(DOMAIN, controller["deviceId"]),
        )

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
