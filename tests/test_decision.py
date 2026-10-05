"""Tests for the rule-based decision preview."""
from __future__ import annotations


from custom_components.solar_smart_miner.decision import build_decision
from custom_components.solar_smart_miner.protocols import (
    CoordinatorSnapshot,
    EnergySnapshot,
    MinerSnapshot,
)


def _miner(miner_id: str, *, temp: float = 60.0, available: bool = True) -> MinerSnapshot:
    return MinerSnapshot(
        miner_id=miner_id,
        ip=miner_id,
        name=f"M-{miner_id}",
        power_w=700.0 if available else None,
        power_limit_w=700.0,
        min_power_w=500.0,
        max_power_w=900.0,
        temperature_c=temp,
        is_available=available,
        power_limit_entity_id=f"number.{miner_id}",
    )


def _snapshot(available_w: float | None, miners=None, **energy) -> CoordinatorSnapshot:
    return CoordinatorSnapshot(
        energy=EnergySnapshot(
            solar_production_w=2000.0, available_for_miners_w=available_w, **energy
        ),
        miners=miners if miners is not None else [_miner("a"), _miner("b")],
    )


def _decide(snapshot, profile="solar_max", temp_ceiling=80, battery_floor=20):
    return build_decision(snapshot, profile, temp_ceiling, battery_floor)


def test_budget_split_evenly_and_rounded() -> None:
    decision = _decide(_snapshot(1555.0))
    assert decision.proposals == {"a": 770.0, "b": 770.0}
    assert "PROPOSE" in decision.trace


def test_budget_clamped_to_miner_range() -> None:
    assert _decide(_snapshot(5000.0)).proposals == {"a": 900.0, "b": 900.0}
    assert _decide(_snapshot(200.0)).proposals == {"a": 500.0, "b": 500.0}


def test_grid_independent_notes_import_when_below_minimum() -> None:
    decision = _decide(_snapshot(600.0), profile="grid_independent")
    assert any("would import" in line for line in decision.trace)


def test_grid_agnostic_runs_everything_at_max() -> None:
    decision = _decide(_snapshot(None), profile="grid_agnostic")
    assert decision.proposals == {"a": 900.0, "b": 900.0}


def test_unknown_budget_keeps_current_limits() -> None:
    decision = _decide(_snapshot(None))
    assert decision.proposals == {"a": 700.0, "b": 700.0}
    assert "budget unknown" in decision.summary


def test_solar_fault_overrides_profile() -> None:
    decision = _decide(_snapshot(5000.0, solar_fault=True), profile="grid_agnostic")
    assert decision.proposals == {"a": 500.0, "b": 500.0}
    assert decision.summary.startswith("Safety: solar sensor unavailable")


def test_battery_below_floor_sends_all_to_minimum() -> None:
    decision = _decide(_snapshot(5000.0, battery_soc_pct=10.0))
    assert decision.proposals == {"a": 500.0, "b": 500.0}
    assert decision.summary.startswith("Safety: battery below floor")


def test_hot_miner_goes_to_minimum_and_others_share_budget() -> None:
    snapshot = _snapshot(1800.0, miners=[_miner("a", temp=95.0), _miner("b")])
    decision = _decide(snapshot)
    assert decision.proposals == {"a": 500.0, "b": 900.0}
    assert any(line.startswith("SAFETY: M-a") for line in decision.trace)


def test_unavailable_miners_are_skipped() -> None:
    snapshot = _snapshot(1000.0, miners=[_miner("a", available=False), _miner("b")])
    decision = _decide(snapshot)
    assert decision.proposals == {"b": 900.0}
    assert "M-a: unavailable" in decision.trace


def test_no_miners() -> None:
    decision = _decide(_snapshot(1000.0, miners=[]))
    assert decision.summary == "No miners found"
    assert decision.proposals == {}


def test_summary_names_proposals() -> None:
    summary = _decide(_snapshot(1600.0)).summary
    assert summary == "Solar-max: budget 1,600 W → M-a 800 W, M-b 800 W"
