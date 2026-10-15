"""The day's totals and the day report (plan 2026-10-15-002, U5 and U6)."""
from __future__ import annotations

from datetime import datetime, timedelta
import json
from pathlib import Path
from zoneinfo import ZoneInfo

from custom_components.solar_smart_miner.messages import DayMessages, policy_for
from custom_components.solar_smart_miner.messages.context import DayContext
from custom_components.solar_smart_miner.messages.day_report import (
    TEST_LINE,
    forecast_ratio,
    format_text,
    start_burst,
)
from custom_components.solar_smart_miner.protocols import (
    ACTION_SET_LIMIT,
    ACTION_START,
    ACTION_STOP,
    CoordinatorSnapshot,
    Decision,
    EnergySnapshot,
    MinerSnapshot,
)

FIXTURE = Path(__file__).parent / "fixtures" / "day_2026-10-12.json"
STEPS = [900.0, 1100.0, 1300.0, 1500.0, 1700.0, 1900.0, 2100.0, 2300.0, 2500.0]
POLICY = policy_for("solar_follow")


def _ctx(now, snapshot, *, sun_up=True) -> DayContext:
    return DayContext(
        now=now, snapshot=snapshot, settled=True, restarting=set(), steps=STEPS, too_warm_c=70.0,
        sun_up=sun_up, rising=True, sunset=False,
    )


def _miner(n: int, *, w: float | None = None, th: float | None = None, limit: float | None = 900.0,
           available: bool = True) -> MinerSnapshot:
    mining = bool(w) and bool(th)
    return MinerSnapshot(
        miner_id=f"m{n}", ip=f"m{n}", power_w=w, power_limit_w=limit, min_power_w=900.0,
        max_power_w=2500.0, temperature_c=50.0, is_available=available, power_limit_entity_id=None,
        hashrate_th=th, name=f"Miner {n}", is_stopped=not mining,
    )


def _snap(miners=(), *, pv=None, grid=None, remaining=None) -> CoordinatorSnapshot:
    energy = EnergySnapshot(solar_production_w=None, pv_power_w=pv, grid_net_w=grid, forecast_remaining_kwh=remaining)
    return CoordinatorSnapshot(energy=energy, miners=list(miners), decision=Decision(""))


# --- the reference day ----------------------------------------------------------


def _replay_reference_day(poll_s: int = 15) -> DayMessages:
    """2026-10-12 from the farm's recorder, polled every `poll_s` like the coordinator does."""
    data = json.loads(FIXTURE.read_text())
    tz = ZoneInfo(data["tz"])
    midnight = datetime.fromisoformat(data["date"]).replace(tzinfo=tz)
    series = data["series"]
    cursor = dict.fromkeys(series, 0)
    value: dict[str, float | str | None] = dict.fromkeys(series)
    messages = DayMessages()
    last_action = None
    for k in range(0, 86400 // poll_s + 2):
        t = k * poll_s
        for name, rows in series.items():
            while cursor[name] < len(rows) and rows[cursor[name]][0] <= t:
                value[name] = rows[cursor[name]][1]
                cursor[name] += 1
        now = midnight + timedelta(seconds=t)
        action = value["last_action"]
        if action != last_action and isinstance(action, str) and action.endswith("(ok)") and k > 0:
            miner, plan = action.split(": ", 1)
            kind = ACTION_STOP if "stop" in plan else ACTION_START if "start" in plan else ACTION_SET_LIMIT
            messages.note_command(now.isoformat(timespec="seconds"), miner, kind)
        last_action = action if action is not None else last_action
        miners = [
            _miner(n, w=value[f"miner{n}_power_draw"], th=value[f"miner{n}_hashrate"],
                   limit=value[f"miner{n}_power_limit"], available=value[f"miner{n}_power_draw"] is not None)
            for n in (1, 2, 3)
        ]
        snapshot = _snap(miners, pv=value["pv"], grid=value["grid_net"], remaining=value["forecast_remaining_kwh"])
        messages.update(_ctx(now, snapshot, sun_up=7 <= now.hour < 18), POLICY)
    return messages


def test_the_reference_day_adds_up_like_the_meters() -> None:
    """AE7. The inverters' counters said 42.1 kWh; 0.8.4 counted 29.6 (a missing PV reading as 0 W)."""
    messages = _replay_reference_day()
    kept = messages.state["kept_days"]
    assert [k["date"] for k in kept] == ["2026-10-12"]
    d = kept[0]["report"]
    t = d["totals"]

    assert abs(t["solar"] - 42.1) <= 0.3, t
    assert abs(t["miners"] - 45.8) <= 0.5, t
    assert abs(t["import"] - 13.0) <= 0.5, t
    assert t["export"] < 0.1
    assert not d["incomplete"]
    assert d["light"][0] in ("07:25", "07:26", "07:27") and d["light"][1] in ("18:09", "18:10", "18:11")
    # The power readings peak at 12:15 (7.5 kW); the yield counters the prototype reads put
    # their best slice at 11:00 (8.2 kW): the two disagree within the quarter hour, not the day.
    assert d["best_slice"]["at"] == "12:15" and abs(d["best_slice"]["kw"] - 7.5) <= 0.2
    assert d["self_use_pct"] == 100
    flip = next(m for m in d["miners"] if m["name"] == "Miner 1")
    assert start_burst(flip["starts"])[0] == "07:47"


def test_the_reference_day_report_reads_like_the_prototype() -> None:
    text = format_text(_replay_reference_day().state["kept_days"][0]["report"])

    assert text.startswith("📊 Farm day: Monday 12 October 2026\n\n☀️ Solar: 42.")
    assert "🏠 House & other: " in text and "the rest of the balance" in text
    assert "⚠️ Miner 1 started 4× between 07:47 and 08:29 (start/stop flip-flop)" in text
    assert "incomplete" not in text


# --- the gaps -------------------------------------------------------------------


T0 = datetime.fromisoformat("2026-10-12T00:00:00+03:00")


def _poll(messages: DayMessages, now: datetime, snapshot: CoordinatorSnapshot, *, sun_up=True):
    return messages.update(_ctx(now, snapshot, sun_up=sun_up), POLICY)


def test_a_one_minute_pv_gap_holds_the_last_reading() -> None:
    messages = DayMessages()
    now = T0
    for k in range(0, 12):  # 3 minutes at 15 s; the PV reading drops out for 60 s
        pv = None if 4 <= k < 8 else 3000.0
        _poll(messages, now + timedelta(seconds=15 * k), _snap(pv=pv, grid=-500))
    totals = messages.state["totals"]

    assert round(totals["solar_kwh"], 4) == round(3000 * 165 / 3600 / 1000, 4)
    assert not totals["incomplete"]


def test_a_long_pv_gap_is_not_filled_and_the_day_is_incomplete() -> None:
    messages = DayMessages()
    for k in range(0, 60):  # the PV gone for 13 minutes
        pv = None if k >= 6 else 3000.0
        _poll(messages, T0 + timedelta(seconds=15 * k), _snap(pv=pv, grid=-500))
    totals = messages.state["totals"]

    assert round(totals["solar_kwh"], 4) == round(3000 * 675 / 3600 / 1000, 4)  # last read at 75 s, held 600 s
    assert totals["incomplete"]


def test_a_reading_that_isnt_configured_leaves_its_line_out() -> None:
    messages = DayMessages()
    for k in range(4):
        _poll(messages, T0 + timedelta(minutes=k), _snap(grid=-500))
    d = messages.report()

    assert not d["incomplete"] and not d["solar_known"]
    assert "Solar" not in format_text(d) and "% of the solar" not in format_text(d)


def test_ha_down_over_midnight_marks_both_days_incomplete() -> None:
    messages = DayMessages()
    _poll(messages, T0.replace(hour=21), _snap(pv=0, grid=-400), sun_up=False)
    _poll(messages, T0.replace(hour=22), _snap(pv=0, grid=-400), sun_up=False)  # last poll at 22:00
    events = _poll(messages, T0 + timedelta(days=1, hours=8), _snap(pv=500, grid=-400))

    assert events[0][0] == "day-report"
    assert events[0][1].rstrip().endswith("Home Assistant wasn't running all day: the figures are incomplete")
    assert messages.state["totals"]["incomplete"]  # today began at 08:00


def test_the_night_import_counts_without_mining() -> None:
    messages = DayMessages()
    for k in range(61):
        _poll(messages, T0 + timedelta(minutes=k), _snap(pv=0, grid=-450), sun_up=False)

    assert round(messages.state["totals"]["import_kwh"], 3) == 0.45


def test_a_paused_miner_adds_nothing() -> None:
    messages = DayMessages()
    for k in range(5):
        _poll(messages, T0 + timedelta(minutes=k), _snap([_miner(1, w=None, th=None)], pv=0, grid=-400))
    d = messages.report()
    m = d["miners"][0]

    assert m["kwh"] == 0 and m["hours"] == 0 and m["j_per_th"] is None
    assert "⛏️ Miners: no mining today" in format_text(d)


# --- the report -----------------------------------------------------------------


def test_the_report_goes_out_once_after_midnight_and_not_again_after_a_restart() -> None:
    messages = DayMessages()
    _poll(messages, T0.replace(hour=23, minute=59), _snap(pv=0, grid=-400), sun_up=False)
    events = _poll(messages, T0 + timedelta(days=1, seconds=10), _snap(pv=0, grid=-400), sun_up=False)
    assert [name for name, _ in events] == ["day-report"]

    again = DayMessages(messages.as_dict())
    assert _poll(again, T0 + timedelta(days=1, seconds=25), _snap(pv=0, grid=-400), sun_up=False) == []


def test_the_report_is_sent_on_the_next_poll_when_the_first_one_failed() -> None:
    messages = DayMessages()
    _poll(messages, T0.replace(hour=23, minute=59), _snap(pv=0, grid=-400), sun_up=False)
    messages._begin_day(T0 + timedelta(days=1, seconds=10))  # the poll after midnight broke here
    events = _poll(messages, T0 + timedelta(days=1, seconds=25), _snap(pv=0, grid=-400), sun_up=False)

    assert [name for name, _ in events] == ["day-report"]


def test_seven_days_are_kept() -> None:
    messages = DayMessages()
    for day in range(9):
        _poll(messages, T0 + timedelta(days=day, hours=12), _snap(pv=1000, grid=-100))
    kept = [k["date"] for k in messages.state["kept_days"]]

    assert kept == [f"2026-10-{d}" for d in range(13, 20)]  # the 12th dropped
    assert messages.report("2026-10-12") is None and messages.report("2026-10-13") is not None


def test_today_so_far_and_a_kept_day_on_request() -> None:
    messages = DayMessages()
    for k in range(3):
        _poll(messages, T0 + timedelta(hours=12, minutes=k), _snap(pv=3000, grid=-100))
    today = messages.report()

    assert today["date"] == "2026-10-12" and today["totals"]["solar"] > 0
    assert format_text(today, test=True).startswith(TEST_LINE + "\n\n📊 Farm day: Monday 12 October 2026")
    assert messages.report("2026-10-01") is None


def test_the_forecast_against_what_came() -> None:
    messages = DayMessages()
    _poll(messages, T0.replace(hour=6), _snap(pv=0, grid=-300, remaining=20.0), sun_up=False)
    _poll(messages, T0.replace(hour=7), _snap(pv=0, grid=-300, remaining=10.0))  # the last before production
    for k in range(1, 4 * 60 + 1):  # 4 h at 2.5 kW = 10 kWh... plus the hold
        _poll(messages, T0.replace(hour=7) + timedelta(minutes=k), _snap([_miner(1, w=900.0, th=20.0)], pv=2500, grid=-100))
    d = messages.report()

    assert d["forecast_kwh"] == 10.0
    assert round(forecast_ratio(d), 2) == 1.0
    assert "🔮 Forecast this morning: 10.0 kWh; the panels gave 10.0 kWh (100%)" in format_text(d)


def test_no_forecast_sensors_no_forecast_line() -> None:
    messages = DayMessages()
    for k in range(3):
        _poll(messages, T0 + timedelta(hours=12, minutes=k), _snap(pv=3000, grid=-100))
    d = messages.report()

    assert forecast_ratio(d) is None and "Forecast" not in format_text(d)
