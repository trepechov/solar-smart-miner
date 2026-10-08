#!/usr/bin/env python3
"""Replay the farm's decisions through the current code, and look for rules that collide.

Run with the project's virtualenv from the repo root (it imports the integration):

  .venv/bin/python scripts/replay.py decisions <decisions.jsonl>
      Every logged decision run again; prints the cycles whose plans or summary differ.
      Use before every release that may change behaviour.

  .venv/bin/python scripts/replay.py collisions <actions.jsonl> [<actions.jsonl> ...]
  .venv/bin/python scripts/replay.py collisions --recorder <dir or day.json> [...]
      Where rules met each other: changes reversed within 15 minutes, a start on a miner still
      restarting, an up on one miner next to a down on another, two changes within a minute,
      a step down that waited the morning delay late in the morning, hold reasons that flip.
      --recorder reads Home Assistant recorder history downloads (/api/history/period, one
      JSON file per day) instead of the integration's own logs.

  .venv/bin/python scripts/replay.py export --recorder <day.json> --at 16:50 17:10 ... \\
        [--solar-noon 09:56] [--tz Europe/Sofia]
      Rebuild the decision inputs at those local times from a recorder download, run them
      through the current code and print them as decisions.jsonl lines (the replay fixtures in
      tests/replay/ were made this way). Readings the recorder lacks are approximated: the
      ramp lock from the limit and switch history, the import-high time from the meter, the
      sun's direction from --solar-noon (UTC).

This is a development tool; nothing in the integration imports it.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from custom_components.solar_smart_miner.const import (  # noqa: E402
    DEFAULT_IMPORT_MAX_W,
    DEFAULT_RAMP_LOCK_MINUTES,
    RAMP_DONE_FRACTION,
    RAMP_MIN_MINUTES,
)
from custom_components.solar_smart_miner.decision import build_decision  # noqa: E402
from custom_components.solar_smart_miner.decision_log import load_record, plans_fingerprint  # noqa: E402
from custom_components.solar_smart_miner.protocols import MinerPlan  # noqa: E402

REVERSAL_MIN = 15  # a change undone within this many minutes is a collision
FLIP_WINDOW_MIN = 10
FLIP_COUNT = 6  # hold reasons changing more often than this within the window


# --- replaying decisions.jsonl ---------------------------------------------------------


def replay(path: Path) -> int:
    """Run every logged decision again; the number of cycles that came out differently."""
    differ = 0
    total = 0
    for line in path.read_text(encoding="utf-8").splitlines():
        try:
            record = json.loads(line)
        except ValueError:
            continue
        total += 1
        snapshot, inputs = load_record(record)
        now = build_decision(snapshot, **inputs)
        was = ";".join(
            f"{mid}:{MinerPlan(**plan).fingerprint}" for mid, plan in sorted(record["plans"].items())
        )
        if plans_fingerprint(now) != was or now.summary != record["summary"]:
            differ += 1
            print(f"{record['ts']}\n  was: {record['summary']}\n  now: {now.summary}")
    print(f"{total} decisions replayed, {differ} differ")
    return differ


# --- commands, from either source ----------------------------------------------------------


@dataclass
class Command:
    t: datetime
    miner: str
    action: str  # set_limit / start / stop
    limit_w: float | None
    before_w: float | None  # the limit it had, for set_limit

    @property
    def kind(self) -> str:
        if self.action in ("start", "stop"):
            return self.action
        if self.before_w is None or self.limit_w is None:
            return "set"
        return "up" if self.limit_w > self.before_w else "down"

    def __str__(self) -> str:
        if self.action == "stop":
            what = "stop"
        elif self.action == "start":
            what = f"start at {self.limit_w or 0:,.0f} W"
        else:
            what = f"{self.before_w or 0:,.0f} → {self.limit_w or 0:,.0f} W"
        return f"{self.t:%H:%M:%S} {self.miner} {what}"


_OPPOSITE = {"up": "down", "down": "up", "start": "stop", "stop": "start"}


def commands_from_actions(paths: list[Path]) -> list[Command]:
    """The first line of every command that was sent (actions.jsonl)."""
    out = []
    for path in paths:
        for line in path.read_text(encoding="utf-8").splitlines():
            try:
                e = json.loads(line)
            except ValueError:
                continue
            if not e.get("calls") or "before" not in e:
                continue  # refused, or an outcome line
            plan = e.get("plan") or {}
            out.append(
                Command(
                    datetime.fromisoformat(e["ts"]), e.get("miner") or "?", plan.get("action") or "?",
                    plan.get("limit_w"), (e.get("before") or {}).get("limit_w"),
                )
            )
    return sorted(out, key=lambda c: c.t)


# --- the recorder -------------------------------------------------------------------------


class Recorder:
    """One or more days of HA recorder history: entity id -> [(time, state)], in time order."""

    def __init__(self, paths: list[Path]) -> None:
        self.series: dict[str, list[tuple[datetime, str]]] = defaultdict(list)
        self.attributes: dict[str, dict] = {}
        files = [p for path in paths for p in (sorted(path.glob("*.json")) if path.is_dir() else [path])]
        for file in files:
            for series in json.loads(file.read_text(encoding="utf-8")):
                for s in series:
                    eid = s.get("entity_id") or series[0]["entity_id"]
                    self.series[eid].append((datetime.fromisoformat(s["last_changed"]), s["state"]))
                    if s.get("attributes"):
                        self.attributes[eid] = s["attributes"]
        for eid in self.series:
            self.series[eid].sort(key=lambda p: p[0])

    def find(self, pattern: str) -> list[str]:
        return sorted(e for e in self.series if re.fullmatch(pattern, e))

    def state(self, eid: str, t: datetime) -> str | None:
        value = None
        for when, state in self.series.get(eid, ()):
            if when > t:
                break
            value = state
        return value

    def number(self, eid: str, t: datetime) -> float | None:
        try:
            return float(self.state(eid, t))
        except (TypeError, ValueError):
            return None

    def last_change(self, eid: str, t: datetime, *, numeric: bool = False) -> datetime | None:
        """When the state last became what it is at t (repeats of the same value don't count)."""
        changed, prev = None, None
        for when, state in self.series.get(eid, ()):
            if when > t:
                break
            if state in ("unknown", "unavailable"):
                continue
            key = _as_float(state) if numeric else state
            if prev is not None and key != prev:
                changed = when
            prev = key
        return changed

    def value_before_change(self, eid: str, t: datetime) -> float | None:
        """The number before its last change up to t (the limit a command started from)."""
        values: list[float] = []
        for when, state in self.series.get(eid, ()):
            if when > t:
                break
            value = _as_float(state)
            if isinstance(value, float) and (not values or values[-1] != value):
                values.append(value)
        return values[-2] if len(values) > 1 else (values[-1] if values else None)

    def entity(self, suffix: str) -> str | None:
        found = self.find(rf"sensor\..*{suffix}")
        return found[0] if found else None


def _as_float(state: str) -> float | str:
    try:
        return float(state)
    except ValueError:
        return state


_PLAN = re.compile(r"^(?P<miner>[^:]+): (?P<plan>.+) \((?P<result>[a-z]+)\)$")


def commands_from_recorder(rec: Recorder) -> list[Command]:
    """The commands the "Last action" sensor showed (the first state of each)."""
    out: list[Command] = []
    seen: set[tuple[str, str]] = set()
    for eid in rec.find(r"sensor\..*_last_action"):
        for t, state in rec.series[eid]:
            m = _PLAN.match(state)
            if not m or m["result"] not in ("pending", "ok", "failed"):
                continue
            miner, plan = m["miner"], m["plan"]
            key = (miner, plan)
            if m["result"] != "pending" and key in seen:
                seen.discard(key)
                continue  # the outcome of a command already counted
            if m["result"] == "pending":
                seen.add(key)
            limit_eid = f"number.{miner.lower()}_power_limit"
            before = rec.value_before_change(limit_eid, t + timedelta(seconds=10))
            if plan.startswith("stop"):
                out.append(Command(t, miner, "stop", None, before))
            elif plan.startswith("start at"):
                out.append(Command(t, miner, "start", _watts(plan), before))
            elif plan.endswith(" W"):
                out.append(Command(t, miner, "set_limit", _watts(plan), before))
    return sorted(out, key=lambda c: c.t)


def _watts(text: str) -> float | None:
    m = re.search(r"([\d,]+) W", text)
    return float(m[1].replace(",", "")) if m else None


def summaries_from_recorder(rec: Recorder) -> list[tuple[datetime, str]]:
    return [p for eid in rec.find(r"sensor\..*_decision_log") for p in rec.series[eid]]


# --- collisions ---------------------------------------------------------------------------


def collisions(
    commands: list[Command], summaries: list[tuple[datetime, str]], tz: ZoneInfo
) -> list[str]:
    found: list[str] = []
    window = timedelta(minutes=REVERSAL_MIN)
    ramp = timedelta(minutes=DEFAULT_RAMP_LOCK_MINUTES)
    for i, c in enumerate(commands):
        later = [d for d in commands[i + 1 :] if d.t - c.t <= window]
        for d in later:
            if d.miner == c.miner and _OPPOSITE.get(c.kind) == d.kind:
                found.append(f"reversed within {REVERSAL_MIN} min: {_local(c, tz)}  then  {_local(d, tz)}")
                break
        for d in later:
            if d.miner != c.miner and {c.kind, d.kind} == {"up", "down"}:
                found.append(f"up and down on two miners: {_local(c, tz)}  /  {_local(d, tz)}")
                break
        nxt = next((d for d in commands[i + 1 :]), None)
        if nxt is not None and nxt.t - c.t < timedelta(minutes=RAMP_MIN_MINUTES):
            found.append(f"two changes within {RAMP_MIN_MINUTES} min: {_local(c, tz)}  /  {_local(nxt, tz)}")
        if c.kind == "start":
            prev = next((d for d in reversed(commands[:i]) if d.miner == c.miner), None)
            if prev is not None and prev.kind != "stop" and c.t - prev.t < ramp:
                found.append(f"start while restarting: {_local(prev, tz)}  then  {_local(c, tz)}")
    found += _late_morning_waits(summaries, tz)
    found += _flips(summaries, tz)
    return found


def _local(c: Command, tz: ZoneInfo) -> str:
    return str(Command(c.t.astimezone(tz), c.miner, c.action, c.limit_w, c.before_w))


def _category(summary: str) -> str:
    return re.sub(r"[\d,.]+ ?W|\d+", "#", summary.split(" → ")[0])


def _late_morning_waits(summaries, tz) -> list[str]:
    """A shortfall waited longer than the normal delay after 10:00: the morning delay at work."""
    out, start = [], None
    for t, s in summaries:
        waiting = "above the maximum, waiting" in s
        if waiting and start is None:
            start = t
        elif not waiting and start is not None:
            local = start.astimezone(tz)
            if local.hour >= 10 and t - start > timedelta(minutes=6) and local.hour < 13:
                out.append(f"waited {(t - start).seconds // 60} min on a shortfall at {local:%H:%M} (morning delay?)")
            start = None
    return out


def _flips(summaries, tz) -> list[str]:
    out = []
    window = timedelta(minutes=FLIP_WINDOW_MIN)
    i = 0
    while i < len(summaries):
        t0 = summaries[i][0]
        cats = [_category(s) for t, s in summaries[i:] if t - t0 <= window]
        flips = sum(1 for a, b in zip(cats, cats[1:]) if a != b)
        if flips > FLIP_COUNT:
            out.append(f"{flips} changes of reason within {FLIP_WINDOW_MIN} min from {t0.astimezone(tz):%H:%M}")
            i += len(cats)
        else:
            i += 1
    return out


# --- recorder -> decision inputs (for fixtures) -------------------------------------------


def inputs_at(rec: Recorder, t: datetime, solar_noon: str | None) -> dict:
    """A decisions.jsonl record rebuilt from the recorder at time t, decided by the current code."""
    from custom_components.solar_smart_miner.decision_log import build_record
    from custom_components.solar_smart_miner.protocols import (
        CoordinatorSnapshot,
        EnergySnapshot,
        MinerSnapshot,
    )

    grid = rec.number("sensor.power_meter_active_power", t)
    miners = []
    ramp = timedelta(minutes=DEFAULT_RAMP_LOCK_MINUTES)
    ages: list[float] = []
    done: list[str] = []
    for n, eid in enumerate(rec.find(r"sensor\.(\w+)_miner_consumption"), start=1):
        name = eid.split(".")[1].removesuffix("_miner_consumption")
        label = name[:1].upper() + name[1:]
        power = rec.number(eid, t)
        limit = rec.number(f"number.{name}_power_limit", t)
        switch = rec.state(f"switch.{name}_active", t)
        hashrate = rec.number(f"sensor.{name}_hashrate", t)
        limit_changed = rec.last_change(f"number.{name}_power_limit", t, numeric=True)
        switch_changed = rec.last_change(f"switch.{name}_active", t)
        stopped = switch == "off"
        restarting = (
            stopped and limit_changed is not None and t - limit_changed < ramp
            and rec.state(f"switch.{name}_active", limit_changed) == "on"
        )
        if restarting:
            stopped = False  # a limit change restarts the miner; the switch reads off meanwhile
        miner = MinerSnapshot(
            miner_id=f"miner{n}", ip=f"192.0.2.{n}", name=label, power_w=power, power_limit_w=limit,
            min_power_w=500.0, max_power_w=3500.0,
            temperature_c=rec.number(f"sensor.{name}_temperature", t), is_available=power is not None,
            power_limit_entity_id=f"number.miner{n}_power_limit", hashrate_th=hashrate,
            switch_entity_id=f"switch.miner{n}_active", is_stopped=stopped,
            minutes_since_limit_change=None if limit_changed is None else (t - limit_changed).total_seconds() / 60,
        )
        miners.append(miner)
        change = max((c for c in (limit_changed, switch_changed) if c is not None), default=None)
        if change is not None and t - change < ramp:
            age = (t - change).total_seconds() / 60
            settled = (
                age >= RAMP_MIN_MINUTES
                and (
                    stopped
                    or (power is not None and limit and abs(power - limit) <= RAMP_DONE_FRACTION * limit)
                )
            )
            (done.append(label) if settled else ages.append(age))
    miner_sum = sum(m.power_w for m in miners if m.power_w is not None) or None
    energy = EnergySnapshot(
        solar_production_w=None, grid_net_w=grid, solar_fault=grid is None,
        miner_consumption_sum_w=miner_sum,
        available_for_miners_w=None if grid is None else grid + (miner_sum or 0.0),
        pv_power_w=rec.number("sensor.active_power_total", t),
        forecast_now_w=rec.number("sensor.solar_forecast_power_now", t),
    )
    since_change = min(ages, default=None)
    sun = rec.state("sun.sun", t)
    rising = None
    if solar_noon:
        hh, mm = (int(x) for x in solar_noon.split(":"))
        rising = t < t.astimezone(timezone.utc).replace(hour=hh, minute=mm, second=0, microsecond=0)
    inputs = {
        "profile": "solar_max", "temp_target": 60.0, "temp_tolerance": 10.0, "battery_floor": 20.0,
        "power_steps": [900, 1100, 1300, 1500, 1700, 1900, 2100, 2300, 2500],
        "tuning_settle_minutes": 5.0, "import_min_w": 200.0, "import_max_w": float(DEFAULT_IMPORT_MAX_W),
        "minutes_since_change": since_change, "ramp_lock_minutes": float(DEFAULT_RAMP_LOCK_MINUTES),
        "ramp_done": done,
        "minutes_import_high": _import_high(rec, t, since_change),
        "step_down_delay_minutes": 5.0, "morning_step_down_delay_minutes": 30.0,
        "sun_up": None if sun is None else sun == "above_horizon", "sun_rising": rising,
    }
    snapshot = CoordinatorSnapshot(energy=energy, miners=miners)
    decision = build_decision(snapshot, **inputs)
    record = build_record(snapshot, inputs, decision)
    record["ts"] = t.isoformat(timespec="seconds")
    record["farm_summary"] = next(
        (s for when, s in reversed(summaries_from_recorder(rec)) if when <= t), None
    )
    return record


def _import_high(rec: Recorder, t: datetime, since_change: float | None) -> float | None:
    since = None
    for when, state in rec.series.get("sensor.power_meter_active_power", ()):
        if when > t:
            break
        value = _as_float(state)
        if isinstance(value, float) and -value > DEFAULT_IMPORT_MAX_W:
            since = since or when
        elif isinstance(value, float):
            since = None
    if since is None:
        return None
    lasted = (t - since).total_seconds() / 60
    return lasted if since_change is None else min(lasted, since_change)


# --- command line ---------------------------------------------------------------------------


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="command", required=True)
    p = sub.add_parser("decisions")
    p.add_argument("log", type=Path)
    p = sub.add_parser("collisions")
    p.add_argument("logs", type=Path, nargs="*")
    p.add_argument("--recorder", type=Path, nargs="*", default=[])
    p.add_argument("--tz", default="Europe/Sofia")
    p = sub.add_parser("export")
    p.add_argument("--recorder", type=Path, required=True)
    p.add_argument("--at", nargs="+", required=True, help="local times, HH:MM or HH:MM:SS")
    p.add_argument("--solar-noon", help="UTC, HH:MM")
    p.add_argument("--tz", default="Europe/Sofia")
    args = parser.parse_args(argv)

    if args.command == "decisions":
        return 1 if replay(args.log) else 0
    tz = ZoneInfo(args.tz)
    if args.command == "collisions":
        if args.recorder:
            rec = Recorder(args.recorder)
            commands, summaries = commands_from_recorder(rec), summaries_from_recorder(rec)
        else:
            commands, summaries = commands_from_actions(args.logs), []
        found = collisions(commands, summaries, tz)
        print("\n".join(found) or "no collisions")
        print(f"{len(commands)} commands, {len(found)} collisions")
        return 0
    rec = Recorder([args.recorder])
    day = min(t for series in rec.series.values() for t, _ in series).astimezone(tz).date()
    day = day + timedelta(days=1) if min(
        t for series in rec.series.values() for t, _ in series
    ).astimezone(tz).hour >= 12 else day
    for at in args.at:
        parts = [int(x) for x in at.split(":")] + [0]
        local = datetime(day.year, day.month, day.day, parts[0], parts[1], parts[2], tzinfo=tz)
        print(json.dumps(inputs_at(rec, local, args.solar_noon), ensure_ascii=False, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    sys.exit(main())
