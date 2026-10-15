"""scripts/farm_day.py: the farm day report's numbers and words (the prototype of the day report)."""
from __future__ import annotations

import datetime as dt
import importlib.util
from pathlib import Path
import sys
from zoneinfo import ZoneInfo

# Imported first so "custom_components" resolves to this repo, not the HA test harness's copy.
import custom_components.solar_smart_miner  # noqa: F401

_spec = importlib.util.spec_from_file_location("farm_day", Path(__file__).parent.parent / "scripts" / "farm_day.py")
farm_day = importlib.util.module_from_spec(_spec)
sys.modules["farm_day"] = farm_day  # dataclasses look their module up
_spec.loader.exec_module(farm_day)

TZ = ZoneInfo("Europe/Sofia")
DAY = dt.date(2026, 10, 12)
START = dt.datetime.combine(DAY, dt.time(), TZ)

FARM = {
    "solar_energy_today": ["sensor.pv_today"],
    "solar_power": ["sensor.pv_power"],
    "grid_import_total": "sensor.grid_in",
    "grid_export_total": "sensor.grid_out",
    "last_action": "sensor.last_action",
    "miners": [{"name": "Miner 1", "energy_total": "sensor.m1_energy", "hashrate": "sensor.m1_hashrate"}],
}


def row(hhmm: str, state, day_offset: int = 0) -> dict:
    h, m = map(int, hhmm.split(":"))
    t = START + dt.timedelta(days=day_offset, hours=h, minutes=m)
    return {"state": str(state), "last_changed": t.astimezone(dt.UTC).isoformat()}


def a_day() -> dict[str, list[dict]]:
    """10:00-11:00: the panels give 2 kWh, the miner uses 3 kWh, so the grid gives it 1 kWh.
    The house takes 0.5 kWh an hour from the grid all day."""
    pv = [row("00:00", 14.2), row("00:05", 0.0)] + [row(f"10:{m:02d}", round(2 * m / 60, 3)) for m in range(15, 61, 15) if m < 60] + [row("11:00", 2.0)]
    m1 = [row("00:00", 100.0)] + [row(f"10:{m:02d}", 100 + 3 * m / 60) for m in (15, 30, 45)] + [row("11:00", 103.0)]
    grid_in = [row(f"{h:02d}:00", 500 + 0.5 * h + (1.0 if h >= 11 else 0)) for h in range(24)] + [row("00:00", 512.0 + 1, 1)]
    return {
        "sensor.pv_today": pv,
        "sensor.pv_power": [row("00:00", 0), row("10:00", 2000), row("11:00", 0)],
        "sensor.grid_in": grid_in,
        "sensor.grid_out": [row("00:00", 7.0)],
        "sensor.m1_energy": m1,
        "sensor.m1_hashrate": [row("00:00", 0), row("10:00", 100.0), row("11:00", 0)],
        "sensor.last_action": [
            row("00:00", "Miner 1: stop (pause) (ok)"),  # carried over from the day before
            row("00:01", "unavailable"),
            row("00:02", "Miner 1: stop (pause) (ok)"),  # shown again after a restart, not a command
            row("09:58", "Miner 1: start at 900 W (pending)"),
            row("10:00", "Miner 1: start at 900 W (ok)"),
            row("10:20", "Miner 1: 2,500 W (ok)"),
            row("11:00", "Miner 1: stop (pause) (ok)"),
        ],
    }


def test_counter_bins_daily_reset_and_glitches() -> None:
    pts = farm_day.points([row("00:00", 14.2), row("00:10", 0.0), row("00:20", 0.3)], TZ)
    assert farm_day.counter_bins(pts, START, 2, daily_reset=True) == [0.0, 0.3]
    total = farm_day.points([row("00:00", 100.0), row("00:10", 90.0), row("00:20", 900.0)], TZ)
    assert farm_day.counter_bins(total, START, 2) == [0.0, 0.0]  # a fall and a jump are glitches


def test_ok_commands_skip_the_carried_over_state_and_its_repeat() -> None:
    cmds = farm_day.ok_commands(a_day()["sensor.last_action"], START, TZ)
    assert [c for _, c in cmds] == ["Miner 1: start at 900 W (ok)", "Miner 1: 2,500 W (ok)", "Miner 1: stop (pause) (ok)"]


def test_compute_day_balances_the_energy() -> None:
    d = farm_day.compute_day(a_day(), FARM, DAY, TZ)
    t = d["totals"]
    assert round(t["solar"], 3) == 2.0
    assert round(t["miners"], 3) == 3.0
    assert round(t["miners_solar"], 3) == 2.0 and round(t["miners_grid"], 3) == 1.0
    assert round(t["import"], 3) == 13.0  # the house: 0.5 kWh/h for 24 h; the miner: 1 kWh
    assert round(t["house"], 3) == 12.0
    assert round(t["house_grid"], 3) == 12.0 and round(t["house_solar"], 3) == 0.0
    assert t["export"] == 0.0
    assert d["solar_share_pct"] == 67 and d["self_use_pct"] == 100
    assert d["light"] == ["10:00", "10:00"]
    m = d["miners"][0]
    assert round(m["hours"], 2) == 1.0 and round(m["avg_th_s"], 1) == 100.0
    assert round(m["hashes"]) == round(100 * 3600 * 1e12)
    assert m["starts"] == ["10:00"]
    assert d["commands"] == {"total": 3, "start": 1, "stop": 1, "limit": 1}
    assert d["grid_topup"]["kwh"] == 1.0 and d["grid_topup"]["from"] == "08:00"


def test_start_burst_flags_a_flip_flop_not_a_day_of_starts() -> None:
    assert farm_day.start_burst(["07:47", "08:04", "08:12", "08:29"]) == ["07:47", "08:04", "08:12", "08:29"]
    assert farm_day.start_burst(["08:48", "10:22", "12:45", "15:12", "15:47"]) == []


def test_text_marks_a_test_and_names_a_flip_flop() -> None:
    d = farm_day.compute_day(a_day(), FARM, DAY, TZ)
    d["miners"][0]["starts"] = ["07:47", "08:04", "08:12"]
    text = farm_day.format_text(d, test=True)
    assert text.startswith(farm_day.TEST_LINE)
    assert "📊 Farm day: Monday 12 October 2026" in text
    assert "⛏️ Miners: 3.0 kWh, 67% from the sun (2.0 solar + 1.0 grid)" in text
    assert "⚠️ Miner 1 started 3× between 07:47 and 08:12" in text
    assert not farm_day.format_text(d).startswith(farm_day.TEST_LINE)
    assert len(farm_day.format_caption(d, test=True)) <= 1024


def test_a_day_without_mining_says_so() -> None:
    history = a_day()
    history["sensor.m1_energy"] = [row("00:00", 100.0)]
    history["sensor.m1_hashrate"] = [row("00:00", 0)]
    d = farm_day.compute_day(history, FARM, DAY, TZ)
    assert farm_day.headline(d) == "No mining today"
    text = farm_day.format_text(d)
    assert "⛏️ Miners: no mining today" in text and "\nMiners\n" not in text


def test_sci() -> None:
    assert farm_day.sci(6.81e18) == "6.8 × 10¹⁸"


def test_the_prototype_and_the_integration_use_one_template() -> None:
    from custom_components.solar_smart_miner.messages import day_report

    assert farm_day.format_text is day_report.format_text
    d = farm_day.compute_day(a_day(), FARM, DAY, TZ)
    assert farm_day.format_text(d) == day_report.format_text(d)
