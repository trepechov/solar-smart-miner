"""Tests for SolarMinerCoordinator — U11 (entity reads + power limit apply) and U10 (miner sum + mock consumption)."""
from __future__ import annotations

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
)

SOLAR_ENTITY = "sensor.solar_power"
GRID_ENTITY = "sensor.grid_consumption"
FORECAST_ENTITY = "sensor.forecast_solar_power_production_now"
BATTERY_ENTITY = "sensor.battery_soc"
MINER_IP = "192.168.1.100"
MINER_IP_2 = "192.168.1.101"


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

    coordinator._ai_last_request = time.monotonic() - 5  # < 10 s minimum
    await _refresh(hass, coordinator)

    assert mock_openrouter.await_count == 1


async def test_ai_default_interval_is_a_minute(hass, mock_openrouter) -> None:
    import time

    coordinator = SolarMinerCoordinator(hass, _ai_entry(hass))
    await _refresh(hass, coordinator)

    coordinator._ai_last_request = time.monotonic() - 30
    await _refresh(hass, coordinator)
    assert mock_openrouter.await_count == 1

    coordinator._ai_last_request = time.monotonic() - 61
    await _refresh(hass, coordinator)
    assert mock_openrouter.await_count == 2


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


async def test_ai_gets_the_knowledge_for_the_moment_and_the_log_names_it(hass, mock_openrouter) -> None:
    import json

    hass.states.async_set("sun.sun", "above_horizon", {"elevation": 5.0, "rising": False})
    coordinator = SolarMinerCoordinator(hass, _ai_entry(hass))
    coordinator.transition.sunset = True  # production has been falling (transition.py)
    await coordinator.async_load_knowledge()

    await _refresh(hass, coordinator)

    system = mock_openrouter.await_args.kwargs["messages"][0]["content"]
    assert "KNOWLEDGE BASE (situation: sunset)" in system
    assert "Move between fixed steps" in system  # a P1 rule
    assert "What the sunset looked like" in system  # a sunset-only fact
    entry = json.loads(coordinator.ai_log.path.read_text().splitlines()[0])
    assert entry["knowledge"]["situation"] == "sunset"
    assert "rule.power-steps" in entry["knowledge"]["facts"]


async def test_a_broken_knowledge_base_leaves_the_ai_working(hass, mock_openrouter) -> None:
    from unittest.mock import patch

    import yaml

    coordinator = SolarMinerCoordinator(hass, _ai_entry(hass))
    with patch(
        "custom_components.solar_smart_miner.coordinator.load_facts",
        side_effect=yaml.YAMLError("bad"),
    ):
        await coordinator.async_load_knowledge()

    await _refresh(hass, coordinator)

    assert coordinator.knowledge == []
    assert "KNOWLEDGE BASE" not in mock_openrouter.await_args.kwargs["messages"][0]["content"]


async def test_setup_loads_the_knowledge_base(hass) -> None:
    hass.states.async_set(SOLAR_ENTITY, "2000")
    entry = _make_entry(hass)

    await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    assert any(f.id == "rule.power-steps" for f in entry.runtime_data.knowledge)


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


# ---------------------------------------------------------------------------
# AI decision log + reference sensors (PV actual, forecast)
# ---------------------------------------------------------------------------


async def test_every_ai_answer_is_logged_with_inputs_and_answer(hass, mock_openrouter) -> None:
    import json

    from custom_components.solar_smart_miner.protocols import AiAdvice

    mock_openrouter.return_value = AiAdvice(
        text="Sun is setting | Brod1: reduce (not_enough_energy)",
        model="m/x",
        requested_at="t",
        latency_s=0.5,
        summary="Sun is setting",
        actions=[{"miner": "Brod1", "action": "reduce", "reason": "not_enough_energy", "note": ""}],
        raw="{}",
    )
    coordinator = SolarMinerCoordinator(hass, _ai_entry(hass))

    await _refresh(hass, coordinator)

    entry = json.loads(coordinator.ai_log.path.read_text().splitlines()[0])
    assert entry["ai"]["actions"][0]["reason"] == "not_enough_energy"
    assert entry["model"] == "m/x"
    assert entry["inputs"]["solar_w"] == 2000.0
    assert entry["rules"]["summary"]
    assert "READ" in entry["prompt"]
    assert coordinator.ai_log.history[0]["summary"] == "Sun is setting"


async def test_failed_ai_requests_are_logged_too(hass, mock_openrouter) -> None:
    import json

    from custom_components.solar_smart_miner.protocols import AiAdvice

    mock_openrouter.return_value = AiAdvice(
        text="", model="m", requested_at="t", latency_s=0.1, error="Rate limited by OpenRouter"
    )
    coordinator = SolarMinerCoordinator(hass, _ai_entry(hass))

    await _refresh(hass, coordinator)

    entry = json.loads(coordinator.ai_log.path.read_text().splitlines()[0])
    assert entry["error"] == "Rate limited by OpenRouter"
    assert coordinator.ai_log.history[0]["error"] == "Rate limited by OpenRouter"


async def test_nothing_is_logged_when_ai_is_off(hass, mock_openrouter) -> None:
    coordinator = SolarMinerCoordinator(hass, _ai_entry(hass, key=""))

    await _refresh(hass, coordinator)

    assert not coordinator.ai_log.path.exists()


async def test_reference_sensors_are_read_into_the_snapshot(hass) -> None:
    from custom_components.solar_smart_miner.config_flow import (
        CONF_FORECAST_NEXT_HOUR_ENTITY,
        CONF_FORECAST_NOW_ENTITY,
        CONF_FORECAST_REMAINING_ENTITY,
        CONF_PV_ENTITY,
    )

    hass.states.async_set(SOLAR_ENTITY, "2000")
    hass.states.async_set("sensor.pv", "3.9", {"unit_of_measurement": "kW"})
    hass.states.async_set("sensor.fc_now", "9888", {"unit_of_measurement": "W"})
    hass.states.async_set("sensor.fc_next", "9039")
    hass.states.async_set("sensor.fc_left", "28037", {"unit_of_measurement": "Wh"})
    entry = _make_entry(hass)
    hass.config_entries.async_update_entry(
        entry,
        data={
            **entry.data,
            CONF_PV_ENTITY: "sensor.pv",
            CONF_FORECAST_NOW_ENTITY: "sensor.fc_now",
            CONF_FORECAST_NEXT_HOUR_ENTITY: "sensor.fc_next",
            CONF_FORECAST_REMAINING_ENTITY: "sensor.fc_left",
        },
    )

    energy = (await SolarMinerCoordinator(hass, entry)._async_update_data()).energy

    assert energy.pv_power_w == pytest.approx(3900.0)  # kW converted
    assert energy.forecast_now_w == pytest.approx(9888.0)
    assert energy.forecast_next_hour_w == pytest.approx(9039.0)
    assert energy.forecast_remaining_kwh == pytest.approx(28.037)  # Wh converted


async def test_reference_sensors_are_optional_and_tolerate_unavailable(hass) -> None:
    from custom_components.solar_smart_miner.config_flow import CONF_PV_ENTITY

    hass.states.async_set(SOLAR_ENTITY, "2000")
    hass.states.async_set("sensor.pv", "unavailable")
    entry = _make_entry(hass)

    energy = (await SolarMinerCoordinator(hass, entry)._async_update_data()).energy
    assert energy.pv_power_w is None and energy.forecast_now_w is None

    hass.config_entries.async_update_entry(entry, data={**entry.data, CONF_PV_ENTITY: "sensor.pv"})
    energy = (await SolarMinerCoordinator(hass, entry)._async_update_data()).energy
    assert energy.pv_power_w is None


async def test_reference_sensors_reach_the_ai_prompt_but_not_the_rules(hass, mock_openrouter) -> None:
    from custom_components.solar_smart_miner.config_flow import CONF_FORECAST_NOW_ENTITY

    entry = _ai_entry(hass)
    hass.states.async_set("sensor.fc_now", "9888")
    base = SolarMinerCoordinator(hass, entry)
    await _refresh(hass, base)
    plain = base.data.decision.proposals
    hass.config_entries.async_update_entry(
        entry, data={**entry.data, CONF_FORECAST_NOW_ENTITY: "sensor.fc_now"}
    )
    coordinator = SolarMinerCoordinator(hass, entry)

    await _refresh(hass, coordinator)

    assert "Forecast PV now: 9,888 W" in mock_openrouter.await_args.kwargs["messages"][1]["content"]
    assert coordinator.data.decision.proposals == plain


# ---------------------------------------------------------------------------
# Stop / start (pause switch or relay), tuning, power steps
# ---------------------------------------------------------------------------


async def _update(hass, entry):
    hass.states.async_set(SOLAR_ENTITY, "2000")
    hass.states.async_set(GRID_ENTITY, "1500")
    return await SolarMinerCoordinator(hass, entry)._async_update_data()


async def test_miner_exposes_its_pause_switch(hass, add_hass_miner) -> None:
    reg = add_hass_miner(MINER_IP, active="on")

    miner = (await _update(hass, _make_entry(hass))).miners[0]

    assert miner.switch_entity_id == reg["active"].entity_id
    assert miner.is_stopped is False
    assert miner.relay_entity_id is None


async def test_a_paused_miner_is_stopped_not_unavailable(hass, add_hass_miner, caplog) -> None:
    add_hass_miner(MINER_IP, power="unavailable", active="off")

    miner = (await _update(hass, _make_entry(hass))).miners[0]

    assert miner.is_stopped is True
    assert miner.is_available is False
    assert "power entity unavailable" not in caplog.text


async def test_an_unreachable_miner_is_not_stopped(hass, add_hass_miner, caplog) -> None:
    add_hass_miner(MINER_IP, power="unavailable", active="on")

    miner = (await _update(hass, _make_entry(hass))).miners[0]

    assert miner.is_stopped is False
    assert "power entity unavailable" in caplog.text


async def test_a_relay_that_is_off_means_stopped(hass, add_hass_miner) -> None:
    from custom_components.solar_smart_miner.config_flow import CONF_MINER_RELAYS

    add_hass_miner(MINER_IP, power="unavailable", active="unavailable")
    hass.states.async_set("switch.relay_1", "off")
    entry = _make_entry(hass, options={CONF_MINER_RELAYS: {MINER_IP: "switch.relay_1"}})

    miner = (await _update(hass, entry)).miners[0]

    assert miner.relay_entity_id == "switch.relay_1"
    assert miner.is_stopped is True


async def test_a_relay_that_is_on_does_not_mean_stopped(hass, add_hass_miner) -> None:
    from custom_components.solar_smart_miner.config_flow import CONF_MINER_RELAYS

    add_hass_miner(MINER_IP, active="on")
    hass.states.async_set("switch.relay_1", "on")
    entry = _make_entry(hass, options={CONF_MINER_RELAYS: {MINER_IP: "switch.relay_1"}})

    assert (await _update(hass, entry)).miners[0].is_stopped is False


async def test_limit_change_time_is_unknown_until_a_change_is_seen(hass, add_hass_miner, monkeypatch) -> None:
    import time as time_module

    now = [1000.0]
    monkeypatch.setattr(time_module, "monotonic", lambda: now[0])
    reg = add_hass_miner(MINER_IP, limit="1300")
    hass.states.async_set(SOLAR_ENTITY, "2000")
    hass.states.async_set(GRID_ENTITY, "1500")
    coordinator = SolarMinerCoordinator(hass, _make_entry(hass))

    assert (await coordinator._async_update_data()).miners[0].minutes_since_limit_change is None

    now[0] += 600  # unchanged limit: still unknown
    assert (await coordinator._async_update_data()).miners[0].minutes_since_limit_change is None

    hass.states.async_set(reg["power_limit"].entity_id, "1100", {"min": 500.0, "max": 3500.0})
    assert (await coordinator._async_update_data()).miners[0].minutes_since_limit_change == 0

    now[0] += 15 * 60
    assert (await coordinator._async_update_data()).miners[0].minutes_since_limit_change == pytest.approx(15)


async def test_applied_limit_counts_as_a_change_from_the_next_cycle(hass, add_hass_miner, monkeypatch) -> None:
    import time as time_module

    from pytest_homeassistant_custom_component.common import async_mock_service

    from custom_components.solar_smart_miner.const import CONF_CONTROL_MODE

    now = [1000.0]
    monkeypatch.setattr(time_module, "monotonic", lambda: now[0])
    add_hass_miner(MINER_IP, limit="1100", power="1100", temperature="55", limit_attrs={"min": 500.0, "max": 3500.0})
    hass.states.async_set(SOLAR_ENTITY, "5000")
    hass.states.async_set(GRID_ENTITY, "1500")
    calls = async_mock_service(hass, "number", "set_value")
    coordinator = SolarMinerCoordinator(hass, _make_entry(hass, options={CONF_CONTROL_MODE: "manual"}))
    snapshot = await coordinator._async_update_data()
    miner, plan = snapshot.miners[0], next(iter(snapshot.decision.plans.values()))
    assert snapshot.miners[0].minutes_since_limit_change is None  # settled / unknown
    assert plan.action == "set_limit"

    result = await coordinator.controller.async_apply(
        miner, plan, trigger="manual", steps=coordinator._power_steps()
    )
    assert result.status == "pending"
    assert len(calls) == 1
    now[0] += 120

    snapshot = await coordinator._async_update_data()
    # The number still reads the old limit, yet the miner already counts as changed.
    assert snapshot.miners[0].minutes_since_limit_change == pytest.approx(2)
    assert next(iter(snapshot.decision.plans.values())).action != "set_limit"


async def test_power_steps_and_options_drive_the_decision(hass, add_hass_miner) -> None:
    from custom_components.solar_smart_miner.config_flow import (
        CONF_IMPORT_MAX,
        CONF_IMPORT_MIN,
        CONF_POWER_STEPS,
        CONF_TEMP_TARGET,
    )

    add_hass_miner(MINER_IP, limit="700", power="690", limit_attrs={"min": 500.0, "max": 3500.0})
    # The miner reads 65 °C: at the default target it would hold, under a 70 °C target it may step up.
    options = {CONF_POWER_STEPS: [700, 1000], CONF_TEMP_TARGET: 70,
               CONF_IMPORT_MIN: 250, CONF_IMPORT_MAX: 600}
    entry = _make_entry(hass, options=options)

    # Plenty of budget (the 1,500 W grid sensor is the house, miners included).
    hass.states.async_set(SOLAR_ENTITY, "5000")
    hass.states.async_set(GRID_ENTITY, "1500")
    decision = (await SolarMinerCoordinator(hass, entry)._async_update_data()).decision

    assert list(decision.proposals.values()) == [1000.0]  # only the configured steps
    assert any("Power steps: 700, 1,000 W" in line for line in decision.trace)
    assert "Grid import range: 250 W to 600 W" in decision.trace


async def test_a_sent_command_holds_the_whole_farm_for_the_ramp_lock(hass, add_hass_miner, monkeypatch) -> None:
    import time as time_module

    from pytest_homeassistant_custom_component.common import async_mock_service

    from custom_components.solar_smart_miner.const import CONF_CONTROL_MODE

    now = [1000.0]
    monkeypatch.setattr(time_module, "monotonic", lambda: now[0])
    reg = add_hass_miner(MINER_IP, limit="900", power="900", temperature="55",
                         limit_attrs={"min": 500.0, "max": 3500.0})
    add_hass_miner(MINER_IP_2, limit="900", power="900", temperature="55",
                   limit_attrs={"min": 500.0, "max": 3500.0})
    hass.states.async_set(SOLAR_ENTITY, "9000")
    hass.states.async_set(GRID_ENTITY, "1800")
    async_mock_service(hass, "number", "set_value")
    coordinator = SolarMinerCoordinator(hass, _make_entry(hass, options={CONF_CONTROL_MODE: "manual"}))
    snapshot = await coordinator._async_update_data()
    assert len(snapshot.decision.proposals) == 1  # one miner per proposal

    miner = next(m for m in snapshot.miners if m.miner_id in snapshot.decision.proposals)
    await coordinator.controller.async_apply(
        miner, snapshot.decision.plans[miner.miner_id], trigger="manual", steps=coordinator._power_steps()
    )
    # Still being checked: everything holds, the other miner too.
    snapshot = await coordinator._async_update_data()
    assert snapshot.decision.summary.startswith("Waiting for a miner to restart")

    # The miner shows its new limit: checked, but the ramp lock runs from the change.
    hass.states.async_set(reg["power_limit"].entity_id, "1500", {"min": 500.0, "max": 3500.0})
    now[0] += 60
    snapshot = await coordinator._async_update_data()
    assert snapshot.decision.proposals == {}
    assert snapshot.decision.summary.startswith("Waiting for a miner to restart")

    now[0] += 4 * 60
    snapshot = await coordinator._async_update_data()
    assert not snapshot.decision.summary.startswith("Waiting")


async def test_a_miner_stopped_or_started_by_hand_starts_the_ramp_lock(hass, add_hass_miner, monkeypatch) -> None:
    import time as time_module

    now = [1000.0]
    monkeypatch.setattr(time_module, "monotonic", lambda: now[0])
    reg = add_hass_miner(MINER_IP, limit="900", power="900", temperature="55", active="on",
                         limit_attrs={"min": 500.0, "max": 3500.0})
    hass.states.async_set(SOLAR_ENTITY, "9000")
    hass.states.async_set(GRID_ENTITY, "900")
    coordinator = SolarMinerCoordinator(hass, _make_entry(hass))
    assert not (await coordinator._async_update_data()).decision.summary.startswith("Waiting")

    hass.states.async_set(reg["active"].entity_id, "off")
    now[0] += 30
    snapshot = await coordinator._async_update_data()
    assert snapshot.decision.summary.startswith("Waiting for a miner to restart")


async def test_a_paused_farm_starts_a_miner_below_the_import_floor_only_with_the_sun_up(
    hass, add_hass_miner
) -> None:
    # 2026-10-08: everything paused, meter at 0 W for two hours, nothing started.
    add_hass_miner(MINER_IP, limit="900", power="0", active="off", limit_attrs={"min": 500.0, "max": 3500.0})
    hass.states.async_set(SOLAR_ENTITY, "500")
    hass.states.async_set(GRID_ENTITY, "500")  # the house: the meter reads 0 W
    coordinator = SolarMinerCoordinator(hass, _make_entry(hass))

    hass.states.async_set("sun.sun", "below_horizon", {"elevation": -4.0, "rising": True})
    plan = next(iter((await coordinator._async_update_data()).decision.plans.values()))
    assert plan.action == "hold"

    hass.states.async_set("sun.sun", "above_horizon", {"elevation": 6.0, "rising": True})
    plan = next(iter((await coordinator._async_update_data()).decision.plans.values()))
    assert (plan.action, plan.limit_w) == ("start", 900.0)


async def test_step_down_waits_for_the_import_to_stay_above_the_band(
    hass, add_hass_miner, monkeypatch
) -> None:
    import time as time_module

    from custom_components.solar_smart_miner.config_flow import CONF_STEP_DOWN_DELAY

    now = [1000.0]
    monkeypatch.setattr(time_module, "monotonic", lambda: now[0])
    add_hass_miner(MINER_IP, limit="1500", power="1500", temperature="55",
                   limit_attrs={"min": 500.0, "max": 3500.0})
    hass.states.async_set(SOLAR_ENTITY, "600")
    hass.states.async_set(GRID_ENTITY, "1500")  # importing 900 W
    coordinator = SolarMinerCoordinator(hass, _make_entry(hass, options={CONF_STEP_DOWN_DELAY: 5}))

    first = (await coordinator._async_update_data()).decision
    assert next(iter(first.plans.values())).action == "hold"
    assert any("steps down after 5 min" in line for line in first.trace)

    now[0] += 6 * 60
    plan = next(iter((await coordinator._async_update_data()).decision.plans.values()))
    assert plan.action in ("set_limit", "stop")

    # The import back inside the band resets the clock.
    hass.states.async_set(SOLAR_ENTITY, "1200")
    await coordinator._async_update_data()
    hass.states.async_set(SOLAR_ENTITY, "600")
    plan = next(iter((await coordinator._async_update_data()).decision.plans.values()))
    assert plan.action == "hold"


async def test_ramp_lock_ends_early_once_the_changed_miner_draws_its_new_power(
    hass, add_hass_miner, monkeypatch
) -> None:
    # Owner, 2026-10-08: once the changed miner's draw is close to its limit, the next miner
    # may change, even while its hashrate is still settling.
    import time as time_module

    now = [1000.0]
    monkeypatch.setattr(time_module, "monotonic", lambda: now[0])
    miner = add_hass_miner(MINER_IP, limit="1300", power="1290", temperature="55",
                           limit_attrs={"min": 500.0, "max": 3500.0})
    hass.states.async_set(SOLAR_ENTITY, "5000")
    hass.states.async_set(GRID_ENTITY, "1500")
    coordinator = SolarMinerCoordinator(hass, _make_entry(hass))
    snapshot = (await coordinator._async_update_data()).miners[0]
    hass.states.async_set(miner["miner_consumption"].entity_id, "0")  # it restarts ...
    now[0] -= 30
    coordinator.controller.note_change(snapshot, stopping=False, restarting=True)  # changed 30 s ago
    now[0] += 30
    assert (await coordinator._async_update_data()).decision.summary.startswith("Waiting for a miner")

    now[0] += 60  # 90 s after the change, back and drawing 1,290 of 1,300 W
    hass.states.async_set(miner["miner_consumption"].entity_id, "1290")
    decision = (await coordinator._async_update_data()).decision
    assert not decision.summary.startswith("Waiting for a miner")
    assert any("already at the new power" in line for line in decision.trace)

    # Still far below its limit: it is ramping, the farm waits the full 4 minutes.
    hass.states.async_set(miner["miner_consumption"].entity_id, "300")
    assert (await coordinator._async_update_data()).decision.summary.startswith("Waiting for a miner")
    now[0] += 3 * 60
    assert not (await coordinator._async_update_data()).decision.summary.startswith("Waiting for a miner")


async def test_a_miner_restarting_after_a_limit_change_is_not_read_as_stopped(
    hass, add_hass_miner, monkeypatch
) -> None:
    # 2026-10-08 13:09: Brod3 restarting at 1,500 W showed its pause switch off for a minute
    # or two; read as stopped, the ramp lock ended and the plan "start at 900 W" undid the step.
    import time as time_module

    now = [1000.0]
    monkeypatch.setattr(time_module, "monotonic", lambda: now[0])
    reg = add_hass_miner(MINER_IP, limit="1300", power="1300", temperature="55", active="on",
                         limit_attrs={"min": 500.0, "max": 3500.0})
    hass.states.async_set(SOLAR_ENTITY, "9000")
    hass.states.async_set(GRID_ENTITY, "1300")
    coordinator = SolarMinerCoordinator(hass, _make_entry(hass))
    await coordinator._async_update_data()

    hass.states.async_set(reg["power_limit"].entity_id, "1500", {"min": 500.0, "max": 3500.0})
    await coordinator._async_update_data()
    now[0] += 60
    hass.states.async_set(reg["active"].entity_id, "off")
    hass.states.async_set(reg["miner_consumption"].entity_id, "0")
    now[0] += 60

    snapshot = await coordinator._async_update_data()
    assert snapshot.miners[0].is_stopped is False
    assert snapshot.decision.summary.startswith("Waiting for a miner to restart")
    assert all(plan.action != "start" for plan in snapshot.decision.plans.values())

    # Still off well after the restart: it really is stopped.
    now[0] += 5 * 60
    assert (await coordinator._async_update_data()).miners[0].is_stopped is True


async def test_a_stopped_miner_given_a_limit_stays_stopped(hass, add_hass_miner, monkeypatch) -> None:
    # A start sets the limit first: until the switch turns on, the miner is still stopped.
    import time as time_module

    now = [1000.0]
    monkeypatch.setattr(time_module, "monotonic", lambda: now[0])
    reg = add_hass_miner(MINER_IP, limit="1300", power="0", active="off",
                         limit_attrs={"min": 500.0, "max": 3500.0})
    hass.states.async_set(SOLAR_ENTITY, "500")
    hass.states.async_set(GRID_ENTITY, "500")
    coordinator = SolarMinerCoordinator(hass, _make_entry(hass))
    await coordinator._async_update_data()

    hass.states.async_set(reg["power_limit"].entity_id, "900", {"min": 500.0, "max": 3500.0})
    now[0] += 30
    assert (await coordinator._async_update_data()).miners[0].is_stopped is True


async def test_production_is_read_for_sunset_only_outside_a_ramp_lock_and_while_importing(hass) -> None:
    from custom_components.solar_smart_miner.protocols import EnergySnapshot

    coordinator = SolarMinerCoordinator(hass, _make_entry(hass))
    seen: list = []
    coordinator.transition.update = lambda now, production, **sun: seen.append(production)
    sun = type("Sun", (), {"state": "above_horizon", "attributes": {"rising": False}})()

    importing = EnergySnapshot(solar_production_w=None, grid_net_w=-300.0, miner_consumption_sum_w=2000.0)
    exporting = EnergySnapshot(solar_production_w=None, grid_net_w=50.0, miner_consumption_sum_w=2000.0)
    coordinator._update_transition(importing, None, sun)
    coordinator._update_transition(importing, 2.0, sun)  # a miner is restarting
    coordinator._update_transition(exporting, None, sun)  # throttled or exporting: no reading

    assert seen == [1700.0, None, None]


async def test_a_reload_during_sunset_stays_sunset(hass) -> None:
    hass.states.async_set("sun.sun", "above_horizon", {"rising": False})
    first = SolarMinerCoordinator(hass, _make_entry(hass))
    await first.decision_log.async_write({"inputs": {"sunset": True}})

    again = SolarMinerCoordinator(hass, _make_entry(hass))
    await again.async_seed_transition()
    assert again.transition.sunset

    hass.states.async_set("sun.sun", "below_horizon", {"rising": True})  # the next morning
    fresh = SolarMinerCoordinator(hass, _make_entry(hass))
    await fresh.async_seed_transition()
    assert not fresh.transition.sunset
