"""Tests for sensor platform — hub-level and per-miner entities."""
from __future__ import annotations

import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.solar_smart_miner.config_flow import (
    CONF_AI_ENABLED,
    CONF_BATTERY_ENTITY,
    CONF_GRID_ENTITY,
    CONF_OPENROUTER_KEY,
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
    AiAdvice,
    CoordinatorSnapshot,
    EnergySnapshot,
    MinerSnapshot,
)
from custom_components.solar_smart_miner.sensor import (
    AiAdviceSensor,
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
        data={CONF_SOLAR_ENTITY: SOLAR_ENTITY, CONF_GRID_ENTITY: GRID_ENTITY},
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
        data={CONF_SOLAR_ENTITY: SOLAR_ENTITY},
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
    """No per-miner entities created when hass-miner has no miners."""
    from homeassistant.helpers import entity_registry as er_module

    entry = _make_entry(hass)
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


async def test_two_miners_creates_ten_per_miner_entities(hass, add_hass_miner) -> None:
    """Covers AE1 (partial): 2 hass-miner miners × 5 metrics = 10 per-miner entities."""
    from homeassistant.helpers import entity_registry as er_module

    entry = _make_entry(hass, battery_entity=BATTERY_ENTITY)
    add_hass_miner("192.168.1.10", name="ASIC 1")
    add_hass_miner("192.168.1.11", name="ASIC 2")
    hass.states.async_set(SOLAR_ENTITY, "2000")
    hass.states.async_set(GRID_ENTITY, "1500")
    hass.states.async_set(BATTERY_ENTITY, "75")

    await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    er = er_module.async_get(hass)
    our_entities = [e for e in er.entities.values() if e.platform == DOMAIN]
    # 3 buttons (dashboard, ask AI, apply all) + 1 select (control mode) + 10 hub sensors
    # (solar, house, battery, total miners, grid export, available for miners, decision log,
    # AI advice, last action, activity) + 5 metrics per miner
    assert len(our_entities) == 3 + 1 + 10 + 2 * 5


async def test_miner_added_after_setup_gets_sensors(hass, add_hass_miner) -> None:
    """hass-miner can load after us; its miners must still get sensors."""
    from homeassistant.helpers import entity_registry as er_module

    entry = _make_entry(hass)
    hass.states.async_set(SOLAR_ENTITY, "2000")
    await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    add_hass_miner("192.168.1.10", name="Late miner")
    await entry.runtime_data.async_refresh()
    await hass.async_block_till_done()

    er = er_module.async_get(hass)
    miner_entities = [
        e for e in er.entities.values()
        if e.platform == DOMAIN and "192_168_1_10" in e.unique_id
    ]
    assert len(miner_entities) == len(_MINER_METRICS)


async def test_decision_log_sensor_exposes_trace(hass, add_hass_miner) -> None:
    entry = _make_entry(hass)
    add_hass_miner("192.168.1.10", name="ASIC 1")
    hass.states.async_set(SOLAR_ENTITY, "2000")
    hass.states.async_set(GRID_ENTITY, "1500")

    await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    state = next(
        s for s in hass.states.async_all("sensor") if s.entity_id.endswith("decision_log")
    )
    assert state.attributes["control_mode"] == "manual"
    assert state.attributes["trace"][0] == "READ"
    assert "ASIC 1" in state.attributes["proposals"]
    assert len(state.state) <= 255


# ---------------------------------------------------------------------------
# AI advice sensor
# ---------------------------------------------------------------------------


def _ai_sensor(hass, *, key: str | None = "sk-or-key", enabled: bool = True, advice=None):
    entry = _make_entry(hass)
    hass.config_entries.async_update_entry(
        entry,
        data={**entry.data, CONF_OPENROUTER_KEY: key},
        options={**entry.options, CONF_AI_ENABLED: enabled},
    )
    coord = _make_coordinator_with_data(hass, entry)
    coord.data.ai_advice = advice
    return AiAdviceSensor(coord, entry)


def _advice(**overrides) -> AiAdvice:
    return AiAdvice(
        **{
            "text": "Looks sensible.",
            "model": "x/y:free",
            "requested_at": "2026-10-05T10:00:00+00:00",
            "latency_s": 1.5,
            **overrides,
        }
    )


async def test_ai_sensor_shows_the_response_and_details(hass) -> None:
    sensor = _ai_sensor(hass, advice=_advice())

    assert sensor.native_value == "Looks sensible."
    attrs = sensor.extra_state_attributes
    assert attrs["control_mode"] == "manual"
    assert attrs["response"] == "Looks sensible."
    assert attrs["error"] is None
    assert attrs["model"] == "x/y:free"
    assert attrs["requested_at"] == "2026-10-05T10:00:00+00:00"
    assert attrs["latency_s"] == 1.5
    assert attrs["actions"] == []
    assert attrs["history"] == []
    assert attrs["log_file"].endswith("ai_log.jsonl")


async def test_ai_sensor_truncates_state_but_keeps_full_response(hass) -> None:
    long_text = "word " * 100
    sensor = _ai_sensor(hass, advice=_advice(text=long_text))

    assert len(sensor.native_value) == 255
    assert sensor.extra_state_attributes["response"] == long_text


async def test_ai_sensor_shows_errors(hass) -> None:
    sensor = _ai_sensor(hass, advice=_advice(text="", error="OpenRouter rejected the API key"))

    assert sensor.native_value == "Error: OpenRouter rejected the API key"
    assert sensor.extra_state_attributes["error"] == "OpenRouter rejected the API key"


async def test_ai_sensor_waits_for_first_answer(hass) -> None:
    sensor = _ai_sensor(hass, advice=None)

    assert sensor.native_value == "Waiting for the first answer"
    assert sensor.extra_state_attributes["control_mode"] == "manual"
    assert "response" not in sensor.extra_state_attributes


async def test_ai_sensor_off_without_key_or_when_disabled(hass) -> None:
    assert _ai_sensor(hass, key="", advice=_advice()).native_value == "Off"
    assert _ai_sensor(hass, enabled=False, advice=_advice()).native_value == "Off"


async def test_ai_sensor_keeps_bulky_response_out_of_recorder() -> None:
    for attr in ("response", "actions", "history"):
        assert attr in AiAdviceSensor._unrecorded_attributes


async def test_ai_sensor_state_prefers_the_summary_and_exposes_actions_and_history(hass) -> None:
    actions = [{"miner": "Brod1", "action": "reduce", "reason": "not_enough_energy", "note": ""}]
    sensor = _ai_sensor(
        hass, advice=_advice(text="full text", summary="Sun is going down", actions=actions)
    )
    sensor.coordinator.ai_log.history.appendleft({"time": "18:00:00", "summary": "x", "actions": []})

    assert sensor.native_value == "Sun is going down"
    attrs = sensor.extra_state_attributes
    assert attrs["response"] == "full text"
    assert attrs["actions"] == actions
    assert attrs["history"] == [{"time": "18:00:00", "summary": "x", "actions": []}]


async def test_decision_log_exposes_the_plans_with_actions(hass, add_hass_miner) -> None:
    hass.states.async_set(SOLAR_ENTITY, "2000")
    hass.states.async_set(GRID_ENTITY, "1500")
    add_hass_miner("192.168.1.50", name="ASIC 1", active="on")
    entry = _make_entry(hass)
    await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    state = next(s for s in hass.states.async_all("sensor") if "decision_log" in s.entity_id)

    plan = state.attributes["plans"]["ASIC 1"]
    assert set(plan) == {"action", "limit_w", "method", "reason"}
    assert plan["action"] in {"set_limit", "hold", "stop", "start"}


async def test_sensors_show_the_control_mode(hass, add_hass_miner) -> None:
    from custom_components.solar_smart_miner.const import CONF_CONTROL_MODE

    entry = _make_entry(hass)
    hass.config_entries.async_update_entry(entry, options={**entry.options, CONF_CONTROL_MODE: "auto"})
    add_hass_miner("192.168.1.10", name="ASIC 1")
    hass.states.async_set(SOLAR_ENTITY, "2000")
    hass.states.async_set(GRID_ENTITY, "1500")

    await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    for suffix in ("decision_log", "ai_advice"):
        state = next(s for s in hass.states.async_all("sensor") if s.entity_id.endswith(suffix))
        assert state.attributes["control_mode"] == "auto", suffix
        assert "preview_only" not in state.attributes, suffix
