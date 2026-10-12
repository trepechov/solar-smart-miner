"""The four day messages (plan 2026-10-09-002, U4 and U5): what the day report sees and says."""
from __future__ import annotations

from datetime import datetime, timedelta

from custom_components.solar_smart_miner.day_report import (
    ALL_MAX,
    EVENING,
    MORNING,
    STEP_DOWN,
    DayReport,
    describe,
)
from custom_components.solar_smart_miner.protocols import (
    ACTION_SET_LIMIT,
    CoordinatorSnapshot,
    Decision,
    EnergySnapshot,
    MinerPlan,
    MinerSnapshot,
)

STEPS = [900.0, 1100.0, 1300.0]  # top step 1300 W
BAND_TOP = 70.0
T0 = datetime.fromisoformat("2026-10-12T07:00:00+03:00")


def _miner(n: int, limit: float | None = 1300.0, *, mining: bool = True, temp: float = 50.0) -> MinerSnapshot:
    return MinerSnapshot(
        miner_id=f"192.0.2.{n}", ip=f"192.0.2.{n}", power_w=limit if mining else 0.0,
        power_limit_w=limit, min_power_w=500.0, max_power_w=3500.0, temperature_c=temp,
        is_available=True, power_limit_entity_id=None, hashrate_th=50.0 if mining else 0.0,
        name=f"Miner {n}", is_stopped=not mining,
    )


def _snap(miners, *, pv=None, grid=None, forecast_now=None, next_hour=None, remaining=None, plans=None):
    energy = EnergySnapshot(
        solar_production_w=None, pv_power_w=pv, grid_net_w=grid, forecast_now_w=forecast_now,
        forecast_next_hour_w=next_hour, forecast_remaining_kwh=remaining,
        miner_consumption_sum_w=sum(m.power_w or 0.0 for m in miners),
    )
    return CoordinatorSnapshot(energy=energy, miners=list(miners), decision=Decision("", plans=plans or {}))


class Day:
    """Feeds polls a minute apart to one DayReport and collects its events."""

    def __init__(self, report: DayReport | None = None, start: datetime = T0) -> None:
        self.report = report or DayReport()
        self.now = start
        self.events: list[str] = []
        self.texts: dict[str, str] = {}

    def poll(
        self, snapshot, *, minutes: float = 1, settled=True, sun_up=True, sunset=False,
        restarting=frozenset(), poll_s: float = 60.0,
    ):
        self.now += timedelta(minutes=minutes)
        events = self.report.update(
            self.now, snapshot, settled=settled, restarting=set(restarting), steps=STEPS,
            too_warm_c=BAND_TOP, sun_up=sun_up, sunset=sunset, poll_s=poll_s,
        )
        for event in events:
            self.texts[event] = describe(event, self.report.state, snapshot)
        self.events += events
        return events


def _sunny_day() -> Day:
    day = Day()
    day.poll(_snap([_miner(1, 900, mining=False), _miner(2, 900, mining=False)]))  # night: begins
    day.poll(_snap([_miner(1, 900), _miner(2, 900, mining=False)], remaining=31.4, next_hour=2500))
    day.poll(_snap([_miner(1, 1300), _miner(2, 1300)], pv=3400), minutes=120)
    day.poll(_snap([_miner(1, 1300), _miner(2, 1300)], pv=3400))
    return day


def test_a_sunny_day_reports_each_event_once_in_order() -> None:
    day = _sunny_day()
    down = {"192.0.2.2": MinerPlan(ACTION_SET_LIMIT, limit_w=1100.0, reason="import 520 W for 5 min")}
    day.poll(_snap([_miner(1, 1300), _miner(2, 1300)], pv=3000, plans=down), minutes=180)
    day.poll(_snap([_miner(1, 1300), _miner(2, 1100)], pv=2900, forecast_now=4000))
    day.poll(_snap([_miner(1, 1100), _miner(2, 900)], pv=1500))  # later step downs: quiet
    day.poll(_snap([_miner(1, 900, mining=False), _miner(2, 900, mining=False)]), minutes=120,
             sunset=True)
    day.poll(_snap([_miner(1, 900, mining=False), _miner(2, 900, mining=False)]), sun_up=False)

    assert day.events == [MORNING, ALL_MAX, STEP_DOWN, EVENING]
    assert day.texts[MORNING] == (
        "☀️ Production is up: Miner 1 started at 900 W (07:02).\n"
        "Forecast: 31.4 kWh for the rest of today, 2.5 kW next hour."
    )
    assert day.texts[ALL_MAX] == (
        "⚡ All 2 miners run at their top step, settled (09:03, PV 3.4 kW).\n"
        "It took 2 h 1 min from the first start (07:02)."
    )
    assert day.texts[STEP_DOWN] == (
        "🌥 Stepping down: Miner 2 1,300 W → 1,100 W (import 520 W for 5 min).\n"
        "Forecast 4.0 kW, panels give 2.9 kW (72%)."
    )
    assert day.texts[EVENING].startswith("🌙 Day done: the last miner stopped at 14:05.\nToday: PV ")
    assert "mining 07:02–14:05" in day.texts[EVENING]
    assert "Forecast this morning: 31.4 kWh." in day.texts[EVENING]
    assert "Peak" not in day.texts[EVENING]


def test_a_cloud_that_stops_every_miner_at_noon_is_not_the_evening() -> None:
    day = _sunny_day()
    day.poll(_snap([_miner(1, 1300, mining=False), _miner(2, 1300, mining=False)]))
    day.poll(_snap([_miner(1, 1300, mining=False), _miner(2, 1300, mining=False)]))
    day.poll(_snap([_miner(1, 900), _miner(2, 900, mining=False)]))

    assert day.events == [MORNING, ALL_MAX, STEP_DOWN]  # the stop is the first step down
    assert EVENING not in day.events and day.events.count(MORNING) == 1


def test_all_max_waits_for_settling_the_band_and_a_second_poll() -> None:
    day = Day()
    day.poll(_snap([_miner(1, 900, mining=False)]))
    day.poll(_snap([_miner(1, 900)]))
    day.poll(_snap([_miner(1, 1300)]), settled=False)
    day.poll(_snap([_miner(1, 1300)]), settled=False)
    day.poll(_snap([_miner(1, 1300, temp=BAND_TOP)]))
    day.poll(_snap([_miner(1, 1300)]))
    assert day.events == [MORNING]  # one poll at max so far

    day.poll(_snap([_miner(1, 1300)]))
    assert day.events == [MORNING, ALL_MAX]


def test_a_step_down_before_all_max_says_nothing() -> None:
    day = Day()
    day.poll(_snap([_miner(1, 900, mining=False), _miner(2, 900, mining=False)]))
    day.poll(_snap([_miner(1, 1100), _miner(2, 900, mining=False)]))
    day.poll(_snap([_miner(1, 900), _miner(2, 900, mining=False)]))

    assert day.events == [MORNING]


def test_a_restart_of_ha_repeats_nothing_and_keeps_the_morning() -> None:
    day = _sunny_day()
    saved = day.report.as_dict()

    again = Day(DayReport(saved), start=day.now)
    again.poll(_snap([_miner(1, 1300), _miner(2, 1300)], pv=3400))
    again.poll(_snap([_miner(1, 1300), _miner(2, 1300)], pv=3400))

    assert again.events == []
    assert again.report.state["first_start_at"] == "2026-10-12T07:02:00+03:00"


def test_loaded_mid_morning_with_miners_mining_doesnt_greet() -> None:
    day = Day(start=T0 + timedelta(hours=3))
    day.poll(_snap([_miner(1, 1100)]))

    assert day.events == []
    assert day.report.state["first_start_at"] is None


def test_picked_up_in_the_afternoon_skips_the_greeting_but_sends_the_evening() -> None:
    """HA down overnight, back at 15:00 with miners mining: the rest of the day still counts."""
    day = Day(start=T0 + timedelta(hours=8))
    day.poll(_snap([_miner(1, 1100)], pv=2000))
    day.poll(_snap([_miner(1, 1100)], pv=2000))
    day.poll(_snap([_miner(1, 900, mining=False)]), sunset=True)

    assert day.events == [EVENING]
    assert day.report.state["pv_kwh"] > 0
    assert "mining" not in day.texts[EVENING].split("\n")[1]  # no first start to give a window


def test_the_next_day_begins_with_the_sunrise() -> None:
    day = _sunny_day()
    day.poll(_snap([_miner(1, 900, mining=False), _miner(2, 900, mining=False)]), minutes=60 * 23)
    day.poll(_snap([_miner(1, 900), _miner(2, 900, mining=False)]))

    assert day.events[-1] == MORNING and day.events.count(MORNING) == 2


def test_energy_is_added_up_per_poll_and_a_long_gap_is_not_filled() -> None:
    day = Day()
    day.poll(_snap([_miner(1, 900, mining=False)], pv=0))
    day.poll(_snap([_miner(1, 1200)], pv=3000, grid=-600))  # 1 min
    day.poll(_snap([_miner(1, 1200)], pv=3000, grid=-600))  # 1 min
    day.poll(_snap([_miner(1, 1200)], pv=3000, grid=-600), minutes=10)  # a gap: not counted
    state = day.report.state

    assert round(state["pv_kwh"], 3) == 0.1  # 3 kW for 2 minutes
    assert round(state["miners_kwh"], 3) == 0.04
    assert round(state["import_kwh"], 3) == 0.02
    assert state["mining_minutes"] == {"192.0.2.1": 2.0}


def test_messages_leave_out_what_is_missing() -> None:
    day = Day()
    day.poll(_snap([_miner(1, 900, mining=False)]))
    day.poll(_snap([_miner(1, 900)]))  # no forecast sensors
    day.poll(_snap([_miner(1, 900, mining=False)]), sunset=True)

    assert day.texts[MORNING] == "☀️ Production is up: Miner 1 started at 900 W (07:02)."
    assert "Forecast" not in day.texts[EVENING]
    assert day.texts[EVENING].endswith("Peak: 0 of 1 miners at their top step.")


def test_a_stop_reads_as_stopped() -> None:
    day = _sunny_day()
    day.poll(_snap([_miner(1, 1300), _miner(2, 1300, mining=False)], forecast_now=5000))
    assert STEP_DOWN not in day.events  # one poll could be a glitch
    day.poll(_snap([_miner(1, 1300), _miner(2, 1300, mining=False)], forecast_now=5000))

    assert day.texts[STEP_DOWN].startswith("🌥 Stepping down: Miner 2 stopped.")
    assert day.texts[STEP_DOWN].endswith("Forecast 5.0 kW.")  # no PV reading: only the forecast


def test_a_restart_after_a_limit_change_is_not_a_stop() -> None:
    """Every limit change restarts the miner: no power for a minute or two (2026-10-08)."""
    day = _sunny_day()
    for _ in range(4):
        day.poll(_snap([_miner(1, 1300), _miner(2, 1300, mining=False)]), restarting={"192.0.2.2"})
    day.poll(_snap([_miner(1, 1300), _miner(2, 1300)]))

    assert STEP_DOWN not in day.events


def test_a_step_down_while_the_miner_is_unavailable_is_still_seen() -> None:
    day = _sunny_day()
    gone = _miner(2, 1100, mining=False)
    gone.is_available = False
    day.poll(_snap([_miner(1, 1300), gone]))
    day.poll(_snap([_miner(1, 1300), _miner(2, 1100)]))

    assert day.events[-1] == STEP_DOWN
    assert day.texts[STEP_DOWN].startswith("🌥 Stepping down: Miner 2 1,300 W → 1,100 W.")


def test_the_reason_comes_only_from_a_plan_that_lowers_the_miner() -> None:
    day = Day()
    day.poll(_snap([_miner(1, 900, mining=False)]))
    up = {"192.0.2.1": MinerPlan(ACTION_SET_LIMIT, limit_w=1300.0, reason="room in the budget")}
    day.poll(_snap([_miner(1, 900)], plans=up))
    day.poll(_snap([_miner(1, 1300)]))
    day.poll(_snap([_miner(1, 1300)]))
    day.poll(_snap([_miner(1, 1100)]))  # lowered by hand: no plan said why

    assert day.events[-1] == STEP_DOWN
    assert day.texts[STEP_DOWN].splitlines()[0] == "🌥 Stepping down: Miner 1 1,300 W → 1,100 W."


def test_a_missing_miner_means_not_all_at_max() -> None:
    day = Day()
    day.poll(_snap([_miner(1, 900, mining=False), _miner(2, 900, mining=False)]))
    day.poll(_snap([_miner(1, 900), _miner(2, 900)]))
    gone = _miner(2, 1300)
    gone.is_available = False
    for _ in range(3):
        day.poll(_snap([_miner(1, 1300), gone]))
    assert ALL_MAX not in day.events

    day.poll(_snap([_miner(1, 1300), _miner(2, 1300)]))
    day.poll(_snap([_miner(1, 1300), _miner(2, 1300)]))
    assert day.texts[ALL_MAX].startswith("⚡ All 2 miners")


def test_a_slow_polling_interval_still_counts_the_energy() -> None:
    day = Day()
    day.poll(_snap([_miner(1, 900, mining=False)], pv=0), poll_s=600)
    day.poll(_snap([_miner(1, 1200)], pv=3000), minutes=10, poll_s=600)

    assert round(day.report.state["pv_kwh"], 3) == 0.5  # 3 kW for 10 minutes


def test_the_saved_day_is_a_copy() -> None:
    day = _sunny_day()
    saved = day.report.as_dict()
    day.poll(_snap([_miner(1, 1300), _miner(2, 1300)]))

    assert saved["sent"] == [MORNING, ALL_MAX] and saved is not day.report.state
    assert saved["mining_minutes"] is not day.report.state["mining_minutes"]
