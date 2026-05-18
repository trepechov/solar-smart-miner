"""Sensor platform for Solar Smart Miner — U10 stub.

Exposes TotalMinerConsumptionSensor. Full sensor suite (solar, grid, battery,
per-miner metrics, last-decision) lands in U7.
"""
from __future__ import annotations

from homeassistant.components.sensor import SensorDeviceClass, SensorEntity, SensorStateClass
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import UnitOfPower
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .coordinator import SolarMinerCoordinator


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    coordinator: SolarMinerCoordinator = entry.runtime_data
    async_add_entities([TotalMinerConsumptionSensor(coordinator, entry)])


class TotalMinerConsumptionSensor(CoordinatorEntity[SolarMinerCoordinator], SensorEntity):
    _attr_has_entity_name = True
    _attr_device_class = SensorDeviceClass.POWER
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_native_unit_of_measurement = UnitOfPower.WATT

    def __init__(self, coordinator: SolarMinerCoordinator, entry: ConfigEntry) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = f"{entry.unique_id or entry.entry_id}_total_miner_consumption"
        self._attr_name = "Total miner consumption"

    @property
    def native_value(self) -> float | None:
        if self.coordinator.data is None:
            return None
        return self.coordinator.data.energy.miner_consumption_sum_w
