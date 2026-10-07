"""A ready-made GRAAS dashboard, built from this installation's own entities.

Returned by the graas.dashboard action as a Lovelace config (and YAML to paste
into a dashboard's raw configuration editor). Only built-in cards are used:
a "sections" view, heading cards, and tile cards with the valve open/close and
numeric-input features — nothing to install.
"""

from __future__ import annotations

from typing import Any

import yaml
from homeassistant.core import HomeAssistant
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er

from .const import DOMAIN
from .coordinator import GraasCoordinator
from .entity import zone_device_name

# Device-level readings shown under each controller, when the controller has them.
DEVICE_SENSOR_KEYS = ("air_temperature", "air_humidity", "soil_temperature", "pressure", "wind_speed")
# Per-zone readings, in the order they appear on the zone's section.
ZONE_SENSOR_KEYS = ("watering_ends", "next_run", "last_irrigation", "soil_moisture", "zone_soil_temperature")

# Short tile titles (the section heading already names the zone or controller).
TITLES: dict[str, dict[str, str]] = {
    "en": {
        "dashboard": "Watering", "watering": "Watering", "stop_all": "Stop all zones", "run_time": "Run time",
        "watering_ends": "Watering ends", "next_run": "Next run", "last_irrigation": "Last watered",
        "soil_moisture": "Soil moisture", "zone_soil_temperature": "Soil temperature",
        "rain_postponed": "Rain postponed", "air_temperature": "Air temperature", "air_humidity": "Air humidity",
        "soil_temperature": "Soil temperature", "pressure": "Water pressure", "wind_speed": "Wind",
        "rain_delay": "Rain delay", "paused_until": "Paused until", "skip_next_run": "Skip next run",
    },
    "lt": {
        "dashboard": "Laistymas", "watering": "Laistymas", "stop_all": "Sustabdyti visas zonas",
        "run_time": "Laistymo trukmė", "watering_ends": "Laistymas baigsis", "next_run": "Kitas laistymas",
        "last_irrigation": "Paskutinis laistymas", "soil_moisture": "Dirvos drėgmė",
        "zone_soil_temperature": "Dirvos temperatūra", "rain_postponed": "Atidėta dėl lietaus",
        "air_temperature": "Oro temperatūra", "air_humidity": "Oro drėgmė", "soil_temperature": "Dirvos temperatūra",
        "pressure": "Vandens slėgis", "wind_speed": "Vėjas",
        "rain_delay": "Lietaus pauzė", "paused_until": "Sustabdyta iki", "skip_next_run": "Praleisti kitą laistymą",
    },
}


def build_dashboard(hass: HomeAssistant) -> dict[str, Any]:
    """Lovelace config: one sections view, a section per controller and per zone."""
    entities = er.async_get(hass)
    devices = dr.async_get(hass)
    titles = TITLES.get((hass.config.language or "en").split("-")[0], TITLES["en"])

    def eid(domain: str, unique_id: str) -> str | None:
        entity_id = entities.async_get_entity_id(domain, DOMAIN, unique_id)
        if entity_id is None:
            return None
        entry = entities.async_get(entity_id)
        return None if entry is None or entry.disabled else entity_id

    def device_name(identifier: str, fallback: str) -> str:
        device = devices.async_get_device(identifiers={(DOMAIN, identifier)})
        if device is None:
            return fallback
        return device.name_by_user or device.name or fallback

    def tile(entity_id: str | None, **extra: Any) -> list[dict[str, Any]]:
        return [] if entity_id is None else [{"type": "tile", "entity": entity_id, **extra}]

    sections: list[dict[str, Any]] = []
    for entry in hass.config_entries.async_loaded_entries(DOMAIN):
        coordinator: GraasCoordinator = entry.runtime_data
        for controller in coordinator.data.devices.values():
            serial = controller["deviceId"]
            controller_name = device_name(serial, controller.get("name") or serial)
            badges = [
                {"type": "entity", "entity": entity_id}
                for entity_id in (
                    eid("binary_sensor", f"device_{serial}_online"),
                    eid("sensor", f"device_{serial}_signal_strength"),
                )
                if entity_id
            ]
            cards: list[dict[str, Any]] = [{"type": "heading", "heading": controller_name, "badges": badges}]
            cards += tile(
                eid("button", f"device_{serial}_stop_all"), name=titles["stop_all"], tap_action={"action": "toggle"}
            )
            delay = eid("number", f"device_{serial}_rain_delay")
            cards += tile(delay, name=titles["rain_delay"], features=[{"type": "numeric-input", "style": "buttons"}])
            # The end time only while a delay is on.
            cards += tile(
                eid("sensor", f"device_{serial}_paused_until"),
                name=titles["paused_until"],
                **({"visibility": [{"condition": "state", "entity": delay, "state_not": "0"}]} if delay else {}),
            )
            for key in DEVICE_SENSOR_KEYS:
                cards += tile(eid("sensor", f"device_{serial}_{key}"), name=titles[key])
            sections.append({"type": "grid", "cards": cards})

            for zone in controller.get("zones", []):
                zone_id = zone["id"]
                name = device_name(
                    f"{serial}_zone_{zone_id}",
                    zone_device_name(controller_name, zone.get("name"), zone.get("valve")),
                )
                zone_cards: list[dict[str, Any]] = [{"type": "heading", "heading": name, "heading_style": "subtitle"}]
                valve = eid("valve", f"zone_{zone_id}_valve")
                zone_cards += tile(valve, name=titles["watering"], features=[{"type": "valve-open-close"}])
                zone_cards += tile(
                    eid("number", f"zone_{zone_id}_run_time"),
                    name=titles["run_time"],
                    features=[{"type": "numeric-input", "style": "buttons"}],
                )
                zone_cards += tile(
                    eid("switch", f"zone_{zone_id}_skip_next_run"),
                    name=titles["skip_next_run"],
                    features=[{"type": "toggle"}],
                )
                for key in ZONE_SENSOR_KEYS:
                    extra: dict[str, Any] = {}
                    if key == "watering_ends" and valve:
                        # Only meaningful during a run; hidden otherwise instead of "Unknown".
                        extra["visibility"] = [{"condition": "state", "entity": valve, "state": "open"}]
                    zone_cards += tile(eid("sensor", f"zone_{zone_id}_{key}"), name=titles[key], **extra)
                zone_cards += tile(
                    eid("binary_sensor", f"zone_{zone_id}_rain_postponed"), name=titles["rain_postponed"]
                )
                sections.append({"type": "grid", "cards": zone_cards})

    return {
        "title": titles["dashboard"],
        "views": [
            {
                "title": titles["dashboard"],
                "path": "graas",
                "icon": "mdi:sprinkler-variant",
                "type": "sections",
                "max_columns": 4,
                "sections": sections,
            }
        ],
    }


def dashboard_yaml(config: dict[str, Any]) -> str:
    """The config as YAML for Home Assistant's raw configuration editor."""
    return yaml.safe_dump(config, sort_keys=False, allow_unicode=True)
