"""Tests for SolarMinerCoordinator — U11 (entity reads + power limit apply)."""
from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest
from homeassistant.helpers import device_registry as dr_module, entity_registry as er_module
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.solar_smart_miner.config_flow import (
    CONF_BATTERY_ENTITY,
    CONF_GRID_ENTITY,
    CONF_MINER_IP,
    CONF_MINER_NAME,
    CONF_MINERS,
    CONF_POLLING_INTERVAL,
    CONF_SOLAR_ENTITY,
)
from custom_components.solar_smart_miner.const import (
    CONF_MOCK_SOLAR_ENABLED,
    CONF_MOCK_SOLAR_ENTITY,
    DEFAULT_POLLING_INTERVAL,
    DOMAIN,
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


def _make_hass_miner_entry(hass):
    """Create a fake hass_miner config entry so we can register devices under it."""
    entry = MockConfigEntry(domain="hass_miner", data={}, version=1)
    entry.add_to_hass(hass)
    return entry


def _make_entry(
    hass,
    *,
    mock_enabled: bool = False,
    mock_entity: str | None = None,
    miners: list | None = None,
    battery_entity: str | None = None,
):
    entry = MockConfigEntry(
        domain=DOMAIN,
        data={
            CONF_SOLAR_ENTITY: SOLAR_ENTITY,
            CONF_GRID_ENTITY: GRID_ENTITY,
            CONF_BATTERY_ENTITY: battery_entity,
            CONF_MINERS: miners or [],
        },
        options={
            CONF_POLLING_INTERVAL: DEFAULT_POLLING_INTERVAL,
            CONF_MOCK_SOLAR_ENABLED: mock_enabled,
            CONF_MOCK_SOLAR_ENTITY: mock_entity,
        },
        version=1,
    )
    entry.add_to_hass(hass)
    return entry


def _register_miner_device(hass, miner_ip: str, config_entry_id: str):
    """Create a hass_miner device entry in the device registry."""
    dr = dr_module.async_get(hass)
    return dr.async_get_or_create(
        config_entry_id=config_entry_id,
        identifiers={("hass_miner", miner_ip)},
        name=f"Miner {miner_ip}",
    )


def _register_miner_entities(hass, device, miner_ip: str, *, power_available: bool = True):
    """Create hass_miner entity registry entries for a device."""
    er = er_module.async_get(hass)

    power_entry = er.async_get_or_create(
        "sensor",
        "hass_miner",
        f"{miner_ip}_power",
        device_id=device.id,
        original_device_class="power",
    )
    if power_available:
        hass.states.async_set(power_entry.entity_id, "600")
    else:
        hass.states.async_set(power_entry.entity_id, "unavailable")

    temp_entry = er.async_get_or_create(
        "sensor",
        "hass_miner",
        f"{miner_ip}_temperature",
        device_id=device.id,
        original_device_class="temperature",
    )
    hass.states.async_set(temp_entry.entity_id, "65")

    limit_entry = er.async_get_or_create(
        "number",
        "hass_miner",
        f"{miner_ip}_power_limit",
        device_id=device.id,
    )
    hass.states.async_set(
        limit_entry.entity_id, "800", {"min": 200.0, "max": 1500.0}
    )

    return power_entry, temp_entry, limit_entry


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


async def test_coordinator_reads_miner_entities(hass) -> None:
    hass.states.async_set(SOLAR_ENTITY, "2000")
    hass.states.async_set(GRID_ENTITY, "1500")
    entry = _make_entry(hass, miners=[{CONF_MINER_NAME: "Test", CONF_MINER_IP: MINER_IP}])
    hm_entry = _make_hass_miner_entry(hass)

    device = _register_miner_device(hass, MINER_IP, hm_entry.entry_id)
    _, _, limit_entry = _register_miner_entities(hass, device, MINER_IP)

    coord = SolarMinerCoordinator(hass, entry)
    snapshot = await coord._async_update_data()

    assert len(snapshot.miners) == 1
    miner = snapshot.miners[0]
    assert miner.is_available is True
    assert miner.power_w == pytest.approx(600.0)
    assert miner.temperature_c == pytest.approx(65.0)
    assert miner.power_limit_w == pytest.approx(800.0)
    assert miner.min_power_w == pytest.approx(200.0)
    assert miner.max_power_w == pytest.approx(1500.0)
    assert miner.power_limit_entity_id == limit_entry.entity_id
    assert miner.ip == MINER_IP


async def test_coordinator_reads_two_miners(hass) -> None:
    hass.states.async_set(SOLAR_ENTITY, "2000")
    hass.states.async_set(GRID_ENTITY, "1500")
    entry = _make_entry(
        hass,
        miners=[
            {CONF_MINER_NAME: "Miner1", CONF_MINER_IP: MINER_IP},
            {CONF_MINER_NAME: "Miner2", CONF_MINER_IP: MINER_IP_2},
        ],
    )
    hm_entry = _make_hass_miner_entry(hass)

    device1 = _register_miner_device(hass, MINER_IP, hm_entry.entry_id)
    _register_miner_entities(hass, device1, MINER_IP)
    device2 = _register_miner_device(hass, MINER_IP_2, hm_entry.entry_id)
    _register_miner_entities(hass, device2, MINER_IP_2)

    coord = SolarMinerCoordinator(hass, entry)
    snapshot = await coord._async_update_data()

    assert len(snapshot.miners) == 2
    assert all(m.is_available for m in snapshot.miners)


async def test_coordinator_miner_no_device_found_marks_unavailable(hass) -> None:
    hass.states.async_set(SOLAR_ENTITY, "2000")
    hass.states.async_set(GRID_ENTITY, "1500")
    entry = _make_entry(hass, miners=[{CONF_MINER_NAME: "Ghost", CONF_MINER_IP: MINER_IP}])
    # No device registered in device registry
    coord = SolarMinerCoordinator(hass, entry)
    snapshot = await coord._async_update_data()

    assert len(snapshot.miners) == 1
    assert snapshot.miners[0].is_available is False


async def test_coordinator_miner_power_unavailable_marks_unavailable(hass) -> None:
    hass.states.async_set(SOLAR_ENTITY, "2000")
    hass.states.async_set(GRID_ENTITY, "1500")
    entry = _make_entry(hass, miners=[{CONF_MINER_NAME: "Test", CONF_MINER_IP: MINER_IP}])
    hm_entry = _make_hass_miner_entry(hass)

    device = _register_miner_device(hass, MINER_IP, hm_entry.entry_id)
    _register_miner_entities(hass, device, MINER_IP, power_available=False)

    coord = SolarMinerCoordinator(hass, entry)
    snapshot = await coord._async_update_data()

    assert snapshot.miners[0].is_available is False


async def test_coordinator_miner_power_unavailable_does_not_affect_other(hass) -> None:
    hass.states.async_set(SOLAR_ENTITY, "2000")
    hass.states.async_set(GRID_ENTITY, "1500")
    entry = _make_entry(
        hass,
        miners=[
            {CONF_MINER_NAME: "Bad", CONF_MINER_IP: MINER_IP},
            {CONF_MINER_NAME: "Good", CONF_MINER_IP: MINER_IP_2},
        ],
    )
    hm_entry = _make_hass_miner_entry(hass)

    device1 = _register_miner_device(hass, MINER_IP, hm_entry.entry_id)
    _register_miner_entities(hass, device1, MINER_IP, power_available=False)
    device2 = _register_miner_device(hass, MINER_IP_2, hm_entry.entry_id)
    _register_miner_entities(hass, device2, MINER_IP_2, power_available=True)

    coord = SolarMinerCoordinator(hass, entry)
    snapshot = await coord._async_update_data()

    assert snapshot.miners[0].is_available is False
    assert snapshot.miners[1].is_available is True


async def test_coordinator_no_miners_configured(hass) -> None:
    hass.states.async_set(SOLAR_ENTITY, "2000")
    hass.states.async_set(GRID_ENTITY, "1500")
    entry = _make_entry(hass, miners=[])
    coord = SolarMinerCoordinator(hass, entry)
    snapshot = await coord._async_update_data()

    assert snapshot.miners == []


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
