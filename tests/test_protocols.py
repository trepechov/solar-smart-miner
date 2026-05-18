"""Tests for data classes in protocols.py — U11."""
from __future__ import annotations

import pytest

from custom_components.solar_smart_miner.protocols import (
    CoordinatorSnapshot,
    EnergySnapshot,
    MinerSnapshot,
)


def test_energy_snapshot_defaults() -> None:
    snapshot = EnergySnapshot(solar_production_w=2000.0)
    assert snapshot.grid_consumption_w is None
    assert snapshot.battery_soc_pct is None
    assert snapshot.solar_fault is False
    assert snapshot.mock_solar is False


def test_energy_snapshot_backward_compat_positional() -> None:
    snapshot = EnergySnapshot(solar_production_w=1500.0)
    assert snapshot.solar_production_w == pytest.approx(1500.0)


def test_energy_snapshot_none_solar() -> None:
    snapshot = EnergySnapshot(solar_production_w=None, solar_fault=True)
    assert snapshot.solar_production_w is None
    assert snapshot.solar_fault is True


def test_miner_snapshot_construction() -> None:
    miner = MinerSnapshot(
        miner_id="192.168.1.100",
        ip="192.168.1.100",
        power_w=600.0,
        power_limit_w=800.0,
        min_power_w=200.0,
        max_power_w=1500.0,
        temperature_c=65.0,
        is_available=True,
        power_limit_entity_id="number.miner_power_limit",
    )
    assert miner.is_available is True
    assert miner.power_w == pytest.approx(600.0)
    assert miner.temperature_c == pytest.approx(65.0)
    assert miner.min_power_w == pytest.approx(200.0)
    assert miner.max_power_w == pytest.approx(1500.0)


def test_miner_snapshot_unavailable() -> None:
    miner = MinerSnapshot(
        miner_id="192.168.1.100",
        ip="192.168.1.100",
        power_w=None,
        power_limit_w=None,
        min_power_w=None,
        max_power_w=None,
        temperature_c=None,
        is_available=False,
        power_limit_entity_id=None,
    )
    assert miner.is_available is False
    assert miner.power_limit_entity_id is None


def test_coordinator_snapshot_construction() -> None:
    energy = EnergySnapshot(solar_production_w=2000.0, grid_consumption_w=1500.0)
    snapshot = CoordinatorSnapshot(energy=energy, miners=[])
    assert snapshot.energy.solar_production_w == pytest.approx(2000.0)
    assert snapshot.energy.grid_consumption_w == pytest.approx(1500.0)
    assert snapshot.miners == []


def test_coordinator_snapshot_default_miners() -> None:
    energy = EnergySnapshot(solar_production_w=0.0)
    snapshot = CoordinatorSnapshot(energy=energy)
    assert snapshot.miners == []
