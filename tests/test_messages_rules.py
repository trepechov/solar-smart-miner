"""The day messages (plan 2026-10-15-002): the sun-driven policy's rules, one group each."""
from __future__ import annotations

from datetime import datetime, timedelta

from custom_components.solar_smart_miner.messages import DayMessages, policy_for
from custom_components.solar_smart_miner.messages.context import DayContext
from custom_components.solar_smart_miner.protocols import (
    ACTION_SET_LIMIT,
    ACTION_START,
    ACTION_STOP,
    CoordinatorSnapshot,
    Decision,
    EnergySnapshot,
    MinerPlan,
    MinerSnapshot,
)

STEPS = [900.0, 1100.0, 1300.0]  # lowest 900 W, top 1300 W
BAND_TOP = 70.0
T0 = datetime.fromisoformat("2026-10-12T07:00:00+03:00")
POLICY = policy_for("solar_follow")


def _miner(n: int, limit: float | None = 1300.0, *, mining: bool = True, temp: float = 50.0) -> MinerSnapshot:
    return MinerSnapshot(
        miner_id=f"192.0.2.{n}", ip=f"192.0.2.{n}", power_w=limit if mining else 0.0,
        power_limit_w=limit, min_power_w=500.0, max_power_w=3500.0, temperature_c=temp,
        is_available=True, power_limit_entity_id=None, hashrate_th=50.0 if mining else 0.0,
        name=f"Miner {n}", is_stopped=not mining,
    )


def _off(*ns: int) -> list[MinerSnapshot]:
    return [_miner(n, 900, mining=False) for n in ns]


def _snap(miners, *, pv=None, forecast_now=None, next_hour=None, remaining=None, plans=None):
    energy = EnergySnapshot(
        solar_production_w=None, pv_power_w=pv, forecast_now_w=forecast_now,
        forecast_next_hour_w=next_hour, forecast_remaining_kwh=remaining,
        miner_consumption_sum_w=sum(m.power_w or 0.0 for m in miners),
    )
    return CoordinatorSnapshot(energy=energy, miners=list(miners), decision=Decision("", plans=plans or {}))


class Day:
    """Feeds polls a minute apart to one DayMessages and collects what it sends."""

    def __init__(self, messages: DayMessages | None = None, start: datetime = T0) -> None:
        self.messages = messages or DayMessages()
        self.now = start
        self.events: list[str] = []
        self.texts: dict[str, str] = {}

    @property
    def state(self) -> dict:
        return self.messages.state

    def start(self, miner: str = "Miner 1") -> None:
        """The integration started a miner (its command finished ok), sent a minute from now."""
        self.messages.note_command((self.now + timedelta(minutes=1)).isoformat(timespec="seconds"), miner, ACTION_START)

    def poll(
        self, snapshot, *, minutes: float = 1, settled=True, sun_up=True, rising=True, sunset=False,
        restarting=frozenset(), mode=None, battery=False, picking_up=False, policy=POLICY,
    ):
        self.now += timedelta(minutes=minutes)
        ctx = DayContext(
            now=self.now, snapshot=snapshot, settled=settled, restarting=set(restarting),
            steps=STEPS, too_warm_c=BAND_TOP, sun_up=sun_up, rising=rising, sunset=sunset,
            mode=mode, battery=battery, picking_up=picking_up,
        )
        events = self.messages.update(ctx, policy)
        for name, text in events:
            self.texts[name] = text
        self.events += [name for name, _ in events]
        return events


def _morning() -> Day:
    """Night, then the integration starts miner 1 at 07:02."""
    day = Day()
    day.poll(_snap(_off(1, 2)), sun_up=False)  # 07:01, the day begins
    day.start()
    day.poll(_snap([_miner(1, 900), *_off(2)], remaining=31.4, next_hour=2500))  # 07:02
    return day


def _at_peak() -> Day:
    day = _morning()
    day.poll(_snap([_miner(1, 1300), _miner(2, 1300)], pv=3400), minutes=120)
    day.poll(_snap([_miner(1, 1300), _miner(2, 1300)], pv=3400))
    return day


# --- the policy ---------------------------------------------------------------


def test_a_sunny_day_sends_each_message_once_in_order() -> None:
    day = _at_peak()
    down = {"192.0.2.2": MinerPlan(ACTION_SET_LIMIT, limit_w=1100.0, reason="import 520 W for 5 min")}
    day.poll(_snap([_miner(1, 1300), _miner(2, 1300)], pv=3000, plans=down), minutes=360, rising=False)
    day.poll(_snap([_miner(1, 1300), _miner(2, 1100)], pv=2900, forecast_now=4000, next_hour=3000), rising=False)
    day.poll(_snap([_miner(1, 1100), _miner(2, 900)], pv=1500), rising=False)  # later step downs: quiet
    day.poll(_snap(_off(1, 2)), minutes=120, rising=False)
    day.poll(_snap(_off(1, 2)), sun_up=False, rising=False)

    assert day.events == ["morning", "peak", "going-down", "evening"]
    assert day.texts["morning"] == (
        "☀️ Production is up (07:02).\n"
        "Forecast: 31.4 kWh for the rest of today, 2.5 kW next hour.\n"
        "• Miner 1: mining at 900 W\n"
        "• Miner 2: stopped"
    )
    assert day.texts["peak"].startswith(
        "⚡ All 2 miners run at their top step, settled (09:03, PV 3.4 kW).\n"
        "It took 2 h 1 min from the morning (07:02).\n"
        "• Miner 1: mining at 1,300 W"
    )
    assert day.texts["going-down"].startswith(
        "🌇 Going down: Miner 2 1,300 W → 1,100 W (import 520 W for 5 min).\n"
        "Forecast 4.0 kW, 3.0 kW next hour; panels give 2.9 kW.\n"
    )
    assert day.texts["evening"].startswith(
        "🌙 Day done: the sun has set (17:06).\n"
        "The last miner stopped at 15:05.\n"
        "No cloud step-downs.\n"
    )


def test_a_profile_without_a_policy_sends_nothing() -> None:
    day = Day()
    day.start()
    day.poll(_snap([_miner(1, 900)]), policy=policy_for("battery_focused"))

    assert policy_for("battery_focused") == () and day.events == []


def test_each_message_once_a_day_and_again_the_next_day() -> None:
    day = _at_peak()
    day.poll(_snap(_off(1, 2)), minutes=60 * 23, sun_up=False)  # the next day, at night
    day.start()
    day.poll(_snap([_miner(1, 900), *_off(2)]))

    assert day.events == ["morning", "peak", "day-report", "morning"]  # yesterday's report first


# --- morning ------------------------------------------------------------------


def test_a_start_by_hand_at_night_sends_nothing_and_the_morning_still_comes() -> None:
    """2026-10-14: a miner started by hand at 01:28 for 40 s sent "Production is up" and
    "Day done" at night, and the real morning never came."""
    day = Day(start=datetime.fromisoformat("2026-10-14T01:27:00+03:00"))
    day.poll(_snap([_miner(2, 900), *_off(1)]), sun_up=False)
    day.poll(_snap(_off(1, 2)), sun_up=False)
    day.poll(_snap(_off(1, 2)), sun_up=False)
    assert day.events == []

    day.poll(_snap([_miner(1, 900), *_off(2)], pv=950), minutes=400)
    day.poll(_snap([_miner(1, 900), *_off(2)], pv=1000))
    assert day.events == ["morning"]
    assert day.texts["morning"].startswith("☀️ Production is up (08:11).")


def test_a_house_load_switching_off_at_sunrise_is_not_the_morning() -> None:
    day = Day()
    for pv in (250, 450, 450, 450):  # 200 W more on the meter: still below the lowest step
        day.poll(_snap(_off(1), pv=pv))

    assert day.events == [] and day.state["first_production_at"] is None


def test_picked_up_with_the_miners_mining_sends_the_morning_at_once() -> None:
    """2026-10-15: HA back at 10:08 with the miners already mining (started by hand)."""
    day = Day(start=datetime.fromisoformat("2026-10-15T10:07:00+03:00"))
    day.poll(_snap([_miner(1, 1100), _miner(2, 1100), _miner(3, 1100)]), picking_up=True)

    assert day.events == ["morning"]
    assert day.texts["morning"] == (
        "☀️ Picked up the day at 10:08: production is already up.\n"
        "• Miner 1: mining at 1,100 W\n"
        "• Miner 2: mining at 1,100 W\n"
        "• Miner 3: mining at 1,100 W"
    )
    assert day.state["picked_up"] and day.state["forecast_at_start_kwh"] is None

    for _ in range(2):
        day.poll(_snap([_miner(1, 1300), _miner(2, 1300), _miner(3, 1300)]))
    assert day.events == ["morning", "peak"]
    assert "from the morning (10:08)" in day.texts["peak"]


def test_picked_up_on_the_same_date_after_a_restart() -> None:
    """HA ran past midnight (the day was saved at 03:00), then restarted at 10:08."""
    night = Day(start=datetime.fromisoformat("2026-10-15T02:59:00+03:00"))
    night.poll(_snap(_off(1)), sun_up=False)

    day = Day(DayMessages(night.messages.as_dict()), start=datetime.fromisoformat("2026-10-15T10:07:00+03:00"))
    day.poll(_snap([_miner(1, 1100)]), picking_up=True)

    assert day.events == ["morning"] and day.texts["morning"].startswith("☀️ Picked up the day at 10:08")


def test_picked_up_at_night_waits_for_the_day() -> None:
    day = Day(start=datetime.fromisoformat("2026-10-15T05:00:00+03:00"))
    day.poll(_snap([_miner(1, 900)]), sun_up=False, picking_up=True)

    assert day.events == []


def test_a_start_by_hand_in_daylight_doesnt_open_the_morning() -> None:
    day = Day()
    day.poll(_snap(_off(1)))
    day.poll(_snap([_miner(1, 900)]))  # no command of ours, no PV reading
    day.poll(_snap([_miner(1, 900)]))

    assert day.events == []


def test_an_own_start_on_an_overcast_day_is_usable_production() -> None:
    """A Manual-mode Apply counts too: the controller sent it and it finished ok."""
    day = Day()
    day.poll(_snap(_off(1), pv=300))
    day.start()
    day.poll(_snap([_miner(1, 900)], pv=400), mode="Manual")
    day.poll(_snap(_off(1), pv=200), minutes=600, sun_up=False)

    assert day.events == ["morning", "evening"]
    assert day.texts["morning"].endswith("• Miner 1: mining at 900 W\nMode: Manual")
    assert "No usable production" not in day.texts["evening"]


def test_the_morning_uses_the_time_the_start_was_sent() -> None:
    day = Day()
    day.poll(_snap(_off(1)))
    day.start()  # sent at 07:02
    day.poll(_snap(_off(1)), minutes=4)  # it finishes ok later; the morning says 07:02
    day.poll(_snap([_miner(1, 900)]))

    assert day.texts["morning"].startswith("☀️ Production is up (07:02).")


def test_the_morning_forecast_is_the_last_one_before_production() -> None:
    day = Day()
    day.poll(_snap(_off(1), remaining=30.0), sun_up=False)
    day.poll(_snap(_off(1), remaining=32.5))
    day.poll(_snap([_miner(1, 900)], pv=950, remaining=31.0))
    day.poll(_snap([_miner(1, 900)], pv=950, remaining=30.9))

    assert day.events == ["morning"] and day.state["forecast_at_start_kwh"] == 31.0


def test_without_sun_sun_an_own_start_opens_and_the_sunset_closes() -> None:
    day = Day()
    day.start()
    day.poll(_snap([_miner(1, 900)]), sun_up=None, rising=None)
    day.poll(_snap(_off(1)), minutes=600, sun_up=None, rising=None)
    day.poll(_snap(_off(1)), sun_up=None, rising=None, sunset=True)

    assert day.events == ["morning", "evening"]


def test_the_messages_leave_out_what_is_missing() -> None:
    day = Day()
    day.start()
    day.poll(_snap([_miner(1, 900)]))  # no forecast sensors

    assert day.texts["morning"] == "☀️ Production is up (07:01).\n• Miner 1: mining at 900 W"


# --- peak ---------------------------------------------------------------------


def test_peak_waits_for_settling_the_band_and_a_second_poll() -> None:
    day = Day()
    day.start()
    day.poll(_snap([_miner(1, 900)]))
    day.poll(_snap([_miner(1, 1300)]), settled=False)
    day.poll(_snap([_miner(1, 1300)]), settled=False)
    day.poll(_snap([_miner(1, 1300, temp=BAND_TOP)]))
    day.poll(_snap([_miner(1, 1300)]))
    assert day.events == ["morning"]  # one poll at max so far

    day.poll(_snap([_miner(1, 1300)]))
    assert day.events == ["morning", "peak"]


def test_a_missing_miner_means_not_at_peak() -> None:
    day = Day()
    day.start()
    day.poll(_snap([_miner(1, 900), _miner(2, 900)]))
    gone = _miner(2, 1300)
    gone.is_available = False
    for _ in range(3):
        day.poll(_snap([_miner(1, 1300), gone]))
    assert "peak" not in day.events
    assert day.texts.get("peak") is None

    day.poll(_snap([_miner(1, 1300), _miner(2, 1300)]))
    day.poll(_snap([_miner(1, 1300), _miner(2, 1300)]))
    assert day.texts["peak"].startswith("⚡ All 2 miners")


def test_no_peak_without_the_morning() -> None:
    day = Day()
    for _ in range(3):
        day.poll(_snap([_miner(1, 1300)]))  # started by hand, no PV reading

    assert day.events == []


# --- going down ---------------------------------------------------------------


def test_a_step_down_before_noon_is_a_cloud() -> None:
    day = _at_peak()
    day.poll(_snap([_miner(1, 1300), _miner(2, 1100)], forecast_now=4000, next_hour=3000))

    assert "going-down" not in day.events and day.state["cloud_step_downs"] == 1


def test_three_miners_stepping_down_in_one_poll_are_one_cloud() -> None:
    day = Day()
    day.start()
    day.poll(_snap([_miner(1, 1300), _miner(2, 1300), _miner(3, 1300)]))
    day.poll(_snap([_miner(1, 1100), _miner(2, 1100), _miner(3, 1100)]))

    assert day.state["cloud_step_downs"] == 1


def test_a_cloud_at_noon_then_the_evening_trend_at_five() -> None:
    day = _at_peak()
    day.poll(_snap([_miner(1, 1300), _miner(2, 1100)], forecast_now=6000, next_hour=6500), minutes=180)  # 12:04
    day.poll(_snap([_miner(1, 1300), _miner(2, 1300)]))
    day.poll(_snap([_miner(1, 1300), _miner(2, 1100)], forecast_now=3000, next_hour=1800), minutes=270, rising=False)
    day.poll(_snap(_off(1, 2)), minutes=90, sun_up=False, rising=False)

    assert day.events == ["morning", "peak", "going-down", "evening"]
    assert day.texts["going-down"].startswith("🌇 Going down: Miner 2 1,300 W → 1,100 W.")
    assert "Cloud step-downs: 1." in day.texts["evening"]


def test_going_down_needs_the_forecast_falling_when_there_is_one() -> None:
    day = _at_peak()
    day.poll(_snap([_miner(1, 1300), _miner(2, 1100)], forecast_now=3000, next_hour=3500), rising=False)

    assert "going-down" not in day.events and day.state["cloud_step_downs"] == 1


def test_going_down_without_a_forecast_after_noon() -> None:
    day = _at_peak()
    day.poll(_snap([_miner(1, 1300), _miner(2, 1100)]), rising=False)

    assert day.events[-1] == "going-down"


def test_a_stop_reads_as_stopped_on_the_second_poll() -> None:
    day = _at_peak()
    day.poll(_snap([_miner(1, 1300), _miner(2, 1300, mining=False)], forecast_now=5000), rising=False)
    assert "going-down" not in day.events  # one poll could be a glitch
    day.poll(_snap([_miner(1, 1300), _miner(2, 1300, mining=False)], forecast_now=5000), rising=False)

    assert day.texts["going-down"].startswith("🌇 Going down: Miner 2 stopped.\nForecast 5.0 kW.")


def test_a_restart_after_a_limit_change_is_not_a_stop() -> None:
    """Every limit change restarts the miner: no power for a minute or two (2026-10-08)."""
    day = _at_peak()
    for _ in range(4):
        day.poll(_snap([_miner(1, 1300), _miner(2, 1300, mining=False)]), restarting={"192.0.2.2"}, rising=False)
    day.poll(_snap([_miner(1, 1300), _miner(2, 1300)]), rising=False)

    assert "going-down" not in day.events and day.state["cloud_step_downs"] == 0


def test_a_step_down_while_the_miner_is_unavailable_is_still_seen() -> None:
    day = _at_peak()
    gone = _miner(2, 1100, mining=False)
    gone.is_available = False
    day.poll(_snap([_miner(1, 1300), gone]), rising=False)
    day.poll(_snap([_miner(1, 1300), _miner(2, 1100)]), rising=False)

    assert day.texts["going-down"].startswith("🌇 Going down: Miner 2 1,300 W → 1,100 W.")


def test_the_reason_comes_only_from_a_plan_that_lowers_the_miner() -> None:
    day = Day()
    day.start()
    up = {"192.0.2.1": MinerPlan(ACTION_SET_LIMIT, limit_w=1300.0, reason="room in the budget")}
    day.poll(_snap([_miner(1, 900)], plans=up))
    day.poll(_snap([_miner(1, 1300)]))
    day.poll(_snap([_miner(1, 1100)]), rising=False)  # lowered by hand: no plan said why

    assert day.texts["going-down"].splitlines()[0] == "🌇 Going down: Miner 1 1,300 W → 1,100 W."


# --- evening ------------------------------------------------------------------


def test_an_overcast_day_still_gets_its_evening() -> None:
    day = Day()
    day.poll(_snap(_off(1, 2), pv=200))
    day.poll(_snap(_off(1, 2), pv=0), minutes=600, sun_up=False)

    assert day.events == ["evening"]
    assert day.texts["evening"].startswith("🌙 Day done: the sun has set (17:01).\nNo usable production today.\n")


def test_no_evening_before_the_sun_was_seen_up() -> None:
    day = Day(start=datetime.fromisoformat("2026-10-15T00:01:00+03:00"))
    day.poll(_snap(_off(1)), sun_up=False)

    assert day.events == []


def test_with_a_battery_the_day_isnt_called_done() -> None:
    day = _at_peak()
    day.poll(_snap([_miner(1, 900), _miner(2, 900)]), minutes=600, sun_up=False, battery=True)

    assert day.texts["evening"].startswith("🌙 Sunset (19:03).\n")
    assert "Still mining after sunset: Miner 1, Miner 2." in day.texts["evening"]
    assert "Day done" not in day.texts["evening"]


def test_the_evening_gives_the_peak_when_it_never_came() -> None:
    day = _morning()
    day.poll(_snap(_off(1, 2)), minutes=600, sun_up=False)

    assert "Peak: 0 of 2 miners at their top step." in day.texts["evening"]


def test_the_mode_is_named_unless_automatic() -> None:
    day = _morning()
    day.poll(_snap(_off(1, 2)), minutes=600, sun_up=False, mode="Manual")

    assert day.texts["evening"].endswith("• Miner 2: stopped\nMode: Manual")
    assert "Mode" not in day.texts["morning"]


# --- the day's state ----------------------------------------------------------


def test_a_restart_of_ha_repeats_nothing_and_keeps_the_morning() -> None:
    day = _at_peak()
    again = Day(DayMessages(day.messages.as_dict()), start=day.now)
    again.poll(_snap([_miner(1, 1300), _miner(2, 1300)], pv=3400), picking_up=True)
    again.poll(_snap([_miner(1, 1300), _miner(2, 1300)], pv=3400))

    assert again.events == []
    assert again.state["first_production_at"] == "2026-10-12T07:02:00+03:00"


def test_a_day_saved_by_0_8_6_loads_with_todays_names() -> None:
    saved = {"date": "2026-10-12", "sent": ["morning", "all_max"], "first_start_at": "x", "pv_kwh": 3.0}
    messages = DayMessages(saved)

    assert messages.state["sent"] == ["morning", "peak"]
    assert "first_start_at" not in messages.state and "pv_kwh" not in messages.state


def test_the_day_records_every_kind_of_command_and_starts_afresh() -> None:
    messages = DayMessages({"date": "2026-10-12"})
    messages.note_command("2026-10-12T07:02:00+03:00", "Miner 1", ACTION_START)
    messages.note_command("2026-10-12T09:00:00+03:00", "Miner 1", ACTION_SET_LIMIT)
    messages.note_command("2026-10-12T18:00:00+03:00", "Miner 1", ACTION_STOP)
    messages.note_command("2026-10-13T00:00:05+03:00", "Miner 2", ACTION_START)  # just before the poll
    assert [c["kind"] for c in messages.state["commands"]] == ["start", "limit", "stop", "start"]

    day = Day(messages, start=datetime.fromisoformat("2026-10-13T00:00:00+03:00"))
    day.poll(_snap(_off(1)), sun_up=False)
    assert messages.state["commands"] == [{"at": "2026-10-13T00:00:05+03:00", "miner": "Miner 2", "kind": "start"}]


def test_the_saved_day_is_a_copy() -> None:
    day = _at_peak()
    saved = day.messages.as_dict()
    day.poll(_snap([_miner(1, 1300), _miner(2, 1300)]))

    assert saved["sent"] == ["morning", "peak"] and saved is not day.state
    assert saved["commands"] is not day.state["commands"]
