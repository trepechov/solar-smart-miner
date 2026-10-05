"""Tests for SolarMinerCoordinator — U11 (entity reads + power limit apply) and U10 (miner sum + mock consumption)."""
from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.solar_smart_miner.config_flow import (
    CONF_BATTERY_ENTITY,
    CONF_GRID_ENTITY,
    CONF_POLLING_INTERVAL,
    CONF_PROFILE,
    CONF_SOLAR_ENTITY,
    CONF_SOLAR_ENTITY_TYPE,
)
from custom_components.solar_smart_miner.const import (
    CONF_MOCK_CONSUMPTION_ENABLED,
    CONF_MOCK_SOLAR_ENABLED,
    CONF_MOCK_SOLAR_ENTITY,
    DEFAULT_POLLING_INTERVAL,
    DOMAIN,
    SOLAR_ENTITY_TYPE_NET_EXPORT,
    SOLAR_ENTITY_TYPE_NET_IMPORT,
)
from custom_components.solar_smart_miner.coordinator import SolarMinerCoordinator
from custom_components.solar_smart_miner.protocols import (
    CoordinatorSnapshot,
    MinerSnapshot,
)

SOLAR_ENTITY = "sensor.solar_power"
GRID_ENTITY = "sensor.grid_consumption"
FORECAST_ENTITY = "sensor.forecast_solar_power_production_now"
BATTERY_ENTITY = "sensor.battery_soc"
MINER_IP = "192.168.1.100"
MINER_IP_2 = "192.168.1.101"
POWER_LIMIT_ENTITY = "number.miner_power_limit"


def _make_entry(
    hass,
    *,
    mock_enabled: bool = False,
    mock_entity: str | None = None,
    mock_consumption_enabled: bool = False,
    battery_entity: str | None = None,
    solar_entity_type: str | None = None,
    options: dict | None = None,
):
    data = {
        CONF_SOLAR_ENTITY: SOLAR_ENTITY,
        CONF_GRID_ENTITY: GRID_ENTITY,
        CONF_BATTERY_ENTITY: battery_entity,
    }
    if solar_entity_type is not None:
        data[CONF_SOLAR_ENTITY_TYPE] = solar_entity_type
    entry = MockConfigEntry(
        domain=DOMAIN,
        data=data,
        options={
            CONF_POLLING_INTERVAL: DEFAULT_POLLING_INTERVAL,
            CONF_MOCK_SOLAR_ENABLED: mock_enabled,
            CONF_MOCK_SOLAR_ENTITY: mock_entity,
            CONF_MOCK_CONSUMPTION_ENABLED: mock_consumption_enabled,
            **(options or {}),
        },
        version=1,
    )
    entry.add_to_hass(hass)
    return entry


# ---------------------------------------------------------------------------
# U9 mock solar tests — updated to use CoordinatorSnapshot
# ---------------------------------------------------------------------------


async def test_coordinator_reads_real_solar_when_mock_disabled(hass) -> None:
    hass.states.async_set(SOLAR_ENTITY, "2000")
    hass.states.async_set(GRID_ENTITY, "1500")
    entry = _make_entry(hass, mock_enabled=False)
    coord = SolarMinerCoordinator(hass, entry)

    snapshot = await coord._async_update_data()

    assert isinstance(snapshot, CoordinatorSnapshot)
    assert snapshot.energy.solar_production_w == pytest.approx(2000.0)
    assert snapshot.energy.mock_solar is False


async def test_coordinator_reads_mock_solar_when_enabled(hass) -> None:
    hass.states.async_set(SOLAR_ENTITY, "2000")
    hass.states.async_set(GRID_ENTITY, "1500")
    hass.states.async_set(FORECAST_ENTITY, "3500")
    entry = _make_entry(hass, mock_enabled=True, mock_entity=FORECAST_ENTITY)
    coord = SolarMinerCoordinator(hass, entry)

    snapshot = await coord._async_update_data()

    assert snapshot.energy.solar_production_w == pytest.approx(3500.0)
    assert snapshot.energy.mock_solar is True


async def test_coordinator_falls_back_when_mock_entity_unavailable(hass) -> None:
    hass.states.async_set(SOLAR_ENTITY, "1800")
    hass.states.async_set(GRID_ENTITY, "1500")
    hass.states.async_set(FORECAST_ENTITY, "unavailable")
    entry = _make_entry(hass, mock_enabled=True, mock_entity=FORECAST_ENTITY)
    coord = SolarMinerCoordinator(hass, entry)

    snapshot = await coord._async_update_data()

    assert snapshot.energy.solar_production_w == pytest.approx(1800.0)
    assert snapshot.energy.mock_solar is False


async def test_coordinator_falls_back_when_mock_entity_not_configured(hass) -> None:
    hass.states.async_set(SOLAR_ENTITY, "1200")
    hass.states.async_set(GRID_ENTITY, "1500")
    entry = _make_entry(hass, mock_enabled=True, mock_entity=None)
    coord = SolarMinerCoordinator(hass, entry)

    snapshot = await coord._async_update_data()

    assert snapshot.energy.solar_production_w == pytest.approx(1200.0)
    assert snapshot.energy.mock_solar is False


async def test_coordinator_solar_unavailable_sets_solar_fault(hass) -> None:
    """Edge: real solar unavailable → solar_fault=True, no exception raised (replaces UpdateFailed)."""
    hass.states.async_set(SOLAR_ENTITY, "unavailable")
    hass.states.async_set(GRID_ENTITY, "1500")
    entry = _make_entry(hass, mock_enabled=False)
    coord = SolarMinerCoordinator(hass, entry)

    snapshot = await coord._async_update_data()

    assert snapshot.energy.solar_fault is True
    assert snapshot.energy.solar_production_w is None


# ---------------------------------------------------------------------------
# U11 — energy reads
# ---------------------------------------------------------------------------


async def test_coordinator_reads_grid_entity(hass) -> None:
    hass.states.async_set(SOLAR_ENTITY, "2000")
    hass.states.async_set(GRID_ENTITY, "1500")
    entry = _make_entry(hass)
    coord = SolarMinerCoordinator(hass, entry)

    snapshot = await coord._async_update_data()

    assert snapshot.energy.grid_consumption_w == pytest.approx(1500.0)


async def test_coordinator_grid_unavailable_returns_none(hass) -> None:
    hass.states.async_set(SOLAR_ENTITY, "2000")
    hass.states.async_set(GRID_ENTITY, "unknown")
    entry = _make_entry(hass)
    coord = SolarMinerCoordinator(hass, entry)

    snapshot = await coord._async_update_data()

    assert snapshot.energy.grid_consumption_w is None
    assert snapshot.energy.solar_fault is False


async def test_coordinator_reads_battery_entity(hass) -> None:
    hass.states.async_set(SOLAR_ENTITY, "2000")
    hass.states.async_set(GRID_ENTITY, "1500")
    hass.states.async_set(BATTERY_ENTITY, "75")
    entry = _make_entry(hass, battery_entity=BATTERY_ENTITY)
    coord = SolarMinerCoordinator(hass, entry)

    snapshot = await coord._async_update_data()

    assert snapshot.energy.battery_soc_pct == pytest.approx(75.0)


async def test_coordinator_battery_not_configured_returns_none(hass) -> None:
    hass.states.async_set(SOLAR_ENTITY, "2000")
    hass.states.async_set(GRID_ENTITY, "1500")
    entry = _make_entry(hass, battery_entity=None)
    coord = SolarMinerCoordinator(hass, entry)

    snapshot = await coord._async_update_data()

    assert snapshot.energy.battery_soc_pct is None


# ---------------------------------------------------------------------------
# U11 — miner reads
# ---------------------------------------------------------------------------


async def test_coordinator_reads_miner_entities(hass, add_hass_miner) -> None:
    hass.states.async_set(SOLAR_ENTITY, "2000")
    hass.states.async_set(GRID_ENTITY, "1500")
    entry = _make_entry(hass)
    miner_reg = add_hass_miner(MINER_IP, name="Antheater00", hashrate="95.5")

    coord = SolarMinerCoordinator(hass, entry)
    snapshot = await coord._async_update_data()

    assert len(snapshot.miners) == 1
    miner = snapshot.miners[0]
    assert miner.is_available is True
    assert miner.name == "Antheater00"
    assert miner.ip == MINER_IP
    assert miner.miner_id == MINER_IP
    # Miner-level entities, not the decoys (power-limit sensor, board temp, ideal hashrate).
    assert miner.power_w == pytest.approx(600.0)
    assert miner.temperature_c == pytest.approx(65.0)
    assert miner.hashrate_th == pytest.approx(95.5)
    assert miner.power_limit_w == pytest.approx(800.0)
    assert miner.min_power_w == pytest.approx(200.0)
    assert miner.max_power_w == pytest.approx(1500.0)
    assert miner.power_limit_entity_id == miner_reg["power_limit"].entity_id


async def test_coordinator_discovers_every_hass_miner_entry(hass, add_hass_miner) -> None:
    hass.states.async_set(SOLAR_ENTITY, "2000")
    hass.states.async_set(GRID_ENTITY, "1500")
    entry = _make_entry(hass)
    add_hass_miner(MINER_IP)
    add_hass_miner(MINER_IP_2)

    coord = SolarMinerCoordinator(hass, entry)
    snapshot = await coord._async_update_data()

    assert {m.ip for m in snapshot.miners} == {MINER_IP, MINER_IP_2}
    assert all(m.is_available for m in snapshot.miners)


async def test_coordinator_uses_device_name_by_user(hass, add_hass_miner) -> None:
    from homeassistant.helpers import device_registry as dr_module

    hass.states.async_set(SOLAR_ENTITY, "2000")
    entry = _make_entry(hass)
    miner_reg = add_hass_miner(MINER_IP, name="Antheater00")
    dr_module.async_get(hass).async_update_device(
        miner_reg["device"].id, name_by_user="Garage miner"
    )

    snapshot = await SolarMinerCoordinator(hass, entry)._async_update_data()

    assert snapshot.miners[0].name == "Garage miner"


async def test_coordinator_min_max_fall_back_to_hass_miner_entry_data(hass, add_hass_miner) -> None:
    hass.states.async_set(SOLAR_ENTITY, "2000")
    entry = _make_entry(hass)
    add_hass_miner(
        MINER_IP, limit_attrs={}, entry_data={"min_power": 500, "max_power": 900}
    )

    snapshot = await SolarMinerCoordinator(hass, entry)._async_update_data()

    assert snapshot.miners[0].min_power_w == pytest.approx(500.0)
    assert snapshot.miners[0].max_power_w == pytest.approx(900.0)


async def test_coordinator_skips_disabled_hass_miner_entry(hass, add_hass_miner) -> None:
    from homeassistant.config_entries import ConfigEntryDisabler

    hass.states.async_set(SOLAR_ENTITY, "2000")
    entry = _make_entry(hass)
    miner_reg = add_hass_miner(MINER_IP)
    await hass.config_entries.async_set_disabled_by(
        miner_reg["entry"].entry_id, ConfigEntryDisabler.USER
    )

    snapshot = await SolarMinerCoordinator(hass, entry)._async_update_data()

    assert snapshot.miners == []


async def test_coordinator_miner_power_unavailable_marks_unavailable(hass, add_hass_miner) -> None:
    hass.states.async_set(SOLAR_ENTITY, "2000")
    hass.states.async_set(GRID_ENTITY, "1500")
    entry = _make_entry(hass)
    add_hass_miner(MINER_IP, power="unavailable")

    coord = SolarMinerCoordinator(hass, entry)
    snapshot = await coord._async_update_data()

    assert snapshot.miners[0].is_available is False


async def test_coordinator_miner_power_unavailable_does_not_affect_other(hass, add_hass_miner) -> None:
    hass.states.async_set(SOLAR_ENTITY, "2000")
    hass.states.async_set(GRID_ENTITY, "1500")
    entry = _make_entry(hass)
    add_hass_miner(MINER_IP, power="unavailable")
    add_hass_miner(MINER_IP_2)

    coord = SolarMinerCoordinator(hass, entry)
    snapshot = await coord._async_update_data()

    by_ip = {m.ip: m for m in snapshot.miners}
    assert by_ip[MINER_IP].is_available is False
    assert by_ip[MINER_IP_2].is_available is True


async def test_coordinator_no_hass_miner_entries(hass) -> None:
    hass.states.async_set(SOLAR_ENTITY, "2000")
    hass.states.async_set(GRID_ENTITY, "1500")
    entry = _make_entry(hass)
    coord = SolarMinerCoordinator(hass, entry)
    snapshot = await coord._async_update_data()

    assert snapshot.miners == []


# ---------------------------------------------------------------------------
# Grid balance — net meter types and unit conversion
# ---------------------------------------------------------------------------


async def test_net_export_meter_derives_solar_and_available(hass, add_hass_miner) -> None:
    """Net meter at -350 W (importing) with house at 1,500 W → solar 1,150 W."""
    hass.states.async_set(SOLAR_ENTITY, "-350")
    hass.states.async_set(GRID_ENTITY, "1500")
    entry = _make_entry(hass, solar_entity_type=SOLAR_ENTITY_TYPE_NET_EXPORT)
    add_hass_miner(MINER_IP, power="600")

    snapshot = await SolarMinerCoordinator(hass, entry)._async_update_data()
    energy = snapshot.energy

    assert energy.grid_net_w == pytest.approx(-350.0)
    assert energy.solar_production_w == pytest.approx(1150.0)
    # Miners could draw 600 - 350 = 250 W without importing.
    assert energy.available_for_miners_w == pytest.approx(250.0)
    assert energy.solar_fault is False


async def test_net_import_meter_flips_sign(hass) -> None:
    hass.states.async_set(SOLAR_ENTITY, "-400")  # exporting 400 W
    hass.states.async_set(GRID_ENTITY, "1000")
    entry = _make_entry(hass, solar_entity_type=SOLAR_ENTITY_TYPE_NET_IMPORT)

    energy = (await SolarMinerCoordinator(hass, entry)._async_update_data()).energy

    assert energy.grid_net_w == pytest.approx(400.0)
    assert energy.solar_production_w == pytest.approx(1400.0)


async def test_net_meter_without_house_reading_leaves_solar_unknown(hass) -> None:
    hass.states.async_set(SOLAR_ENTITY, "300")
    hass.states.async_set(GRID_ENTITY, "unknown")
    entry = _make_entry(hass, solar_entity_type=SOLAR_ENTITY_TYPE_NET_EXPORT)

    energy = (await SolarMinerCoordinator(hass, entry)._async_update_data()).energy

    assert energy.grid_net_w == pytest.approx(300.0)
    assert energy.solar_production_w is None
    assert energy.solar_fault is False


async def test_production_sensor_derives_grid_balance(hass) -> None:
    hass.states.async_set(SOLAR_ENTITY, "2000")
    hass.states.async_set(GRID_ENTITY, "1500")
    entry = _make_entry(hass)  # default type: production

    energy = (await SolarMinerCoordinator(hass, entry)._async_update_data()).energy

    assert energy.grid_net_w == pytest.approx(500.0)
    assert energy.available_for_miners_w == pytest.approx(500.0)


async def test_kw_readings_are_converted_to_w(hass) -> None:
    hass.states.async_set(SOLAR_ENTITY, "2.5", {"unit_of_measurement": "kW"})
    hass.states.async_set(GRID_ENTITY, "1.2", {"unit_of_measurement": "kW"})
    entry = _make_entry(hass)

    energy = (await SolarMinerCoordinator(hass, entry)._async_update_data()).energy

    assert energy.solar_production_w == pytest.approx(2500.0)
    assert energy.grid_consumption_w == pytest.approx(1200.0)


# ---------------------------------------------------------------------------
# Decision preview
# ---------------------------------------------------------------------------


async def test_update_attaches_decision_and_history(hass, add_hass_miner) -> None:
    hass.states.async_set(SOLAR_ENTITY, "2000")
    hass.states.async_set(GRID_ENTITY, "1500")
    entry = _make_entry(hass, options={CONF_PROFILE: "grid_independent"})
    add_hass_miner(MINER_IP, name="M1")

    coord = SolarMinerCoordinator(hass, entry)
    snapshot = await coord._async_update_data()

    assert snapshot.decision is not None
    assert snapshot.decision.trace[0] == "READ"
    assert len(snapshot.decision_history) == 1
    assert snapshot.decision_history[0]["summary"] == snapshot.decision.summary


async def test_history_only_records_changed_decisions(hass, add_hass_miner) -> None:
    hass.states.async_set(SOLAR_ENTITY, "2000")
    hass.states.async_set(GRID_ENTITY, "1500")
    entry = _make_entry(hass)
    add_hass_miner(MINER_IP)
    coord = SolarMinerCoordinator(hass, entry)

    await coord._async_update_data()
    await coord._async_update_data()  # same inputs → same summary
    hass.states.async_set(SOLAR_ENTITY, "3000")
    snapshot = await coord._async_update_data()

    assert len(snapshot.decision_history) == 2
    assert snapshot.decision_history[0]["summary"] == snapshot.decision.summary


# ---------------------------------------------------------------------------
# U11 — _async_apply_power_limit
# ---------------------------------------------------------------------------


def _make_miner_snapshot(
    *,
    min_power_w: float | None = 200.0,
    max_power_w: float | None = 1500.0,
    power_limit_entity_id: str | None = POWER_LIMIT_ENTITY,
) -> MinerSnapshot:
    return MinerSnapshot(
        miner_id=MINER_IP,
        ip=MINER_IP,
        power_w=600.0,
        power_limit_w=800.0,
        min_power_w=min_power_w,
        max_power_w=max_power_w,
        temperature_c=65.0,
        is_available=True,
        power_limit_entity_id=power_limit_entity_id,
    )


async def test_apply_power_limit_live_mode_calls_service(hass) -> None:
    entry = _make_entry(hass)
    coord = SolarMinerCoordinator(hass, entry)
    snapshot = _make_miner_snapshot()

    with patch(
        "homeassistant.core.ServiceRegistry.async_call", new_callable=AsyncMock
    ) as mock_call:
        await coord._async_apply_power_limit(snapshot, 600.0, dry_run=False)

    mock_call.assert_called_once_with(
        "number",
        "set_value",
        {"entity_id": POWER_LIMIT_ENTITY, "value": 600.0},
    )


async def test_apply_power_limit_dry_run_does_not_call_service(hass) -> None:
    entry = _make_entry(hass)
    coord = SolarMinerCoordinator(hass, entry)
    snapshot = _make_miner_snapshot()

    with patch(
        "homeassistant.core.ServiceRegistry.async_call", new_callable=AsyncMock
    ) as mock_call:
        await coord._async_apply_power_limit(snapshot, 600.0, dry_run=True)

    mock_call.assert_not_called()


async def test_apply_power_limit_clamps_below_min(hass) -> None:
    entry = _make_entry(hass)
    coord = SolarMinerCoordinator(hass, entry)
    snapshot = _make_miner_snapshot(min_power_w=200.0)

    with patch(
        "homeassistant.core.ServiceRegistry.async_call", new_callable=AsyncMock
    ) as mock_call:
        await coord._async_apply_power_limit(snapshot, 50.0, dry_run=False)

    mock_call.assert_called_once_with(
        "number", "set_value", {"entity_id": POWER_LIMIT_ENTITY, "value": 200.0}
    )


async def test_apply_power_limit_clamps_above_max(hass) -> None:
    entry = _make_entry(hass)
    coord = SolarMinerCoordinator(hass, entry)
    snapshot = _make_miner_snapshot(max_power_w=1500.0)

    with patch(
        "homeassistant.core.ServiceRegistry.async_call", new_callable=AsyncMock
    ) as mock_call:
        await coord._async_apply_power_limit(snapshot, 2000.0, dry_run=False)

    mock_call.assert_called_once_with(
        "number", "set_value", {"entity_id": POWER_LIMIT_ENTITY, "value": 1500.0}
    )


async def test_apply_power_limit_no_entity_id_skips_silently(hass) -> None:
    entry = _make_entry(hass)
    coord = SolarMinerCoordinator(hass, entry)
    snapshot = _make_miner_snapshot(power_limit_entity_id=None)

    with patch(
        "homeassistant.core.ServiceRegistry.async_call", new_callable=AsyncMock
    ) as mock_call:
        await coord._async_apply_power_limit(snapshot, 600.0, dry_run=False)

    mock_call.assert_not_called()


# ---------------------------------------------------------------------------
# Integration: full setup via hass.config_entries.async_setup
# ---------------------------------------------------------------------------


async def test_async_setup_entry_wires_coordinator(hass) -> None:
    """Full integration path: async_setup_entry creates coordinator and stores it in runtime_data."""
    hass.states.async_set(SOLAR_ENTITY, "2000")
    hass.states.async_set(GRID_ENTITY, "1500")
    entry = _make_entry(hass)

    await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    assert entry.runtime_data is not None
    assert isinstance(entry.runtime_data.data, CoordinatorSnapshot)
    assert entry.runtime_data.data.energy.solar_production_w == pytest.approx(2000.0)
    assert entry.runtime_data.data.miners == []


# ---------------------------------------------------------------------------
# U10 — miner power sum and mock consumption mode
# ---------------------------------------------------------------------------


async def test_miner_sum_computed_from_available_miners(hass, add_hass_miner) -> None:
    hass.states.async_set(SOLAR_ENTITY, "2000")
    hass.states.async_set(GRID_ENTITY, "1800")
    entry = _make_entry(hass)
    add_hass_miner(MINER_IP)  # 600 W
    add_hass_miner(MINER_IP_2)  # 600 W

    coord = SolarMinerCoordinator(hass, entry)
    snapshot = await coord._async_update_data()

    assert snapshot.energy.miner_consumption_sum_w == pytest.approx(1200.0)
    assert snapshot.energy.mock_consumption is False
    assert snapshot.energy.grid_consumption_w == pytest.approx(1800.0)  # unchanged


async def test_miner_sum_skips_unavailable_miners(hass, add_hass_miner) -> None:
    hass.states.async_set(SOLAR_ENTITY, "2000")
    hass.states.async_set(GRID_ENTITY, "1800")
    entry = _make_entry(hass)
    add_hass_miner(MINER_IP, power="unavailable")
    add_hass_miner(MINER_IP_2)  # 600 W

    coord = SolarMinerCoordinator(hass, entry)
    snapshot = await coord._async_update_data()

    assert snapshot.energy.miner_consumption_sum_w == pytest.approx(600.0)


async def test_miner_sum_is_none_when_all_miners_unavailable(hass, add_hass_miner) -> None:
    hass.states.async_set(SOLAR_ENTITY, "2000")
    hass.states.async_set(GRID_ENTITY, "1800")
    entry = _make_entry(hass)
    add_hass_miner(MINER_IP, power="unavailable")

    coord = SolarMinerCoordinator(hass, entry)
    snapshot = await coord._async_update_data()

    assert snapshot.energy.miner_consumption_sum_w is None


async def test_miner_sum_is_none_when_no_miners_configured(hass) -> None:
    hass.states.async_set(SOLAR_ENTITY, "2000")
    hass.states.async_set(GRID_ENTITY, "1800")
    entry = _make_entry(hass)

    coord = SolarMinerCoordinator(hass, entry)
    snapshot = await coord._async_update_data()

    assert snapshot.energy.miner_consumption_sum_w is None


async def test_mock_consumption_substitutes_grid_when_sum_available(hass, add_hass_miner) -> None:
    hass.states.async_set(SOLAR_ENTITY, "2000")
    hass.states.async_set(GRID_ENTITY, "1800")
    entry = _make_entry(hass, mock_consumption_enabled=True)
    add_hass_miner(MINER_IP)  # 600 W
    add_hass_miner(MINER_IP_2)  # 600 W

    coord = SolarMinerCoordinator(hass, entry)
    snapshot = await coord._async_update_data()

    assert snapshot.energy.miner_consumption_sum_w == pytest.approx(1200.0)
    assert snapshot.energy.grid_consumption_w == pytest.approx(1200.0)
    assert snapshot.energy.mock_consumption is True


async def test_mock_consumption_falls_back_when_sum_none(hass) -> None:
    hass.states.async_set(SOLAR_ENTITY, "2000")
    hass.states.async_set(GRID_ENTITY, "1800")
    entry = _make_entry(hass, mock_consumption_enabled=True)

    coord = SolarMinerCoordinator(hass, entry)
    snapshot = await coord._async_update_data()

    # sum is None → keep real grid entity value
    assert snapshot.energy.grid_consumption_w == pytest.approx(1800.0)
    assert snapshot.energy.mock_consumption is False
    assert snapshot.energy.miner_consumption_sum_w is None


async def test_mock_consumption_disabled_leaves_grid_unchanged(hass) -> None:
    hass.states.async_set(SOLAR_ENTITY, "2000")
    hass.states.async_set(GRID_ENTITY, "1800")
    entry = _make_entry(hass, mock_consumption_enabled=False)

    coord = SolarMinerCoordinator(hass, entry)
    snapshot = await coord._async_update_data()

    assert snapshot.energy.grid_consumption_w == pytest.approx(1800.0)
    assert snapshot.energy.mock_consumption is False


async def test_energy_snapshot_backward_compat_no_new_fields(hass) -> None:
    """EnergySnapshot(solar_production_w=...) still constructs without error."""
    from custom_components.solar_smart_miner.protocols import EnergySnapshot

    snap = EnergySnapshot(solar_production_w=2000.0)
    assert snap.miner_consumption_sum_w is None
    assert snap.mock_consumption is False


# ---------------------------------------------------------------------------
# U2: Hashrate and efficiency reads
# ---------------------------------------------------------------------------

async def test_coordinator_reads_hashrate_and_efficiency(hass, add_hass_miner) -> None:
    hass.states.async_set(SOLAR_ENTITY, "2000")
    hass.states.async_set(GRID_ENTITY, "1500")
    entry = _make_entry(hass)
    add_hass_miner(MINER_IP, hashrate="45.5", efficiency="21.3")

    coord = SolarMinerCoordinator(hass, entry)
    snapshot = await coord._async_update_data()

    miner = snapshot.miners[0]
    assert miner.hashrate_th == pytest.approx(45.5)
    assert miner.efficiency_jth == pytest.approx(21.3)


async def test_coordinator_hashrate_none_when_entity_absent(hass, add_hass_miner) -> None:
    hass.states.async_set(SOLAR_ENTITY, "2000")
    hass.states.async_set(GRID_ENTITY, "1500")
    entry = _make_entry(hass)
    add_hass_miner(MINER_IP)  # no hashrate/efficiency — only the decoys exist

    coord = SolarMinerCoordinator(hass, entry)
    snapshot = await coord._async_update_data()

    miner = snapshot.miners[0]
    assert miner.hashrate_th is None
    assert miner.efficiency_jth is None


async def test_coordinator_hashrate_none_when_state_unavailable(hass, add_hass_miner) -> None:
    hass.states.async_set(SOLAR_ENTITY, "2000")
    hass.states.async_set(GRID_ENTITY, "1500")
    entry = _make_entry(hass)
    add_hass_miner(MINER_IP, hashrate="unavailable")

    coord = SolarMinerCoordinator(hass, entry)
    snapshot = await coord._async_update_data()

    assert snapshot.miners[0].hashrate_th is None


async def test_coordinator_ignores_empty_duplicate_devices(hass, add_hass_miner) -> None:
    """The name comes from the device owning the miner's entities, not any device."""
    from homeassistant.helpers import device_registry as dr_module

    hass.states.async_set(SOLAR_ENTITY, "2000")
    entry = _make_entry(hass)
    miner_reg = add_hass_miner(MINER_IP, name="Real")
    dr = dr_module.async_get(hass)
    stale = dr.async_get_or_create(
        config_entry_id=miner_reg["entry"].entry_id,
        identifiers={("miner", "stale")},
        name="Stale duplicate",
    )
    dr.async_update_device(stale.id, name_by_user="Stale renamed")

    snapshot = await SolarMinerCoordinator(hass, entry)._async_update_data()

    assert snapshot.miners[0].name == "Real"


# ---------------------------------------------------------------------------
# AI advisor (advisory only)
# ---------------------------------------------------------------------------


def _ai_entry(hass, *, key: str | None = "sk-or-key", options: dict | None = None):
    from custom_components.solar_smart_miner.config_flow import CONF_OPENROUTER_KEY

    entry = _make_entry(hass, options=options)
    hass.config_entries.async_update_entry(entry, data={**entry.data, CONF_OPENROUTER_KEY: key})
    hass.states.async_set(SOLAR_ENTITY, "2000")
    return entry


async def _refresh(hass, coordinator):
    coordinator.data = await coordinator._async_update_data()
    await hass.async_block_till_done(wait_background_tasks=True)


async def test_ai_advice_requested_and_attached_to_snapshot(hass, mock_openrouter) -> None:
    coordinator = SolarMinerCoordinator(hass, _ai_entry(hass))

    await _refresh(hass, coordinator)

    mock_openrouter.assert_awaited_once()
    kwargs = mock_openrouter.await_args.kwargs
    assert kwargs["api_key"] == "sk-or-key"
    assert kwargs["model"] == "openrouter/free"  # default when none is stored
    assert "READ" in kwargs["messages"][1]["content"]
    assert coordinator.data.ai_advice.text == "Looks fine."
    # The next regular update carries the stored answer too.
    assert (await coordinator._async_update_data()).ai_advice.text == "Looks fine."


async def test_ai_uses_the_configured_model(hass, mock_openrouter) -> None:
    from custom_components.solar_smart_miner.config_flow import CONF_OPENROUTER_MODEL

    entry = _ai_entry(hass)
    hass.config_entries.async_update_entry(
        entry, data={**entry.data, CONF_OPENROUTER_MODEL: "google/gemma-4-31b-it:free"}
    )

    await _refresh(hass, SolarMinerCoordinator(hass, entry))

    assert mock_openrouter.await_args.kwargs["model"] == "google/gemma-4-31b-it:free"


async def test_ai_not_asked_again_within_interval(hass, mock_openrouter) -> None:
    coordinator = SolarMinerCoordinator(hass, _ai_entry(hass))

    await _refresh(hass, coordinator)
    await _refresh(hass, coordinator)

    assert mock_openrouter.await_count == 1


async def test_ai_asked_again_once_interval_has_passed(hass, mock_openrouter) -> None:
    import time

    from custom_components.solar_smart_miner.config_flow import CONF_AI_INTERVAL

    coordinator = SolarMinerCoordinator(hass, _ai_entry(hass, options={CONF_AI_INTERVAL: 120}))
    await _refresh(hass, coordinator)

    coordinator._ai_last_request = time.monotonic() - 121
    await _refresh(hass, coordinator)

    assert mock_openrouter.await_count == 2


async def test_ai_interval_has_a_floor(hass, mock_openrouter) -> None:
    import time

    from custom_components.solar_smart_miner.config_flow import CONF_AI_INTERVAL

    coordinator = SolarMinerCoordinator(hass, _ai_entry(hass, options={CONF_AI_INTERVAL: 1}))
    await _refresh(hass, coordinator)

    coordinator._ai_last_request = time.monotonic() - 30  # < 60 s minimum
    await _refresh(hass, coordinator)

    assert mock_openrouter.await_count == 1


async def test_ai_not_asked_without_key(hass, mock_openrouter) -> None:
    coordinator = SolarMinerCoordinator(hass, _ai_entry(hass, key=""))

    await _refresh(hass, coordinator)

    mock_openrouter.assert_not_awaited()
    assert coordinator.ai_enabled is False
    assert coordinator.data.ai_advice is None


async def test_ai_not_asked_when_disabled(hass, mock_openrouter) -> None:
    from custom_components.solar_smart_miner.config_flow import CONF_AI_ENABLED

    coordinator = SolarMinerCoordinator(hass, _ai_entry(hass, options={CONF_AI_ENABLED: False}))

    await _refresh(hass, coordinator)

    mock_openrouter.assert_not_awaited()


async def test_ai_failure_is_stored_not_raised(hass, mock_openrouter) -> None:
    from custom_components.solar_smart_miner.protocols import AiAdvice

    mock_openrouter.return_value = AiAdvice(
        text="", model="m", requested_at="t", latency_s=0.0, error="Rate limited by OpenRouter"
    )
    coordinator = SolarMinerCoordinator(hass, _ai_entry(hass))

    await _refresh(hass, coordinator)

    assert coordinator.data.ai_advice.error == "Rate limited by OpenRouter"
    # A failed request still counts toward the interval, so a bad key isn't retried every poll.
    await _refresh(hass, coordinator)
    assert mock_openrouter.await_count == 1


async def test_ai_answer_arriving_after_update_notifies_listeners(hass, mock_openrouter) -> None:
    import asyncio

    from custom_components.solar_smart_miner.protocols import AiAdvice

    async def slow_answer(*_, **__):
        await asyncio.sleep(0)  # a real request suspends; the answer lands after the update
        return AiAdvice(text="Slow answer.", model="m", requested_at="t", latency_s=0.0)

    mock_openrouter.side_effect = slow_answer
    coordinator = SolarMinerCoordinator(hass, _ai_entry(hass))
    coordinator.data = await coordinator._async_update_data()
    assert coordinator.data.ai_advice is None
    updates = []
    unsubscribe = coordinator.async_add_listener(
        lambda: updates.append(coordinator.data.ai_advice)
    )

    await hass.async_block_till_done(wait_background_tasks=True)
    unsubscribe()

    assert [a.text for a in updates] == ["Slow answer."]


async def test_ask_ai_now_bypasses_interval(hass, mock_openrouter) -> None:
    coordinator = SolarMinerCoordinator(hass, _ai_entry(hass))
    await _refresh(hass, coordinator)

    import time

    coordinator._ai_last_request = time.monotonic() - 11  # past the button cooldown
    coordinator.async_ask_ai_now()
    await hass.async_block_till_done(wait_background_tasks=True)

    assert mock_openrouter.await_count == 2


async def test_ask_ai_now_ignores_rapid_repeat_presses(hass, mock_openrouter) -> None:
    coordinator = SolarMinerCoordinator(hass, _ai_entry(hass))
    await _refresh(hass, coordinator)

    coordinator.async_ask_ai_now()
    await hass.async_block_till_done(wait_background_tasks=True)

    assert mock_openrouter.await_count == 1


async def test_ask_ai_now_explains_when_ai_is_off(hass) -> None:
    import pytest
    from homeassistant.exceptions import HomeAssistantError

    coordinator = SolarMinerCoordinator(hass, _ai_entry(hass, key=""))
    coordinator.data = await coordinator._async_update_data()

    with pytest.raises(HomeAssistantError, match="AI \\(OpenRouter\\)"):
        coordinator.async_ask_ai_now()


async def test_ai_never_changes_the_proposals(hass, mock_openrouter) -> None:
    """Advice is advisory: the rule-based decision is identical with or without AI."""
    with_ai = SolarMinerCoordinator(hass, _ai_entry(hass))
    without_ai = SolarMinerCoordinator(hass, _ai_entry(hass, key=""))

    await _refresh(hass, with_ai)
    await _refresh(hass, without_ai)

    assert with_ai.data.decision.proposals == without_ai.data.decision.proposals
    assert with_ai.data.decision.summary == without_ai.data.decision.summary


# --- solar production floor ---------------------------------------------------


async def test_negative_production_reading_is_floored_at_zero(hass) -> None:
    """Inverters report a small negative standby draw at night."""
    hass.states.async_set(SOLAR_ENTITY, "-10")
    entry = _make_entry(hass)

    snapshot = await SolarMinerCoordinator(hass, entry)._async_update_data()

    assert snapshot.energy.solar_production_w == 0.0
    assert snapshot.energy.solar_fault is False
