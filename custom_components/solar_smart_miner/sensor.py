"""Sensor platform for Solar Smart Miner."""
from __future__ import annotations

from homeassistant.components.sensor import SensorDeviceClass, SensorEntity, SensorStateClass
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import PERCENTAGE, UnitOfPower, UnitOfTemperature
from homeassistant.core import HomeAssistant
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .config_flow import (
    CONF_BATTERY_ENTITY,
    CONF_GRID_ENTITY,
    CONF_MINER_IP,
    CONF_MINER_NAME,
    CONF_MINERS,
)
from .const import DOMAIN
from .coordinator import SolarMinerCoordinator
from .protocols import MinerSnapshot


def _hub_device_info(entry: ConfigEntry) -> DeviceInfo:
    return DeviceInfo(
        identifiers={(DOMAIN, entry.entry_id)},
        name=entry.title,
    )


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    coordinator: SolarMinerCoordinator = entry.runtime_data
    entities: list[SensorEntity] = [
        TotalMinerConsumptionSensor(coordinator, entry),
        SolarProductionSensor(coordinator, entry),
    ]

    if entry.data.get(CONF_GRID_ENTITY, ""):
        entities.append(GridConsumptionSensor(coordinator, entry))

    if entry.data.get(CONF_BATTERY_ENTITY, ""):
        entities.append(BatterySocSensor(coordinator, entry))

    for miner_conf in entry.data.get(CONF_MINERS, []):
        miner_ip: str = miner_conf.get(CONF_MINER_IP, "")
        miner_name: str = miner_conf.get(CONF_MINER_NAME, miner_ip)
        for metric in _MINER_METRICS:
            entities.append(MinerSensor(coordinator, entry, miner_ip, miner_name, metric))

    async_add_entities(entities)


# ---------------------------------------------------------------------------
# Hub-level sensors
# ---------------------------------------------------------------------------

class TotalMinerConsumptionSensor(CoordinatorEntity[SolarMinerCoordinator], SensorEntity):
    _attr_has_entity_name = True
    _attr_device_class = SensorDeviceClass.POWER
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_native_unit_of_measurement = UnitOfPower.WATT

    def __init__(self, coordinator: SolarMinerCoordinator, entry: ConfigEntry) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = f"{entry.unique_id or entry.entry_id}_total_miner_consumption"
        self._attr_name = "Total miner consumption"
        self._attr_device_info = _hub_device_info(entry)

    @property
    def native_value(self) -> float | None:
        if self.coordinator.data is None:
            return None
        return self.coordinator.data.energy.miner_consumption_sum_w


class SolarProductionSensor(CoordinatorEntity[SolarMinerCoordinator], SensorEntity):
    _attr_has_entity_name = True
    _attr_device_class = SensorDeviceClass.POWER
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_native_unit_of_measurement = UnitOfPower.WATT

    def __init__(self, coordinator: SolarMinerCoordinator, entry: ConfigEntry) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = f"{entry.unique_id or entry.entry_id}_solar_production"
        self._attr_name = "Solar production"
        self._attr_device_info = _hub_device_info(entry)

    @property
    def native_value(self) -> float | None:
        if self.coordinator.data is None:
            return None
        return self.coordinator.data.energy.solar_production_w


class GridConsumptionSensor(CoordinatorEntity[SolarMinerCoordinator], SensorEntity):
    _attr_has_entity_name = True
    _attr_device_class = SensorDeviceClass.POWER
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_native_unit_of_measurement = UnitOfPower.WATT

    def __init__(self, coordinator: SolarMinerCoordinator, entry: ConfigEntry) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = f"{entry.unique_id or entry.entry_id}_grid_consumption"
        self._attr_name = "Grid consumption"
        self._attr_device_info = _hub_device_info(entry)

    @property
    def native_value(self) -> float | None:
        if self.coordinator.data is None:
            return None
        return self.coordinator.data.energy.grid_consumption_w


class BatterySocSensor(CoordinatorEntity[SolarMinerCoordinator], SensorEntity):
    _attr_has_entity_name = True
    _attr_device_class = SensorDeviceClass.BATTERY
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_native_unit_of_measurement = PERCENTAGE

    def __init__(self, coordinator: SolarMinerCoordinator, entry: ConfigEntry) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = f"{entry.unique_id or entry.entry_id}_battery_soc"
        self._attr_name = "Battery SOC"
        self._attr_device_info = _hub_device_info(entry)

    @property
    def native_value(self) -> float | None:
        if self.coordinator.data is None:
            return None
        return self.coordinator.data.energy.battery_soc_pct


# ---------------------------------------------------------------------------
# Per-miner sensors
# ---------------------------------------------------------------------------

_MINER_METRICS: list[dict] = [
    {
        "key": "power_w",
        "label": "power draw",
        "device_class": SensorDeviceClass.POWER,
        "unit": UnitOfPower.WATT,
        "state_class": SensorStateClass.MEASUREMENT,
    },
    {
        "key": "temperature_c",
        "label": "temperature",
        "device_class": SensorDeviceClass.TEMPERATURE,
        "unit": UnitOfTemperature.CELSIUS,
        "state_class": SensorStateClass.MEASUREMENT,
    },
    {
        "key": "power_limit_w",
        "label": "power limit",
        "device_class": SensorDeviceClass.POWER,
        "unit": UnitOfPower.WATT,
        "state_class": SensorStateClass.MEASUREMENT,
    },
    {
        "key": "hashrate_th",
        "label": "hashrate",
        "device_class": None,
        "unit": "TH/s",
        "state_class": SensorStateClass.MEASUREMENT,
    },
    {
        "key": "efficiency_jth",
        "label": "efficiency",
        "device_class": None,
        "unit": "J/TH",
        "state_class": SensorStateClass.MEASUREMENT,
    },
]


class MinerSensor(CoordinatorEntity[SolarMinerCoordinator], SensorEntity):
    _attr_has_entity_name = True

    def __init__(
        self,
        coordinator: SolarMinerCoordinator,
        entry: ConfigEntry,
        miner_ip: str,
        miner_name: str,
        metric: dict,
    ) -> None:
        super().__init__(coordinator)
        ip_slug = miner_ip.replace(".", "_")
        self._miner_ip = miner_ip
        self._metric_key: str = metric["key"]
        self._attr_unique_id = (
            f"{entry.unique_id or entry.entry_id}_{ip_slug}_{metric['key']}"
        )
        self._attr_name = f"{miner_name} {metric['label']}"
        self._attr_device_class = metric["device_class"]
        self._attr_native_unit_of_measurement = metric["unit"]
        self._attr_state_class = metric["state_class"]
        self._attr_device_info = _hub_device_info(entry)

    @property
    def native_value(self) -> float | None:
        if self.coordinator.data is None:
            return None
        miner: MinerSnapshot | None = next(
            (m for m in self.coordinator.data.miners if m.ip == self._miner_ip),
            None,
        )
        if miner is None or not miner.is_available:
            return None
        return getattr(miner, self._metric_key)
