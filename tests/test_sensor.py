"""Tests for TotalMinerConsumptionSensor — U10."""
from __future__ import annotations

from unittest.mock import MagicMock

import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.solar_smart_miner.config_flow import (
    CONF_GRID_ENTITY,
    CONF_MINERS,
    CONF_POLLING_INTERVAL,
    CONF_SOLAR_ENTITY,
)
from custom_components.solar_smart_miner.const import (
    CONF_MOCK_CONSUMPTION_ENABLED,
    CONF_MOCK_SOLAR_ENABLED,
    DEFAULT_POLLING_INTERVAL,
    DOMAIN,
)
from custom_components.solar_smart_miner.coordinator import SolarMinerCoordinator
from custom_components.solar_smart_miner.protocols import CoordinatorSnapshot, EnergySnapshot
from custom_components.solar_smart_miner.sensor import TotalMinerConsumptionSensor

SOLAR_ENTITY = "sensor.solar_power"
GRID_ENTITY = "sensor.grid_consumption"


def _make_entry(hass):
    entry = MockConfigEntry(
        domain=DOMAIN,
        data={
            CONF_SOLAR_ENTITY: SOLAR_ENTITY,
            CONF_GRID_ENTITY: GRID_ENTITY,
            CONF_MINERS: [],
        },
        options={
            CONF_POLLING_INTERVAL: DEFAULT_POLLING_INTERVAL,
            CONF_MOCK_SOLAR_ENABLED: False,
            CONF_MOCK_CONSUMPTION_ENABLED: False,
        },
        version=1,
    )
    entry.add_to_hass(hass)
    return entry


def _make_coordinator_with_data(
    hass, entry, miner_consumption_sum_w: float | None
) -> SolarMinerCoordinator:
    coord = SolarMinerCoordinator(hass, entry)
    energy = EnergySnapshot(
        solar_production_w=2000.0,
        grid_consumption_w=1500.0,
        miner_consumption_sum_w=miner_consumption_sum_w,
    )
    coord.data = CoordinatorSnapshot(energy=energy, miners=[])
    return coord


async def test_sensor_native_value_returns_sum(hass) -> None:
    entry = _make_entry(hass)
    coord = _make_coordinator_with_data(hass, entry, miner_consumption_sum_w=1400.0)

    sensor = TotalMinerConsumptionSensor(coord, entry)

    assert sensor.native_value == pytest.approx(1400.0)


async def test_sensor_native_value_none_when_sum_none(hass) -> None:
    entry = _make_entry(hass)
    coord = _make_coordinator_with_data(hass, entry, miner_consumption_sum_w=None)

    sensor = TotalMinerConsumptionSensor(coord, entry)

    assert sensor.native_value is None


async def test_sensor_native_value_none_when_coordinator_data_none(hass) -> None:
    entry = _make_entry(hass)
    coord = SolarMinerCoordinator(hass, entry)
    coord.data = None  # simulate pre-first-refresh state

    sensor = TotalMinerConsumptionSensor(coord, entry)

    assert sensor.native_value is None


async def test_sensor_unique_id_uses_entry_id_fallback(hass) -> None:
    entry = MockConfigEntry(
        domain=DOMAIN,
        data={CONF_SOLAR_ENTITY: SOLAR_ENTITY, CONF_GRID_ENTITY: GRID_ENTITY, CONF_MINERS: []},
        options={
            CONF_POLLING_INTERVAL: DEFAULT_POLLING_INTERVAL,
            CONF_MOCK_SOLAR_ENABLED: False,
            CONF_MOCK_CONSUMPTION_ENABLED: False,
        },
        unique_id=None,
        version=1,
    )
    entry.add_to_hass(hass)
    coord = _make_coordinator_with_data(hass, entry, miner_consumption_sum_w=0.0)

    sensor = TotalMinerConsumptionSensor(coord, entry)

    assert sensor.unique_id is not None
    assert "total_miner_consumption" in sensor.unique_id


async def test_sensor_registered_via_async_setup_entry(hass) -> None:
    """Integration: sensor registers when integration is set up."""
    hass.states.async_set(SOLAR_ENTITY, "2000")
    hass.states.async_set(GRID_ENTITY, "1500")
    entry = _make_entry(hass)

    await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    # The sensor entity should now be in hass state machine
    all_states = hass.states.async_all()
    sensor_states = [s for s in all_states if "miner_consumption" in s.entity_id]
    assert len(sensor_states) == 1
