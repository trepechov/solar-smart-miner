"""Tests for the rule-based decisions: Solar-follow steers on the grid import; power steps, one
change at a time, ramp lock, stop / start, temperature, even load."""
from __future__ import annotations

import pytest

from custom_components.solar_smart_miner.const import DEFAULT_POWER_STEPS
from custom_components.solar_smart_miner.decision import build_decision, min_import_range_w
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


# A short ladder keeps the allocation tests readable; the default ladder has its own test.
STEPS = [900, 1100, 1300, 1500]


def _decide(
    snapshot, profile="solar_follow", temp_target=65, temp_tolerance=10, battery_floor=20, **kw
):
    # The default import range, 200 to 400 W, and no step-down delay unless a test sets one.
    kw.setdefault("power_steps", STEPS)
    return build_decision(snapshot, profile, temp_target, temp_tolerance, battery_floor, **kw)


def _metered(import_w: float, miners: list[MinerSnapshot], **energy) -> CoordinatorSnapshot:
    """Snapshot as the coordinator builds it from a grid meter reading this import."""
    draw = sum(m.power_w or 0.0 for m in miners)
    return _snapshot(draw - import_w, miners, grid_net_w=-import_w, **energy)


BELOW, INSIDE = 50.0, 300.0  # imports below and inside the default 200 to 400 W range


def _over(watts: float) -> float:
    """An import this far above the 400 W maximum."""
    return 400.0 + watts


def _apply(state: dict[str, list], decision) -> None:
    """Carry a decision out on a {miner: [limit, stopped]} state."""
    for mid, plan in decision.plans.items():
        if plan.action == ACTION_STOP:
            state[mid][1] = True
        elif plan.action in (ACTION_SET_LIMIT, ACTION_START):
            state[mid] = [plan.limit_w, False]


def _farm_import(state: dict[str, list], potential_w: float, house_w: float = 300.0) -> float:
    """Zero export: the inverters give at most the load, so the import never goes below 0."""
    load = house_w + sum(limit for limit, stopped in state.values() if not stopped)
    return load - min(potential_w, load)


def _three(**kw) -> list[MinerSnapshot]:
    return [_miner(i, **kw) for i in "abc"]


def _actions(decision) -> dict[str, str]:
    return {mid: plan.action for mid, plan in decision.plans.items()}


# --- power steps ---------------------------------------------------------------


def test_default_steps_are_the_agreed_ladder() -> None:
    # Owner, 2026-10-07: 900 to 2,500 W in 200 W steps are the normal ladder.
    assert DEFAULT_POWER_STEPS == [900, 1100, 1300, 1500, 1700, 1900, 2100, 2300, 2500]


def test_without_configured_steps_the_default_ladder_is_used() -> None:
    decision = build_decision(
        _metered(BELOW, [_miner("a", limit=900.0)]), "solar_follow", 65, 10, 20,
    )
    assert decision.proposals == {"a": 1100.0}


def test_inside_the_range_every_miner_holds() -> None:
    decision = _decide(_metered(INSIDE, _three()))

    assert set(_actions(decision).values()) == {ACTION_HOLD}
    assert decision.proposals == {}  # nothing to change
    assert "Import 300 W inside the range → hold" in decision.trace


def test_a_shortfall_one_miner_can_take_is_one_jump_on_the_hungriest_miner() -> None:
    miners = [_miner("a", limit=1100.0), _miner("b", limit=1500.0), _miner("c", limit=1100.0)]
    decision = _decide(_metered(_over(350.0), miners))

    assert decision.proposals == {"b": 1100.0}  # skips 1,300 W: one restart, not two
    assert decision.plans["a"].action == decision.plans["c"].action == ACTION_HOLD
    assert decision.plans["b"].reason == "import above the maximum"


def test_a_shortfall_no_running_miner_can_take_stops_the_lowest_power_one() -> None:
    miners = [_miner("a", limit=1300.0), _miner("b", limit=1100.0), _miner("c", limit=1300.0)]
    decision = _decide(_metered(_over(500.0), miners))  # a step down gives at most 400 W

    assert _actions(decision) == {"a": ACTION_HOLD, "b": ACTION_STOP, "c": ACTION_HOLD}


def test_never_more_than_one_miner_changes() -> None:
    for import_w in range(-2000, 4000, 130):
        for miners in (_three(limit=1100.0), _three(stopped=True), _three(limit=1500.0)):
            decision = _decide(_metered(float(import_w), miners))
            changes = [p for p in decision.plans.values() if p.action != ACTION_HOLD]
            assert len(changes) <= 1, (import_w, decision.summary)


def test_below_the_minimum_one_miner_goes_one_step_up() -> None:
    """Seen 2026-10-07: Brod1 2,300 to 2,500 W, Brod2 and Brod3 2,100 to 2,500 W in one proposal."""
    steps = [1900, 2100, 2300, 2500]
    miners = [_miner("a", limit=2300.0), _miner("b", limit=2100.0), _miner("c", limit=2100.0)]
    decision = _decide(_metered(BELOW, miners), power_steps=steps)

    assert decision.proposals == {"b": 2300.0}  # the weakest, one step
    assert decision.plans["b"].reason == "import below the minimum"
    assert any("One miner changes at a time" in line for line in decision.trace)


def test_one_step_up_even_when_the_meter_shows_a_large_export() -> None:
    # Owner, 2026-10-09: steer on the import alone; an increment is one step, so a wrong
    # reading costs one restart, not a jump to the top.
    decision = _decide(_metered(-3000.0, [_miner("a", limit=900.0)]))

    assert decision.proposals == {"a": 1100.0}


def test_every_proposal_is_one_of_the_steps() -> None:
    for import_w in range(-2000, 4000, 130):
        decision = _decide(_metered(float(import_w), _three(limit=1100.0)))
        for limit in decision.proposals.values():
            assert limit in DEFAULT_POWER_STEPS, (import_w, limit)


def test_a_limit_off_the_ladder_is_snapped_once_to_the_nearest_step() -> None:
    # 1,280 W (an old arbitrary limit) is nearest to 1,300 W and is moved there, then stays.
    decision = _decide(_metered(INSIDE, [_miner("a", limit=1280.0)]))

    assert decision.proposals == {"a": 1300.0}
    assert decision.plans["a"].reason == "off step"
    assert _decide(_metered(INSIDE, [_miner("a", limit=1300.0)])).proposals == {}


def test_each_miner_only_uses_steps_inside_its_own_range() -> None:
    narrow = [_miner("a", limit=1100.0, min_w=500.0, max_w=1200.0)]

    assert _decide(_metered(BELOW, narrow)).proposals == {}  # already at its top step
    assert _decide(_metered(BELOW, [_miner("a", limit=900.0, max_w=1200.0)])).proposals == {"a": 1100.0}


def test_miner_range_without_any_step_falls_back_to_its_ends() -> None:
    odd = _miner("a", limit=500.0, min_w=500.0, max_w=800.0)

    assert _decide(_metered(BELOW, [odd])).proposals == {"a": 800.0}


# --- stop / start ----------------------------------------------------------------


def test_stops_one_miner_at_a_time_when_not_even_the_lowest_step_fits() -> None:
    decision = _decide(_metered(_over(800.0), _three(limit=900.0)))

    assert _actions(decision) == {"a": ACTION_HOLD, "b": ACTION_HOLD, "c": ACTION_STOP}
    assert decision.proposals == {}
    assert decision.plans["c"].method == "pause"


def test_stop_uses_the_relay_when_one_is_configured() -> None:
    relay = _decide(_metered(_over(2000.0), [_miner("a", limit=900.0, relay=True)])).plans["a"]
    pause = _decide(_metered(_over(2000.0), [_miner("b", limit=900.0)])).plans["b"]

    assert (relay.method, relay.target_entity_id) == ("relay", "switch.relay_a")
    assert (pause.method, pause.target_entity_id) == ("pause", "switch.b")


def test_without_any_stop_method_the_miner_drops_to_its_lowest_step() -> None:
    decision = _decide(_metered(_over(2000.0), [_miner("a", limit=1300.0, switch=False)]))

    assert decision.plans["a"].action == ACTION_SET_LIMIT
    assert decision.plans["a"].limit_w == 900.0
    assert any("no stop method" in line for line in decision.trace)


def test_sun_for_only_some_miners_stops_them_one_per_decision() -> None:
    state = {i: [900.0, False] for i in "abc"}
    for _ in range(6):
        miners = [_miner(i, limit=lim, stopped=stopped) for i, (lim, stopped) in state.items()]
        _apply(state, _decide(_metered(_farm_import(state, potential_w=1600.0), miners)))

    # 1,600 W of sun for 300 W of house: c, then b stopped; a alone runs, inside the range.
    assert state == {"a": [900.0, False], "b": [900.0, True], "c": [900.0, True]}


def test_a_stopped_miner_is_not_unavailable_and_stays_stopped_inside_the_range() -> None:
    decision = _decide(_metered(INSIDE, [_miner("a", stopped=True)]))

    assert decision.plans["a"].action == ACTION_HOLD
    assert "stopped" in decision.summary
    assert "All miners unavailable" not in decision.summary


def test_below_the_minimum_a_stopped_miner_starts_at_its_lowest_step() -> None:
    miners = [_miner("a", limit=1100.0), _miner("b", stopped=True, limit=1100.0)]

    assert _decide(_metered(INSIDE, miners)).plans["b"].action != ACTION_START
    started = _decide(_metered(BELOW, miners)).plans["b"]
    assert started.action == ACTION_START  # before a step up of the running miner
    assert started.limit_w == 900.0
    assert (started.method, started.target_entity_id) == ("pause", "switch.b")


def test_start_goes_through_the_relay_when_configured() -> None:
    decision = _decide(_metered(BELOW, [_miner("a", stopped=True, relay=True)]))

    plan = decision.plans["a"]
    assert (plan.action, plan.method, plan.target_entity_id) == (
        ACTION_START,
        "relay",
        "switch.relay_a",
    )


def test_unreachable_miners_are_skipped() -> None:
    snapshot = _metered(BELOW, [_miner("a", available=False), _miner("b")])
    decision = _decide(snapshot)

    assert "a" not in decision.plans
    assert "M-a: unavailable" in decision.trace


# --- settling: the ramp lock is the only wait after a change ---------------------------


def test_once_the_ramp_lock_is_over_a_just_changed_miner_may_move_again() -> None:
    # 0.8.0: the tuning window (no step-up for 5 minutes, temperature ignored) measured the
    # same thing as the settling after a restart (S11); the ramp lock alone covers it.
    decision = _decide(_metered(BELOW, [_miner("a", limit=1100.0, since=2.0)]))

    assert decision.proposals == {"a": 1300.0}


def test_a_settled_miners_temperature_counts_at_once() -> None:
    decision = _decide(_metered(INSIDE, [_miner("a", limit=1300.0, temp=80.0, since=2.0)]))

    assert decision.proposals == {"a": 1100.0}


# --- ramp lock: the whole farm waits while a miner restarts ----------------------------


def test_every_miner_holds_while_a_miner_restarts() -> None:
    decision = _decide(_metered(BELOW, _three(limit=900.0)), minutes_since_change=1.0)

    assert set(_actions(decision).values()) == {ACTION_HOLD}
    assert decision.summary.startswith("Waiting for a miner to restart")
    assert any("ramp lock" in line and "~3 min" in line for line in decision.trace)


def test_the_next_change_comes_once_the_ramp_lock_is_over() -> None:
    for since in (None, 4.0, 30.0):
        decision = _decide(_metered(BELOW, _three(limit=900.0)), minutes_since_change=since)
        assert decision.proposals == {"a": 1100.0}, since


def test_the_ramp_lock_length_is_a_parameter() -> None:
    snapshot = _metered(BELOW, _three(limit=900.0))

    assert _decide(snapshot, minutes_since_change=5.0, ramp_lock_minutes=10).proposals == {}


def test_safety_does_not_wait_for_the_ramp_lock() -> None:
    decision = _decide(_metered(BELOW, _three(), battery_soc_pct=10.0), minutes_since_change=0.0)

    assert set(_actions(decision).values()) == {ACTION_STOP}  # safety may change every miner


# --- profiles and safety -------------------------------------------------------------


@pytest.mark.parametrize("old", ["solar_max", "grid_agnostic", "grid_independent", "battery_focused"])
def test_an_old_profile_name_decides_as_solar_follow(old) -> None:
    # One profile since 0.8.0 (owner, 2026-10-09); the stored name is rewritten at setup, and
    # until then (or in an old decisions.jsonl line) it reads as Solar-follow.
    for snapshot in (_metered(30.0, _three(limit=1100.0)), _metered(700.0, _three(limit=1500.0))):
        follow = _decide(snapshot)
        legacy = _decide(snapshot, profile=old)
        assert legacy.plans == follow.plans
        assert legacy.summary == follow.summary
        assert "Profile: Solar-follow" in legacy.trace


def test_an_unknown_grid_import_holds_current_limits() -> None:
    decision = _decide(_snapshot(None, _three()))

    assert set(_actions(decision).values()) == {ACTION_HOLD}
    assert decision.summary.startswith("Solar-follow: grid import unknown")


def test_solar_sensor_fault_holds_every_miner_instead_of_re_tuning_them() -> None:
    decision = _decide(_metered(BELOW, _three(), solar_fault=True))

    assert set(_actions(decision).values()) == {ACTION_HOLD}
    assert decision.summary.startswith("Safety: solar sensor unavailable")


def test_battery_below_floor_stops_all_miners() -> None:
    decision = _decide(_metered(BELOW, _three(), battery_soc_pct=10.0))

    assert set(_actions(decision).values()) == {ACTION_STOP}
    assert decision.summary.startswith("Safety: battery below floor")


# --- temperature: target + tolerance ---------------------------------------------------


def test_too_warm_miner_steps_down_one_step() -> None:
    decision = _decide(_metered(INSIDE, [_miner("a", temp=75.0), _miner("b")]))
    # target 65 °C + tolerance 10 °C: 75 °C is too warm

    assert decision.plans["a"].action == ACTION_SET_LIMIT
    assert decision.plans["a"].limit_w == 1100.0  # one step, not straight to the lowest
    assert decision.plans["b"].action == ACTION_HOLD
    assert "M-a: 75 °C, at or above 75 °C → one step down" in decision.trace


def test_two_too_warm_miners_step_down_one_at_a_time_hottest_first() -> None:
    miners = [_miner("a", temp=76.0), _miner("b", temp=79.0)]
    decision = _decide(_metered(BELOW, miners))

    assert decision.proposals == {"b": 1100.0}
    assert decision.plans["a"].action == ACTION_HOLD
    assert decision.summary.startswith("Temperature: M-b too warm")


def test_miner_inside_the_band_holds_below_the_minimum() -> None:
    warm = _decide(_metered(BELOW, [_miner("a", limit=1100.0, temp=74.0)]))
    cool = _decide(_metered(BELOW, [_miner("a", limit=1100.0, temp=64.0)]))

    assert warm.plans["a"].action == ACTION_HOLD
    assert warm.plans["a"].limit_w == 1100.0
    assert cool.proposals == {"a": 1300.0}  # below the target it may step up


def test_a_warm_miner_is_skipped_and_the_next_weakest_steps_up() -> None:
    miners = [_miner("a", limit=1100.0, temp=70.0), _miner("b", limit=1300.0)]

    assert _decide(_metered(BELOW, miners)).proposals == {"b": 1500.0}


def test_miner_inside_the_band_still_steps_down_for_the_import() -> None:
    decision = _decide(_metered(_over(300.0), [_miner("a", limit=1500.0, temp=70.0)]))

    assert decision.proposals == {"a": 1100.0}


def test_band_follows_the_configured_target_and_tolerance() -> None:
    miners = [_miner("a", limit=1100.0, temp=70.0)]

    assert _decide(_metered(BELOW, miners), temp_target=75).proposals == {"a": 1300.0}
    assert _decide(_metered(BELOW, miners), temp_target=60, temp_tolerance=5).proposals == {
        "a": 900.0
    }


def test_too_warm_at_the_lowest_step_is_left_to_the_miner() -> None:
    decision = _decide(_metered(BELOW, [_miner("a", limit=900.0, temp=85.0)]))

    assert decision.plans["a"].action == ACTION_HOLD
    assert decision.plans["a"].limit_w == 900.0
    assert any("lowest step" in line and "cutoff" in line for line in decision.trace)


def test_no_miners() -> None:
    decision = _decide(_metered(INSIDE, miners=[]))

    assert decision.summary == "No miners found"
    assert decision.plans == {}


def test_summary_names_the_import_and_what_each_miner_will_do() -> None:
    miners = [_miner("a", limit=900.0), _miner("b", limit=900.0), _miner("c", limit=900.0)]

    assert _decide(_metered(_over(800.0), miners)).summary == (
        "Solar-follow: import 1,200 W → M-a hold 900 W, M-b hold 900 W, M-c stop (pause)"
    )
    assert _decide(_metered(BELOW, miners)).summary == (
        "Solar-follow: import 50 W → M-a 1,100 W, M-b hold 900 W, M-c hold 900 W"
    )
    assert _decide(_metered(INSIDE, [_miner("a", limit=900.0)])).summary == (
        "Solar-follow: import 300 W → M-a hold 900 W"
    )


def test_trace_lists_pv_and_forecast_readings_only_when_present() -> None:
    plain = _decide(_metered(INSIDE, _three())).trace
    assert not any(line.startswith(("Actual PV", "Forecast PV")) for line in plain)

    trace = _decide(
        _metered(
            INSIDE,
            _three(),
            pv_power_w=3926.0,
            forecast_now_w=9888.0,
            forecast_next_hour_w=9039.0,
            forecast_remaining_kwh=28.04,
        )
    ).trace
    assert "Actual PV output: 3,926 W" in trace
    assert "Forecast PV now: 9,888 W (reference only)" in trace
    assert "Forecast PV next hour: 9,039 W (reference only)" in trace
    assert "Forecast PV left today: 28.0 kWh" in trace


def test_pv_and_forecast_never_change_the_proposal() -> None:
    for import_w in (BELOW, INSIDE, _over(300.0)):
        plain = _decide(_metered(import_w, _three())).proposals
        with_ref = _decide(_metered(import_w, _three(), pv_power_w=100.0, forecast_now_w=99999.0)).proposals
        assert plain == with_ref


# --- Solar-follow: aim for a small grid import ------------------------------------------


def test_import_below_the_minimum_steps_up_one_step() -> None:
    snapshot = _metered(30.0, _three(limit=1100.0))  # throttled: the meter sits near 0 W

    decision = _decide(snapshot, import_min_w=200, import_max_w=400)
    assert list(decision.proposals.values()) == [1300.0]  # one miner, one step
    assert "Grid import range: 200 W to 400 W" in decision.trace
    # With no minimum, 30 W of import sits inside the range: nothing moves.
    assert _decide(snapshot, import_min_w=0, import_max_w=400).proposals == {}


def test_import_inside_the_range_holds() -> None:
    # Owner, 2026-10-08: import at least 200 W, step down above 400 W.
    for import_w in (200.0, 300.0, 400.0):
        decision = _decide(_metered(import_w, _three(limit=1100.0)), import_min_w=200, import_max_w=400)
        assert set(_actions(decision).values()) == {ACTION_HOLD}, import_w


def test_import_above_the_maximum_steps_down_back_into_the_range() -> None:
    decision = _decide(_metered(700.0, _three(limit=1500.0)), import_min_w=200, import_max_w=400)

    # 300 W over the maximum: one miner 1,500 → 1,100 W, the smallest cut that brings it back.
    assert list(decision.proposals.values()) == [1100.0]
    assert any("step down by at least 300 W" in line for line in decision.trace)


def test_all_stopped_and_import_below_the_floor_starts_a_miner() -> None:
    # 2026-10-08: all paused, meter at 0 W from 07:45, budget stuck at the import target, so
    # the 1,000 W a start needs never showed up and nothing started until 09:54.
    miners = [_miner(i, stopped=True) for i in "abc"]
    decision = _decide(_metered(0.0, miners), import_min_w=250)

    assert _actions(decision) == {"a": ACTION_START, "b": ACTION_HOLD, "c": ACTION_HOLD}
    assert decision.plans["a"].limit_w == 900.0  # its lowest step


def test_below_the_floor_the_next_stopped_miner_starts_before_a_raise() -> None:
    miners = [_miner("a", limit=1300.0), _miner("b", stopped=True), _miner("c", stopped=True)]
    decision = _decide(_metered(20.0, miners), import_min_w=250)

    assert _actions(decision) == {"a": ACTION_HOLD, "b": ACTION_START, "c": ACTION_HOLD}


def test_nothing_starts_while_the_sun_is_down() -> None:
    miners = [_miner(i, stopped=True) for i in "abc"]
    decision = _decide(_metered(0.0, miners), import_min_w=250, sun_up=False)

    assert decision.proposals == {}
    assert set(_actions(decision).values()) == {ACTION_HOLD}
    assert any("the sun is down" in line for line in decision.trace)


def test_during_sunset_nothing_starts_or_steps_up() -> None:
    # 2026-10-08 16:48 to 18:13: a stop dropped the import below the minimum, "start a stopped
    # miner" undid it, nine times. Owner, 2026-10-09: during sunset nothing starts.
    miners = [_miner("a", limit=900.0), _miner("b", stopped=True)]
    decision = _decide(_metered(0.0, miners), sunset=True)

    assert decision.proposals == {}
    assert set(_actions(decision).values()) == {ACTION_HOLD}
    assert "Sunset: production is falling → nothing starts or steps up until sunrise" in decision.trace
    assert any("but the sun is setting → nothing starts" in line for line in decision.trace)


def test_during_sunset_a_shortfall_still_steps_down_and_the_load_evens_only_downwards() -> None:
    down = _decide(_metered(_over(150.0), [_miner("a", limit=1500.0)]), sunset=True)
    assert down.proposals == {"a": 1300.0}

    uneven = [_miner("a", limit=1900.0), _miner("b", limit=900.0)]
    assert _decide(_metered(INSIDE, uneven), power_steps=DEFAULT_POWER_STEPS, sunset=True).proposals == {
        "a": 1700.0
    }


def test_step_down_waits_until_the_shortfall_has_lasted() -> None:
    snapshot = _metered(600.0, _three(limit=1500.0))
    kw = {"import_min_w": 200, "step_down_delay_minutes": 5, "sunrise": False}

    early = _decide(snapshot, minutes_import_high=2, **kw)
    assert early.proposals == {}
    assert any("steps down after 5 min" in line for line in early.trace)
    assert list(_decide(snapshot, minutes_import_high=6, **kw).proposals.values()) == [1300.0]


def test_during_sunrise_a_shortfall_waits_for_the_sunrise_delay() -> None:
    # Owner, 2026-10-08: some import in the morning is fine, the sun will come. 2026-10-09: only
    # during sunrise, while production still rises (transition.py), not all morning.
    snapshot = _metered(600.0, _three(limit=1500.0))
    kw = {
        "import_min_w": 200,
        "step_down_delay_minutes": 5,
        "morning_step_down_delay_minutes": 30,
        "sunrise": True,
    }

    waiting = _decide(snapshot, minutes_import_high=10, **kw)
    assert waiting.proposals == {}
    assert any("the sun is rising" in line for line in waiting.trace)
    assert list(_decide(snapshot, minutes_import_high=31, **kw).proposals.values()) == [1300.0]


def test_the_forecast_never_changes_the_proposal() -> None:
    # Owner, 2026-10-07: the forecast can be far off, so it is shown but never decides.
    miners = [_miner("a", limit=1500.0), _miner("b", limit=1500.0), _miner("c", stopped=True)]

    blind = _decide(_metered(20.0, miners), import_min_w=250)
    seen = _decide(
        _metered(20.0, miners, forecast_now_w=6000.0, pv_power_w=3000.0), import_min_w=250
    )
    assert seen.plans == blind.plans
    assert not any("hidden headroom" in line for line in seen.trace)
    assert "Forecast PV now: 6,000 W (reference only)" in seen.trace


# --- a whole evening and a whole morning --------------------------------------------------

# What the panels could give (W) on 2026-10-05 from 16:40 to sunset, three miners at 1,300 W.
SUNSET_POTENTIAL = [3840, 3271, 2886, 2664, 1941, 1620, 947, 888, 105, 9, 0]


def test_a_sunset_steps_down_stops_every_miner_and_stays_on_the_ladder() -> None:
    state = {i: [1300.0, False] for i in "abc"}  # miner -> [limit, stopped]

    for potential in SUNSET_POTENTIAL:
        for _ in range(3):  # a few decisions per reading, one change each
            miners = [_miner(i, limit=lim, stopped=stopped) for i, (lim, stopped) in state.items()]
            decision = _decide(
                _metered(_farm_import(state, potential), miners), sun_up=potential > 0, sunset=True
            )
            for plan in decision.plans.values():
                if plan.action in (ACTION_SET_LIMIT, ACTION_START):
                    assert plan.limit_w in DEFAULT_POWER_STEPS
                    assert plan.action != ACTION_START  # nothing starts during sunset
            _apply(state, decision)

    assert all(stopped for _, stopped in state.values())  # dark: everything is stopped


def test_a_morning_starts_each_miner_then_raises_them() -> None:
    state = {i: [900.0, True] for i in "abc"}
    running: list[int] = []

    for potential in (0, 800, 1600, 2500, 3400, 4000, 4600, 5200):
        for _ in range(2):
            miners = [_miner(i, limit=lim, stopped=stopped) for i, (lim, stopped) in state.items()]
            _apply(state, _decide(_metered(_farm_import(state, potential), miners), sun_up=potential > 0))
        running.append(sum(1 for _, stopped in state.values() if not stopped))

    # One increment each time the sun covers the load: a stopped miner starts first.
    assert running[0] == 0 and running[-1] == 3
    assert running == sorted(running)
    assert sum(limit for limit, stopped in state.values() if not stopped) > 3 * 900


def test_a_range_with_no_width_is_widened_by_one_step() -> None:
    decision = _decide(_metered(500.0, _three(limit=1100.0)), import_min_w=400, import_max_w=400)

    assert "Grid import range: 400 W to 600 W" in decision.trace
    assert set(_actions(decision).values()) == {ACTION_HOLD}


def test_a_range_narrower_than_one_step_is_widened() -> None:
    # 200 to 300 W: a 200 W step up from 150 W import would land at 350 W, above the maximum.
    decision = _decide(_metered(150.0, _three(limit=1100.0)), import_min_w=200, import_max_w=300)

    assert "Grid import range: 200 W to 400 W" in decision.trace


def test_the_narrowest_import_range_is_the_largest_step_gap() -> None:
    assert min_import_range_w([900.0, 1100.0, 1300.0]) == 200
    assert min_import_range_w([900.0, 1400.0, 1500.0]) == 500
    assert min_import_range_w([1000.0]) == 200


# --- even load (secondary) -------------------------------------------------------------


def test_with_the_import_in_range_limits_two_steps_apart_are_evened_out() -> None:
    # 2026-10-08 afternoon: 2,500 / 1,900 / 1,100 W, boards 58 / 45 / 42 °C. Owner: split the
    # power evenly once every miner runs; a step up first, so the import never drops below range.
    miners = [_miner("a", limit=2500.0), _miner("b", limit=1900.0), _miner("c", limit=1100.0)]
    decision = _decide(_metered(300.0, miners), import_min_w=200, import_max_w=400,
                       power_steps=DEFAULT_POWER_STEPS)

    assert decision.proposals == {"c": 1300.0}
    assert decision.plans["c"].reason == "even load"
    assert any(line.startswith("Even load:") for line in decision.trace)


def test_limits_within_one_step_are_even_enough() -> None:
    miners = [_miner("a", limit=1500.0), _miner("b", limit=1300.0), _miner("c", limit=1300.0)]
    decision = _decide(_metered(300.0, miners), import_min_w=200, import_max_w=400)

    assert decision.proposals == {}


def test_the_hungriest_steps_down_when_the_weakest_cannot_step_up() -> None:
    # c is warm (inside the band: no step up), so the load is evened from the top.
    miners = [_miner("a", limit=2500.0), _miner("b", limit=1900.0), _miner("c", limit=1100.0, temp=70.0)]
    decision = _decide(_metered(300.0, miners), import_min_w=200, import_max_w=400,
                       power_steps=DEFAULT_POWER_STEPS)

    assert decision.proposals == {"a": 2300.0}


def test_after_sunset_the_load_is_evened_only_downwards() -> None:
    miners = [_miner("a", limit=1900.0), _miner("b", limit=900.0)]
    decision = _decide(_metered(300.0, miners), import_min_w=200, import_max_w=400,
                       power_steps=DEFAULT_POWER_STEPS, sun_up=False)

    assert decision.proposals == {"a": 1700.0}


def test_of_equal_limits_the_coolest_steps_up_and_the_hottest_steps_down() -> None:
    up = [_miner("a", limit=1900.0), _miner("b", limit=1100.0, temp=58.0), _miner("c", limit=1100.0, temp=50.0)]
    assert _decide(_metered(300.0, up), import_min_w=200, import_max_w=400,
                   power_steps=DEFAULT_POWER_STEPS).proposals == {"c": 1300.0}

    down = [_miner("a", limit=1900.0, temp=55.0), _miner("b", limit=1900.0, temp=62.0), _miner("c", limit=1100.0)]
    assert _decide(_metered(300.0, down), import_min_w=200, import_max_w=400,
                   power_steps=DEFAULT_POWER_STEPS, sun_up=False).proposals == {"b": 1700.0}


def test_below_the_minimum_the_coolest_of_the_weakest_steps_up() -> None:
    miners = [_miner("a", limit=1100.0, temp=58.0), _miner("b", limit=1100.0, temp=45.0)]
    decision = _decide(_metered(20.0, miners), import_min_w=200, import_max_w=400)

    assert decision.proposals == {"b": 1300.0}


def test_the_load_is_not_evened_while_a_miner_is_stopped() -> None:
    # Starting the next miner comes first; evening out is for once every miner runs.
    miners = [_miner("a", limit=2500.0), _miner("b", limit=1100.0), _miner("c", stopped=True)]
    decision = _decide(_metered(300.0, miners), import_min_w=200, import_max_w=400,
                       power_steps=DEFAULT_POWER_STEPS)

    assert decision.proposals == {}


# --- the pipeline -----------------------------------------------------------------


def test_a_failing_rule_holds_every_miner_instead_of_failing_the_update(monkeypatch, caplog) -> None:
    from custom_components.solar_smart_miner.decision import limits

    def broken(ctx):
        raise ZeroDivisionError("a bug")

    monkeypatch.setattr(limits, "check", broken)
    decision = _decide(_snapshot(3000.0, [_miner("a", limit=1300.0), _miner("b", stopped=True)]))

    assert decision.summary == "Error in the rules: every miner holds"
    assert {mid: (p.action, p.limit_w) for mid, p in decision.plans.items()} == {
        "a": (ACTION_HOLD, 1300.0), "b": (ACTION_HOLD, 1300.0),
    }
    assert any("ZeroDivisionError: a bug" in line for line in decision.trace)
    assert "every miner holds" in caplog.text


def test_the_trace_names_the_group_and_rule_that_decided() -> None:
    assert "Decided by: Pacing / rule.ramp-lock" in _decide(
        _metered(BELOW, _three()), minutes_since_change=1
    ).trace
    assert "Decided by: Allocation / rule.step-down-allocation" in _decide(_metered(BELOW, _three())).trace
    assert "Decided by: Target / rule.small-import-target" in _decide(_metered(INSIDE, _three())).trace
    assert "Decided by: Allocation / rule.stop-below-lowest-step" in _decide(
        _metered(_over(2000.0), [_miner("a", limit=900.0)])
    ).trace


def test_the_groups_run_in_order_and_an_earlier_one_wins() -> None:
    # Safety (sensor lost) wins over pacing (ramp lock) over limits (too warm).
    warm = [_miner("a", temp=90.0), _miner("b")]
    assert _decide(_snapshot(3000.0, warm, solar_fault=True), minutes_since_change=1).summary.startswith(
        "Safety:"
    )
    assert _decide(_snapshot(3000.0, warm), minutes_since_change=1).summary.startswith("Waiting")
    assert _decide(_snapshot(3000.0, warm)).summary.startswith("Temperature:")
