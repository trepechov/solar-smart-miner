"""Tests for the rule-based decision preview: power steps, stop / start, tuning."""
from __future__ import annotations

from custom_components.solar_smart_miner.const import DEFAULT_POWER_STEPS
from custom_components.solar_smart_miner.decision import build_decision
from custom_components.solar_smart_miner.protocols import (
    ACTION_HOLD,
    ACTION_SET_LIMIT,
    ACTION_START,
    ACTION_STOP,
    CoordinatorSnapshot,
    EnergySnapshot,
    MinerSnapshot,
)


def _miner(
    miner_id: str,
    *,
    limit: float | None = 1300.0,
    temp: float = 60.0,
    available: bool = True,
    stopped: bool = False,
    switch: bool = True,
    relay: bool = False,
    since: float | None = None,
    min_w: float = 500.0,
    max_w: float = 3500.0,
) -> MinerSnapshot:
    running = available and not stopped
    return MinerSnapshot(
        miner_id=miner_id,
        ip=miner_id,
        name=f"M-{miner_id}",
        power_w=(limit - 10.0 if limit else None) if running else None,
        power_limit_w=limit,
        min_power_w=min_w,
        max_power_w=max_w,
        temperature_c=temp if running else None,
        is_available=running,
        power_limit_entity_id=f"number.{miner_id}",
        switch_entity_id=f"switch.{miner_id}" if switch else None,
        relay_entity_id=f"switch.relay_{miner_id}" if relay else None,
        is_stopped=stopped,
        minutes_since_limit_change=since,
    )


def _snapshot(available_w: float | None, miners=None, **energy) -> CoordinatorSnapshot:
    return CoordinatorSnapshot(
        energy=EnergySnapshot(
            solar_production_w=2000.0, available_for_miners_w=available_w, **energy
        ),
        miners=miners if miners is not None else [_miner("a"), _miner("b")],
    )


def _decide(
    snapshot, profile="solar_max", temp_target=65, temp_tolerance=10, battery_floor=20, **kw
):
    return build_decision(snapshot, profile, temp_target, temp_tolerance, battery_floor, **kw)


def _three(**kw) -> list[MinerSnapshot]:
    return [_miner(i, **kw) for i in "abc"]


def _actions(decision) -> dict[str, str]:
    return {mid: plan.action for mid, plan in decision.plans.items()}


# --- power steps ---------------------------------------------------------------


def test_default_steps_are_the_agreed_ladder() -> None:
    assert DEFAULT_POWER_STEPS == [900, 1100, 1300, 1500]


def test_enough_budget_holds_the_current_steps() -> None:
    decision = _decide(_snapshot(3846.0, _three()))

    assert set(_actions(decision).values()) == {ACTION_HOLD}
    assert decision.proposals == {}  # nothing to change


def test_a_small_shortfall_does_not_re_tune_a_miner() -> None:
    """3 x 1300 W against 3,780 W is only 120 W short: inside the tolerance."""
    decision = _decide(_snapshot(3780.0, _three()))

    assert set(_actions(decision).values()) == {ACTION_HOLD}


def test_a_real_shortfall_steps_miners_down_one_step_at_a_time() -> None:
    decision = _decide(_snapshot(3000.0, _three()))

    assert decision.proposals == {"a": 1100.0, "b": 1100.0, "c": 900.0}


def test_every_proposal_is_one_of_the_steps() -> None:
    for budget in range(0, 6200, 130):
        decision = _decide(_snapshot(float(budget), _three(limit=1100.0)))
        for limit in decision.proposals.values():
            assert limit in DEFAULT_POWER_STEPS, (budget, limit)


def test_steps_up_only_with_spare_power_beyond_the_step_cost() -> None:
    one = [_miner("a", limit=1100.0)]

    assert _decide(_snapshot(1350.0, one)).plans["a"].action == ACTION_HOLD  # 250 spare < 300
    assert _decide(_snapshot(1400.0, one)).proposals == {"a": 1300.0}


def test_steps_up_to_the_highest_step_the_budget_allows() -> None:
    assert _decide(_snapshot(9000.0, [_miner("a", limit=900.0)])).proposals == {"a": 1500.0}


def test_a_limit_off_the_ladder_is_snapped_once_to_the_nearest_step() -> None:
    # 1,280 W (an old arbitrary limit) is nearest to 1,300 W and is moved there, then stays.
    decision = _decide(_snapshot(1500.0, [_miner("a", limit=1280.0)]))

    assert decision.proposals == {"a": 1300.0}


def test_each_miner_only_uses_steps_inside_its_own_range() -> None:
    narrow = _miner("a", limit=900.0, min_w=500.0, max_w=1200.0)

    assert _decide(_snapshot(9000.0, [narrow])).proposals == {"a": 1100.0}


def test_miner_range_without_any_step_falls_back_to_its_ends() -> None:
    odd = _miner("a", limit=500.0, min_w=500.0, max_w=800.0)

    assert _decide(_snapshot(9000.0, [odd])).proposals == {"a": 800.0}


# --- stop / start ----------------------------------------------------------------


def test_stops_miners_when_not_even_the_lowest_step_fits() -> None:
    decision = _decide(_snapshot(100.0, _three(limit=900.0)))

    assert set(_actions(decision).values()) == {ACTION_STOP}
    assert decision.proposals == {}
    assert all(p.method == "pause" for p in decision.plans.values())


def test_stop_uses_the_relay_when_one_is_configured() -> None:
    miners = [_miner("a", limit=900.0, relay=True), _miner("b", limit=900.0)]
    decision = _decide(_snapshot(0.0, miners))

    assert decision.plans["a"].method == "relay"
    assert decision.plans["a"].target_entity_id == "switch.relay_a"
    assert decision.plans["b"].method == "pause"
    assert decision.plans["b"].target_entity_id == "switch.b"


def test_without_any_stop_method_the_miner_drops_to_its_lowest_step() -> None:
    decision = _decide(_snapshot(0.0, [_miner("a", limit=1300.0, switch=False)]))

    assert decision.plans["a"].action == ACTION_SET_LIMIT
    assert decision.plans["a"].limit_w == 900.0
    assert any("no stop method" in line for line in decision.trace)


def test_budget_for_only_some_miners_stops_the_rest() -> None:
    decision = _decide(_snapshot(1300.0, _three(limit=900.0)))

    assert _actions(decision) == {"a": ACTION_SET_LIMIT, "b": ACTION_STOP, "c": ACTION_STOP}
    assert decision.proposals == {"a": 1100.0}


def test_a_stopped_miner_is_not_unavailable_and_stays_stopped_without_budget() -> None:
    decision = _decide(_snapshot(0.0, [_miner("a", stopped=True)]))

    assert decision.plans["a"].action == ACTION_HOLD
    assert "stopped" in decision.summary
    assert "All miners unavailable" not in decision.summary


def test_starts_a_stopped_miner_when_the_lowest_step_plus_margin_fits() -> None:
    miners = [_miner("a", limit=1100.0), _miner("b", stopped=True, limit=1100.0)]

    assert _decide(_snapshot(2050.0, miners)).plans["b"].action != ACTION_START
    started = _decide(_snapshot(2100.0, miners)).plans["b"]
    assert started.action == ACTION_START
    assert started.limit_w == 900.0
    assert (started.method, started.target_entity_id) == ("pause", "switch.b")


def test_start_goes_through_the_relay_when_configured() -> None:
    decision = _decide(_snapshot(5000.0, [_miner("a", stopped=True, relay=True)]))

    plan = decision.plans["a"]
    assert (plan.action, plan.method, plan.target_entity_id) == (
        ACTION_START,
        "relay",
        "switch.relay_a",
    )


def test_unreachable_miners_are_skipped() -> None:
    snapshot = _snapshot(5000.0, [_miner("a", available=False), _miner("b")])
    decision = _decide(snapshot)

    assert "a" not in decision.plans
    assert "M-a: unavailable" in decision.trace


# --- tuning ----------------------------------------------------------------------


def test_a_miner_that_is_still_tuning_is_not_stepped_up() -> None:
    tuning = [_miner("a", limit=1100.0, since=10.0)]
    decision = _decide(_snapshot(5000.0, tuning), tuning_settle_minutes=60)

    assert decision.plans["a"].action == ACTION_HOLD
    assert decision.plans["a"].reason == "tuning"
    assert any("still tuning" in line and "50 min left" in line for line in decision.trace)


def test_a_settled_miner_is_stepped_up() -> None:
    for since in (None, 90.0):
        decision = _decide(_snapshot(5000.0, [_miner("a", limit=1100.0, since=since)]))
        assert decision.proposals == {"a": 1500.0}


def test_tuning_never_blocks_stepping_down_or_stopping() -> None:
    down = _decide(_snapshot(1200.0, [_miner("a", limit=1500.0, since=5.0)]))
    assert down.proposals == {"a": 1300.0}  # 1,500 W is over 1,200 W + tolerance

    stop = _decide(_snapshot(0.0, [_miner("a", limit=900.0, since=5.0)]))
    assert stop.plans["a"].action == ACTION_STOP


# --- profiles and safety -------------------------------------------------------------


def test_grid_independent_notes_import_when_below_the_lowest_step() -> None:
    decision = _decide(_snapshot(600.0, [_miner("a", limit=900.0)]), profile="grid_independent")

    assert any("would import" in line for line in decision.trace)


def test_grid_agnostic_runs_everything_at_the_top_step() -> None:
    decision = _decide(_snapshot(None, _three(limit=900.0)), profile="grid_agnostic")

    assert decision.proposals == {"a": 1500.0, "b": 1500.0, "c": 1500.0}


def test_grid_agnostic_starts_stopped_miners() -> None:
    decision = _decide(_snapshot(None, [_miner("a", stopped=True)]), profile="grid_agnostic")

    assert decision.plans["a"].action == ACTION_START
    assert decision.plans["a"].limit_w == 1500.0


def test_unknown_budget_holds_current_limits() -> None:
    decision = _decide(_snapshot(None, _three()))

    assert set(_actions(decision).values()) == {ACTION_HOLD}
    assert "budget unknown" in decision.summary


def test_solar_sensor_fault_holds_every_miner_instead_of_re_tuning_them() -> None:
    decision = _decide(_snapshot(5000.0, _three(), solar_fault=True), profile="grid_agnostic")

    assert set(_actions(decision).values()) == {ACTION_HOLD}
    assert decision.summary.startswith("Safety: solar sensor unavailable")


def test_battery_below_floor_stops_all_miners() -> None:
    decision = _decide(_snapshot(5000.0, _three(), battery_soc_pct=10.0))

    assert set(_actions(decision).values()) == {ACTION_STOP}
    assert decision.summary.startswith("Safety: battery below floor")


# --- temperature: target + tolerance ---------------------------------------------------


def test_too_warm_miner_steps_down_one_step_and_the_others_share_the_budget() -> None:
    snapshot = _snapshot(2300.0, miners=[_miner("a", temp=75.0), _miner("b")])
    decision = _decide(snapshot)  # target 65 °C + tolerance 10 °C: 75 °C is too warm

    assert decision.plans["a"].action == ACTION_SET_LIMIT
    assert decision.plans["a"].limit_w == 1100.0  # one step, not straight to the lowest
    assert decision.plans["b"].action == ACTION_HOLD  # 1,300 W fits what is left of 2,300 W
    assert "M-a: 75 °C, at or above 75 °C → one step down" in decision.trace


def test_miner_inside_the_band_holds_even_with_spare_energy() -> None:
    warm = _decide(_snapshot(9000.0, [_miner("a", limit=1100.0, temp=74.0)]))
    cool = _decide(_snapshot(9000.0, [_miner("a", limit=1100.0, temp=64.0)]))

    assert warm.plans["a"].action == ACTION_HOLD
    assert warm.plans["a"].limit_w == 1100.0
    assert cool.proposals == {"a": 1500.0}  # below the target it may step up


def test_miner_inside_the_band_still_steps_down_for_the_budget() -> None:
    decision = _decide(_snapshot(1100.0, [_miner("a", limit=1500.0, temp=70.0)]))

    assert decision.proposals == {"a": 1100.0}


def test_band_follows_the_configured_target_and_tolerance() -> None:
    miners = [_miner("a", limit=1100.0, temp=70.0)]

    assert _decide(_snapshot(9000.0, miners), temp_target=75).proposals == {"a": 1500.0}
    assert _decide(_snapshot(9000.0, miners), temp_target=60, temp_tolerance=5).proposals == {
        "a": 900.0
    }


def test_too_warm_at_the_lowest_step_is_left_to_the_miner() -> None:
    decision = _decide(_snapshot(9000.0, [_miner("a", limit=900.0, temp=85.0)]))

    assert decision.plans["a"].action == ACTION_HOLD
    assert decision.plans["a"].limit_w == 900.0
    assert any("lowest step" in line and "cutoff" in line for line in decision.trace)


def test_temperature_is_ignored_while_the_miner_is_tuning() -> None:
    decision = _decide(_snapshot(1300.0, [_miner("a", limit=1300.0, temp=80.0, since=5.0)]))

    assert decision.plans["a"].action == ACTION_HOLD
    assert decision.plans["a"].limit_w == 1300.0


def test_grid_agnostic_keeps_a_warm_miner_at_its_step() -> None:
    miners = [_miner("a", limit=1100.0, temp=70.0), _miner("b", limit=1100.0)]
    decision = _decide(_snapshot(None, miners), profile="grid_agnostic")

    assert decision.plans["a"].action == ACTION_HOLD
    assert decision.proposals == {"b": 1500.0}


def test_no_miners() -> None:
    decision = _decide(_snapshot(1000.0, miners=[]))

    assert decision.summary == "No miners found"
    assert decision.plans == {}


def test_summary_names_what_each_miner_will_do() -> None:
    miners = [_miner("a", limit=900.0), _miner("b", limit=900.0), _miner("c", limit=900.0)]

    # 2,000 W: two miners at 900 W fit, the third is stopped; 200 W spare is under the step-up margin.
    assert _decide(_snapshot(2000.0, miners)).summary == (
        "Solar-max: budget 2,000 W → M-a hold 900 W, M-b hold 900 W, M-c stop (pause)"
    )
    # 3,000 W leaves 300 W over three miners at 900 W: exactly one step up (200 W + 100 W margin).
    assert _decide(_snapshot(3000.0, miners)).summary == (
        "Solar-max: budget 3,000 W → M-a 1,100 W, M-b hold 900 W, M-c hold 900 W"
    )
    assert _decide(_snapshot(1000.0, [_miner("a", limit=900.0)])).summary == (
        "Solar-max: budget 1,000 W → M-a hold 900 W"
    )
    assert _decide(_snapshot(0.0, [_miner("a", limit=900.0)])).summary == (
        "Solar-max: budget 0 W → M-a stop (pause)"
    )


def test_trace_lists_pv_and_forecast_readings_only_when_present() -> None:
    plain = _decide(_snapshot(1000.0)).trace
    assert not any(line.startswith(("Actual PV", "Forecast PV")) for line in plain)

    trace = _decide(
        _snapshot(
            1000.0,
            pv_power_w=3926.0,
            forecast_now_w=9888.0,
            forecast_next_hour_w=9039.0,
            forecast_remaining_kwh=28.04,
        )
    ).trace
    assert "Actual PV output: 3,926 W" in trace
    assert "Forecast PV now: 9,888 W" in trace
    assert "Forecast PV next hour: 9,039 W" in trace
    assert "Forecast PV left today: 28.0 kWh" in trace


def test_pv_and_forecast_never_change_the_proposal() -> None:
    plain = _decide(_snapshot(1555.0)).proposals
    with_ref = _decide(_snapshot(1555.0, pv_power_w=100.0, forecast_now_w=99999.0)).proposals
    assert plain == with_ref


# --- replay of a real evening --------------------------------------------------------

# Budgets (W) the controller computed on 2026-10-05 from 16:40 to sunset, three miners at 1,300 W.
SUNSET_BUDGETS = [3840, 3271, 2886, 2664, 1941, 1620, 947, 888, 105, 9, 0]


def test_sunset_replay_steps_down_stops_miners_and_stays_on_the_ladder() -> None:
    state = {i: [1300.0, False] for i in "abc"}  # miner -> [limit, stopped]

    for budget in SUNSET_BUDGETS:
        miners = [_miner(i, limit=lim, stopped=stopped) for i, (lim, stopped) in state.items()]
        decision = _decide(_snapshot(float(budget), miners))

        for mid, plan in decision.plans.items():
            if plan.action in (ACTION_SET_LIMIT, ACTION_START):
                assert plan.limit_w in DEFAULT_POWER_STEPS
                state[mid] = [plan.limit_w, False]
            elif plan.action == ACTION_STOP:
                state[mid][1] = True
        running_w = sum(lim for lim, stopped in state.values() if not stopped)
        assert running_w <= budget + 150, (budget, state)  # never more than budget + tolerance

    assert all(stopped for _, stopped in state.values())  # dark: everything is stopped


def test_morning_replay_fills_a_running_miner_before_starting_the_next() -> None:
    state = {i: [900.0, True] for i in "abc"}
    started: list[int] = []

    for budget in (0, 800, 1000, 1500, 2100, 2600, 3200, 4000):
        miners = [_miner(i, limit=lim, stopped=stopped) for i, (lim, stopped) in state.items()]
        for mid, plan in _decide(_snapshot(float(budget), miners)).plans.items():
            if plan.action in (ACTION_SET_LIMIT, ACTION_START):
                state[mid] = [plan.limit_w, False]
        started.append(sum(1 for _, stopped in state.values() if not stopped))

    # 2,100 W: the one running miner goes to its top step (1,500 W) rather than a second
    # miner starting; the second needs 900 W + 100 W margin on top of that.
    assert started == [0, 0, 1, 1, 1, 2, 2, 3]
