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


# ---------------------------------------------------------------------------
# U4: Per-miner sensor entities
# ---------------------------------------------------------------------------

def _make_miner_snapshot(
    ip: str = "192.168.1.10",
    is_available: bool = True,
    power_w: float | None = 600.0,
    temperature_c: float | None = 65.0,
    power_limit_w: float | None = 750.0,
    hashrate_th: float | None = 45.5,
    efficiency_jth: float | None = 21.3,
) -> MinerSnapshot:
    return MinerSnapshot(
        miner_id=ip,
        ip=ip,
        power_w=power_w,
        power_limit_w=power_limit_w,
        min_power_w=200.0,
        max_power_w=1500.0,
        temperature_c=temperature_c,
        is_available=is_available,
        power_limit_entity_id=f"number.miner_{ip.replace('.', '_')}_limit",
        hashrate_th=hashrate_th,
        efficiency_jth=efficiency_jth,
    )


def _get_metric(key: str) -> dict:
    return next(m for m in _MINER_METRICS if m["key"] == key)


async def test_miner_sensor_power_draw(hass) -> None:
    entry = _make_entry(hass)
    miner = _make_miner_snapshot(ip="192.168.1.10", power_w=600.0)
    coord = _make_coordinator_with_data(hass, entry, miners=[miner])

    sensor = MinerSensor(coord, entry, "192.168.1.10", "ASIC 1", _get_metric("power_w"))
    assert sensor.native_value == pytest.approx(600.0)


async def test_miner_sensor_temperature(hass) -> None:
    entry = _make_entry(hass)
    miner = _make_miner_snapshot(ip="192.168.1.10", temperature_c=65.0)
    coord = _make_coordinator_with_data(hass, entry, miners=[miner])

    sensor = MinerSensor(coord, entry, "192.168.1.10", "ASIC 1", _get_metric("temperature_c"))
    assert sensor.native_value == pytest.approx(65.0)


async def test_miner_sensor_power_limit(hass) -> None:
    entry = _make_entry(hass)
    miner = _make_miner_snapshot(ip="192.168.1.10", power_limit_w=750.0)
    coord = _make_coordinator_with_data(hass, entry, miners=[miner])

    sensor = MinerSensor(coord, entry, "192.168.1.10", "ASIC 1", _get_metric("power_limit_w"))
    assert sensor.native_value == pytest.approx(750.0)


async def test_miner_sensor_hashrate(hass) -> None:
    entry = _make_entry(hass)
    miner = _make_miner_snapshot(ip="192.168.1.10", hashrate_th=45.5)
    coord = _make_coordinator_with_data(hass, entry, miners=[miner])

    sensor = MinerSensor(coord, entry, "192.168.1.10", "ASIC 1", _get_metric("hashrate_th"))
    assert sensor.native_value == pytest.approx(45.5)


async def test_miner_sensor_hashrate_none_when_not_populated(hass) -> None:
    entry = _make_entry(hass)
    miner = _make_miner_snapshot(ip="192.168.1.10", hashrate_th=None)
    coord = _make_coordinator_with_data(hass, entry, miners=[miner])

    sensor = MinerSensor(coord, entry, "192.168.1.10", "ASIC 1", _get_metric("hashrate_th"))
    assert sensor.native_value is None


async def test_miner_sensor_efficiency(hass) -> None:
    entry = _make_entry(hass)
    miner = _make_miner_snapshot(ip="192.168.1.10", efficiency_jth=21.3)
    coord = _make_coordinator_with_data(hass, entry, miners=[miner])

    sensor = MinerSensor(coord, entry, "192.168.1.10", "ASIC 1", _get_metric("efficiency_jth"))
    assert sensor.native_value == pytest.approx(21.3)


async def test_miner_sensor_none_when_coordinator_data_none(hass) -> None:
    entry = _make_entry(hass)
    coord = SolarMinerCoordinator(hass, entry)
    coord.data = None

    sensor = MinerSensor(coord, entry, "192.168.1.10", "ASIC 1", _get_metric("power_w"))
    assert sensor.native_value is None


async def test_miner_sensor_none_when_unavailable(hass) -> None:
    """Covers AE2: all 5 sensors return None when miner is_available=False."""
    entry = _make_entry(hass)
    miner = _make_miner_snapshot(ip="192.168.1.10", is_available=False)
    coord = _make_coordinator_with_data(hass, entry, miners=[miner])

    for metric in _MINER_METRICS:
        sensor = MinerSensor(coord, entry, "192.168.1.10", "ASIC 1", metric)
        assert sensor.native_value is None, f"Expected None for {metric['key']} when unavailable"


async def test_miner_sensor_none_when_ip_not_in_snapshot(hass) -> None:
    """Sensor returns None when its IP doesn't match any snapshot (e.g. stale after reload)."""
    entry = _make_entry(hass)
    miner = _make_miner_snapshot(ip="192.168.1.99")
    coord = _make_coordinator_with_data(hass, entry, miners=[miner])

    sensor = MinerSensor(coord, entry, "192.168.1.10", "ASIC 1", _get_metric("power_w"))
    assert sensor.native_value is None


async def test_miner_sensor_device_info_is_hub(hass) -> None:
    entry = _make_entry(hass)
    miner = _make_miner_snapshot()
    coord = _make_coordinator_with_data(hass, entry, miners=[miner])

    sensor = MinerSensor(coord, entry, "192.168.1.10", "ASIC 1", _get_metric("power_w"))
    assert sensor.device_info["identifiers"] == {(DOMAIN, entry.entry_id)}


async def test_miner_sensor_unique_id_format(hass) -> None:
    entry = _make_entry(hass)
    miner = _make_miner_snapshot()
    coord = _make_coordinator_with_data(hass, entry, miners=[miner])

    sensor = MinerSensor(coord, entry, "192.168.1.10", "ASIC 1", _get_metric("power_w"))
    assert "192_168_1_10" in sensor.unique_id
    assert "power_w" in sensor.unique_id
    assert entry.entry_id in sensor.unique_id


async def test_zero_miners_creates_no_per_miner_entities(hass) -> None:
    """No per-miner entities created when CONF_MINERS is empty."""
    from homeassistant.helpers import entity_registry as er_module

    entry = _make_entry(hass)  # CONF_MINERS=[]
    hass.states.async_set(SOLAR_ENTITY, "2000")
    hass.states.async_set(GRID_ENTITY, "1500")

    await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    er = er_module.async_get(hass)
    miner_sensor_entities = [
        e for e in er.entities.values()
        if e.platform == DOMAIN and any(
            k in e.entity_id for k in ("power_draw", "temperature", "hashrate", "efficiency", "power_limit")
        )
    ]
    assert len(miner_sensor_entities) == 0


async def test_two_miners_creates_ten_per_miner_entities(hass) -> None:
    """Covers AE1 (partial): 2 miners × 5 metrics = 10 per-miner entities."""
    from homeassistant.helpers import entity_registry as er_module

    entry = MockConfigEntry(
        domain=DOMAIN,
        data={
            CONF_SOLAR_ENTITY: SOLAR_ENTITY,
            CONF_GRID_ENTITY: GRID_ENTITY,
            CONF_BATTERY_ENTITY: BATTERY_ENTITY,
            CONF_MINERS: [
                {"miner_name": "ASIC 1", "miner_ip": "192.168.1.10"},
                {"miner_name": "ASIC 2", "miner_ip": "192.168.1.11"},
            ],
        },
        options={
            CONF_POLLING_INTERVAL: DEFAULT_POLLING_INTERVAL,
            CONF_MOCK_SOLAR_ENABLED: False,
            CONF_MOCK_CONSUMPTION_ENABLED: False,
        },
        version=1,
    )
    entry.add_to_hass(hass)
    hass.states.async_set(SOLAR_ENTITY, "2000")
    hass.states.async_set(GRID_ENTITY, "1500")
    hass.states.async_set(BATTERY_ENTITY, "75")

    await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    er = er_module.async_get(hass)
    our_entities = [e for e in er.entities.values() if e.platform == DOMAIN]
    # 4 hub-level (solar, grid, battery, total consumption) + 10 per-miner = 14
    assert len(our_entities) == 14
