"""Controller and zone sensors. Only readings the hardware reports get an entity."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorEntityDescription,
    SensorStateClass,
)
from homeassistant.const import (
    PERCENTAGE,
    SIGNAL_STRENGTH_DECIBELS_MILLIWATT,
    EntityCategory,
    UnitOfPressure,
    UnitOfSpeed,
    UnitOfTemperature,
    UnitOfVolumeFlowRate,
)
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.util import dt as dt_util

from .coordinator import GraasConfigEntry, GraasCoordinator
from .entity import GraasEntity, GraasZoneEntity

# Soil units Home Assistant has no constants for.
UNIT_MG_PER_KG = "mg/kg"
UNIT_MG_PER_L = "mg/L"


@dataclass(frozen=True, kw_only=True)
class GraasSensorDescription(SensorEntityDescription):
    value_fn: Callable[[dict[str, Any]], Any]
    exists_fn: Callable[[dict[str, Any]], bool]


def _sensor(data: dict[str, Any], key: str) -> Any:
    return (data.get("sensors") or {}).get(key)


def _soil(data: dict[str, Any], key: str) -> Any:
    return (data.get("soil") or {}).get(key)


def _timestamp(value: Any) -> datetime | None:
    return dt_util.parse_datetime(value) if isinstance(value, str) else None


def _watering_ends(zone: dict[str, Any]) -> datetime | None:
    """Start + planned minutes while a timed run is on; unknown otherwise (litres runs end on volume)."""
    if not zone.get("irrigating"):
        return None
    started = _timestamp(zone.get("runningSince"))
    minutes = zone.get("targetDurationMinutes")
    if started is None or not isinstance(minutes, (int, float)) or minutes <= 0:
        return None
    return started + timedelta(minutes=minutes)


DEVICE_SENSORS: tuple[GraasSensorDescription, ...] = (
    GraasSensorDescription(
        key="air_temperature",
        translation_key="air_temperature",
        device_class=SensorDeviceClass.TEMPERATURE,
        native_unit_of_measurement=UnitOfTemperature.CELSIUS,
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=lambda d: _sensor(d, "airTemperature"),
        exists_fn=lambda d: _sensor(d, "airTemperature") is not None,
    ),
    GraasSensorDescription(
        key="air_humidity",
        translation_key="air_humidity",
        device_class=SensorDeviceClass.HUMIDITY,
        native_unit_of_measurement=PERCENTAGE,
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=lambda d: _sensor(d, "airHumidity"),
        exists_fn=lambda d: _sensor(d, "airHumidity") is not None,
    ),
    GraasSensorDescription(
        key="soil_temperature",
        translation_key="soil_temperature",
        device_class=SensorDeviceClass.TEMPERATURE,
        native_unit_of_measurement=UnitOfTemperature.CELSIUS,
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=lambda d: _sensor(d, "soilTemperature"),
        exists_fn=lambda d: _sensor(d, "soilTemperature") is not None,
    ),
    GraasSensorDescription(
        key="pressure",
        translation_key="water_pressure",
        device_class=SensorDeviceClass.PRESSURE,
        native_unit_of_measurement=UnitOfPressure.BAR,
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=lambda d: _sensor(d, "pressure"),
        exists_fn=lambda d: _sensor(d, "pressure") is not None,
    ),
    GraasSensorDescription(
        key="pressure2",
        translation_key="water_pressure_2",
        device_class=SensorDeviceClass.PRESSURE,
        native_unit_of_measurement=UnitOfPressure.BAR,
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=lambda d: _sensor(d, "pressure2"),
        exists_fn=lambda d: _sensor(d, "pressure2") is not None,
    ),
    GraasSensorDescription(
        key="wind_speed",
        translation_key="wind_speed",
        device_class=SensorDeviceClass.WIND_SPEED,
        native_unit_of_measurement=UnitOfSpeed.METERS_PER_SECOND,
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=lambda d: _sensor(d, "windSpeed"),
        exists_fn=lambda d: _sensor(d, "windSpeed") is not None,
    ),
    GraasSensorDescription(
        key="paused_until",
        translation_key="paused_until",
        device_class=SensorDeviceClass.TIMESTAMP,
        value_fn=lambda d: _timestamp(d.get("wateringPausedUntil")),
        exists_fn=lambda d: True,
    ),
    GraasSensorDescription(
        key="signal_strength",
        translation_key="signal_strength",
        device_class=SensorDeviceClass.SIGNAL_STRENGTH,
        native_unit_of_measurement=SIGNAL_STRENGTH_DECIBELS_MILLIWATT,
        state_class=SensorStateClass.MEASUREMENT,
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda d: d.get("signalStrength"),
        exists_fn=lambda d: True,
    ),
    GraasSensorDescription(
        key="last_seen",
        translation_key="last_seen",
        device_class=SensorDeviceClass.TIMESTAMP,
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda d: _timestamp(d.get("lastSeen")),
        exists_fn=lambda d: True,
    ),
)

ZONE_SENSORS: tuple[GraasSensorDescription, ...] = (
    GraasSensorDescription(
        key="soil_moisture",
        translation_key="soil_moisture",
        device_class=SensorDeviceClass.MOISTURE,
        native_unit_of_measurement=PERCENTAGE,
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=lambda z: _soil(z, "moisture"),
        exists_fn=lambda z: _soil(z, "moisture") is not None,
    ),
    GraasSensorDescription(
        key="zone_soil_temperature",
        translation_key="zone_soil_temperature",
        device_class=SensorDeviceClass.TEMPERATURE,
        native_unit_of_measurement=UnitOfTemperature.CELSIUS,
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=lambda z: _soil(z, "temperature"),
        exists_fn=lambda z: _soil(z, "temperature") is not None,
    ),
    GraasSensorDescription(
        key="soil_ec",
        translation_key="soil_ec",
        native_unit_of_measurement="µS/cm",
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=lambda z: _soil(z, "ec"),
        exists_fn=lambda z: _soil(z, "ec") is not None,
    ),
    GraasSensorDescription(
        key="soil_ph",
        translation_key="soil_ph",
        device_class=SensorDeviceClass.PH,
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=lambda z: _soil(z, "ph"),
        exists_fn=lambda z: _soil(z, "ph") is not None,
    ),
    *(
        GraasSensorDescription(
            key=f"soil_{field}",
            translation_key=f"soil_{field}",
            native_unit_of_measurement=unit,
            state_class=SensorStateClass.MEASUREMENT,
            value_fn=lambda z, field=field: _soil(z, field),
            exists_fn=lambda z, field=field: _soil(z, field) is not None,
        )
        # NPK in mg/kg of soil; salinity and dissolved solids in mg/L, as the GRAAS app shows them.
        for field, unit in (
            ("nitrogen", UNIT_MG_PER_KG),
            ("phosphorus", UNIT_MG_PER_KG),
            ("potassium", UNIT_MG_PER_KG),
            ("salinity", UNIT_MG_PER_L),
            ("tds", UNIT_MG_PER_L),
        )
    ),
    GraasSensorDescription(
        key="flow_rate",
        translation_key="flow_rate",
        device_class=SensorDeviceClass.VOLUME_FLOW_RATE,
        native_unit_of_measurement=UnitOfVolumeFlowRate.LITERS_PER_MINUTE,
        state_class=SensorStateClass.MEASUREMENT,
        # Most controllers have no flow meter and always report 0: off by default.
        entity_registry_enabled_default=False,
        value_fn=lambda z: z.get("flowRate"),
        exists_fn=lambda z: True,
    ),
    GraasSensorDescription(
        key="watering_ends",
        translation_key="watering_ends",
        device_class=SensorDeviceClass.TIMESTAMP,
        value_fn=_watering_ends,
        exists_fn=lambda z: True,
    ),
    GraasSensorDescription(
        key="next_run",
        translation_key="next_run",
        device_class=SensorDeviceClass.TIMESTAMP,
        value_fn=lambda z: _timestamp(z.get("nextRun")),
        exists_fn=lambda z: True,
    ),
    GraasSensorDescription(
        key="last_irrigation",
        translation_key="last_irrigation",
        device_class=SensorDeviceClass.TIMESTAMP,
        value_fn=lambda z: _timestamp(z.get("lastIrrigation")),
        exists_fn=lambda z: True,
    ),
)


async def async_setup_entry(
    hass: HomeAssistant, entry: GraasConfigEntry, async_add_entities: AddConfigEntryEntitiesCallback
) -> None:
    coordinator = entry.runtime_data
    entities: list[SensorEntity] = [
        GraasDeviceSensor(coordinator, device_id, description)
        for device_id, device in coordinator.data.devices.items()
        for description in DEVICE_SENSORS
        if description.exists_fn(device)
    ]
    entities += [
        GraasZoneSensor(coordinator, zone_id, description)
        for zone_id, zone in coordinator.data.zones.items()
        for description in ZONE_SENSORS
        if description.exists_fn(zone)
    ]
    async_add_entities(entities)


class GraasDeviceSensor(GraasEntity, SensorEntity):
    entity_description: GraasSensorDescription

    def __init__(self, coordinator: GraasCoordinator, device_id: int, description: GraasSensorDescription) -> None:
        super().__init__(coordinator, device_id, description.key)
        self.entity_description = description

    @property
    def native_value(self) -> Any:
        return self.entity_description.value_fn(self.device_data)


class GraasZoneSensor(GraasZoneEntity, SensorEntity):
    entity_description: GraasSensorDescription

    def __init__(self, coordinator: GraasCoordinator, zone_id: int, description: GraasSensorDescription) -> None:
        super().__init__(coordinator, zone_id, description.key)
        self.entity_description = description

    @property
    def native_value(self) -> Any:
        return self.entity_description.value_fn(self.zone_data)
