"""Tests for sensor platform — hub-level and per-miner entities."""
from __future__ import annotations

import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.solar_smart_miner.config_flow import (
    CONF_BATTERY_ENTITY,
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
from custom_components.solar_smart_miner.protocols import (
    CoordinatorSnapshot,
    EnergySnapshot,
    MinerSnapshot,
)
from custom_components.solar_smart_miner.sensor import (
    BatterySocSensor,
    GridConsumptionSensor,
    MinerSensor,
    SolarProductionSensor,
    TotalMinerConsumptionSensor,
    _MINER_METRICS,
    _hub_device_info,
)

SOLAR_ENTITY = "sensor.solar_power"
GRID_ENTITY = "sensor.grid_consumption"
BATTERY_ENTITY = "sensor.battery_soc"


def _make_entry(hass, battery_entity: str = ""):
    data = {
        CONF_SOLAR_ENTITY: SOLAR_ENTITY,
        CONF_GRID_ENTITY: GRID_ENTITY,
        CONF_MINERS: [],
    }
    if battery_entity:
        data[CONF_BATTERY_ENTITY] = battery_entity
    entry = MockConfigEntry(
        domain=DOMAIN,
        data=data,
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
    hass,
    entry,
    miner_consumption_sum_w: float | None = None,
    solar_production_w: float | None = 2000.0,
    grid_consumption_w: float | None = 1500.0,
    battery_soc_pct: float | None = None,
    solar_fault: bool = False,
    miners: list | None = None,
) -> SolarMinerCoordinator:
    coord = SolarMinerCoordinator(hass, entry)
    energy = EnergySnapshot(
        solar_production_w=solar_production_w,
        grid_consumption_w=grid_consumption_w,
        battery_soc_pct=battery_soc_pct,
        miner_consumption_sum_w=miner_consumption_sum_w,
        solar_fault=solar_fault,
    )
    coord.data = CoordinatorSnapshot(energy=energy, miners=miners or [])
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

    all_states = hass.states.async_all()
    sensor_states = [s for s in all_states if "miner_consumption" in s.entity_id]
    assert len(sensor_states) == 1


# ---------------------------------------------------------------------------
# Hub DeviceInfo
# ---------------------------------------------------------------------------

async def test_hub_device_info_uses_entry_id(hass) -> None:
    entry = _make_entry(hass)
    coord = _make_coordinator_with_data(hass, entry)
    sensor = TotalMinerConsumptionSensor(coord, entry)
    assert (DOMAIN, entry.entry_id) in sensor.device_info["identifiers"]


async def test_all_hub_sensors_share_same_device_info(hass) -> None:
    entry = _make_entry(hass, battery_entity=BATTERY_ENTITY)
    coord = _make_coordinator_with_data(hass, entry, battery_soc_pct=80.0)
    sensors = [
        TotalMinerConsumptionSensor(coord, entry),
        SolarProductionSensor(coord, entry),
        GridConsumptionSensor(coord, entry),
        BatterySocSensor(coord, entry),
    ]
    expected = {(DOMAIN, entry.entry_id)}
    for s in sensors:
        assert s.device_info["identifiers"] == expected


# ---------------------------------------------------------------------------
# SolarProductionSensor
# ---------------------------------------------------------------------------

async def test_solar_production_native_value(hass) -> None:
    entry = _make_entry(hass)
    coord = _make_coordinator_with_data(hass, entry, solar_production_w=2450.0)
    sensor = SolarProductionSensor(coord, entry)
    assert sensor.native_value == pytest.approx(2450.0)


async def test_solar_production_none_when_coordinator_data_none(hass) -> None:
    entry = _make_entry(hass)
    coord = SolarMinerCoordinator(hass, entry)
    coord.data = None
    sensor = SolarProductionSensor(coord, entry)
    assert sensor.native_value is None


async def test_solar_production_none_when_solar_fault(hass) -> None:
    entry = _make_entry(hass)
    coord = _make_coordinator_with_data(hass, entry, solar_production_w=None, solar_fault=True)
    sensor = SolarProductionSensor(coord, entry)
    assert sensor.native_value is None


async def test_solar_production_unique_id(hass) -> None:
    entry = _make_entry(hass)
    coord = _make_coordinator_with_data(hass, entry)
    sensor = SolarProductionSensor(coord, entry)
    assert sensor.unique_id is not None
    assert "solar_production" in sensor.unique_id
    assert entry.entry_id in sensor.unique_id


# ---------------------------------------------------------------------------
# GridConsumptionSensor
# ---------------------------------------------------------------------------

async def test_grid_consumption_native_value(hass) -> None:
    entry = _make_entry(hass)
    coord = _make_coordinator_with_data(hass, entry, grid_consumption_w=1200.0)
    sensor = GridConsumptionSensor(coord, entry)
    assert sensor.native_value == pytest.approx(1200.0)


async def test_grid_consumption_none_when_coordinator_data_none(hass) -> None:
    entry = _make_entry(hass)
    coord = SolarMinerCoordinator(hass, entry)
    coord.data = None
    sensor = GridConsumptionSensor(coord, entry)
    assert sensor.native_value is None


async def test_grid_sensor_not_created_when_entity_absent(hass) -> None:
    """GridConsumptionSensor is only added when CONF_GRID_ENTITY is configured."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        data={CONF_SOLAR_ENTITY: SOLAR_ENTITY, CONF_MINERS: []},
        options={
            CONF_POLLING_INTERVAL: DEFAULT_POLLING_INTERVAL,
            CONF_MOCK_SOLAR_ENABLED: False,
            CONF_MOCK_CONSUMPTION_ENABLED: False,
        },
        version=1,
    )
    entry.add_to_hass(hass)
    hass.states.async_set(SOLAR_ENTITY, "2000")

    await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    all_states = hass.states.async_all()
    grid_states = [s for s in all_states if "grid_consumption" in s.entity_id]
    assert len(grid_states) == 0


# ---------------------------------------------------------------------------
# BatterySocSensor
# ---------------------------------------------------------------------------

async def test_battery_soc_native_value(hass) -> None:
    entry = _make_entry(hass, battery_entity=BATTERY_ENTITY)
    coord = _make_coordinator_with_data(hass, entry, battery_soc_pct=78.0)
    sensor = BatterySocSensor(coord, entry)
    assert sensor.native_value == pytest.approx(78.0)


async def test_battery_soc_none_when_coordinator_data_none(hass) -> None:
    entry = _make_entry(hass, battery_entity=BATTERY_ENTITY)
    coord = SolarMinerCoordinator(hass, entry)
    coord.data = None
    sensor = BatterySocSensor(coord, entry)
    assert sensor.native_value is None


async def test_battery_sensor_not_created_when_entity_absent(hass) -> None:
    """Covers AE3: BatterySocSensor is not created when CONF_BATTERY_ENTITY is absent."""
    from homeassistant.helpers import entity_registry as er_module

    entry = _make_entry(hass)  # no battery_entity
    hass.states.async_set(SOLAR_ENTITY, "2000")
    hass.states.async_set(GRID_ENTITY, "1500")

    await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    er = er_module.async_get(hass)
    battery_entities = [e for e in er.entities.values() if e.platform == DOMAIN and "battery_soc" in e.entity_id]
    assert len(battery_entities) == 0


async def test_battery_sensor_created_when_entity_configured(hass) -> None:
    from homeassistant.helpers import entity_registry as er_module

    entry = _make_entry(hass, battery_entity=BATTERY_ENTITY)
    hass.states.async_set(SOLAR_ENTITY, "2000")
    hass.states.async_set(GRID_ENTITY, "1500")
    hass.states.async_set(BATTERY_ENTITY, "75")

    await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    er = er_module.async_get(hass)
    battery_entities = [e for e in er.entities.values() if e.platform == DOMAIN and "battery_soc" in e.entity_id]
    assert len(battery_entities) == 1
