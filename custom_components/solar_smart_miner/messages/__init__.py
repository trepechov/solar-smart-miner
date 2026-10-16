"""The day messages: a message policy per farm profile (plan 2026-10-15-002).

A message policy is an ordered list of message rules, like the decision's groups. Each poll,
after the decision, DayMessages works out what it needs from this poll and the ones before
(which miners mine, which are at their top step, a step down, the integration's first
start), then runs the profile's rules in order. Each rule fires at most once a day; one
that fires gives the text to send. A policy changes by release, never by a setting.

The sun-driven policy (Solar-follow), in order:

1. manual-reminder (manual_reminder.py): in Manual mode, the first start the decision proposes
   while the sun is up; nothing starts until the owner applies it.
2. morning (morning.py): the integration's first start, or a day picked up with miners mining.
3. peak (peak.py): every miner at its top step, settled.
4. going-down (going_down.py): a step down that matches the sun's decline; other step downs
   are clouds, counted.
5. evening (evening.py): the sun below the horizon.
6. day-report (day_report.py): the calendar day that ended, after midnight.

The rules only observe: they read the snapshot and the decision's own definitions (mining,
settled, the ladder, the band, sun.sun) and never feed a decision. Each has one test group and
one knowledge-base entry (knowledge/messages.yaml). Each poll also adds to the day's totals
(totals.py). The day survives a restart of HA (the coordinator saves `as_dict`), and the last
KEPT_DAYS finished days stay for a report on request.
"""
from __future__ import annotations

import copy
from datetime import datetime

from ..const import MESSAGE_POLICIES
from ..control import is_mining
from ..decision import _ladder
from ..protocols import (
    ACTION_SET_LIMIT,
    ACTION_START,
    ACTION_STOP,
    CoordinatorSnapshot,
    MinerSnapshot,
)
from . import day_report, evening, going_down, manual_reminder, morning, peak, totals
from .context import CONFIRM_POLLS, DayContext

POLICIES = {
    "sun_driven": (manual_reminder, morning, peak, going_down, evening, day_report),
}

COMMAND_KINDS = {ACTION_START: "start", ACTION_STOP: "stop", ACTION_SET_LIMIT: "limit"}
OLD_NAMES = {"all_max": peak.NAME, "step_down": going_down.NAME}  # 0.8.4 to 0.8.6
OLD_KEYS = {"first_production_at": "first_start_at"}  # 0.8.7 and 0.8.8


def policy_for(profile: str) -> tuple:
    """The profile's message rules, in order; none for a profile without a policy."""
    return POLICIES.get(MESSAGE_POLICIES.get(profile, ""), ())


def new_day(date: str | None) -> dict:
    return {
        "date": date,
        "sent": [],  # names of the rules that fired today
        "first_start_at": None,  # ISO time the integration's first start was sent (or the pickup)
        "picked_up": False,  # miners were already mining when HA started
        "forecast_at_start_kwh": None,  # the forecast the morning message showed (rest of the day)
        "solar_at_start_kwh": None,  # the day's solar so far when the morning was sent
        "peak_at": None,
        "peak_miners": 0,
        "peak_at_top": 0,  # most miners seen at their top step at once
        "miners": 0,  # most miners seen available at once
        "going_down": None,  # {"at", "miner", "old_w", "new_w"}
        "cloud_step_downs": 0,
        "sun_seen_up": False,
        "last_mining_at": None,  # ISO time of the last poll a miner mined
        "evening_at": None,
        "commands": [],  # {"at", "miner", "kind"}: the integration's commands that finished ok
        "totals": totals.new_totals(),
        "kept_days": [],  # finished days, newest last: {"date", "report", "reported"}
    }


def _load(data: dict | None) -> dict:
    """A saved day, the names of 0.8.4 to 0.8.8 read as today's; unknown keys dropped."""
    state = new_day(None)
    for key, value in (data or {}).items():
        key = OLD_KEYS.get(key, key)
        if key in state:
            state[key] = value
    state["sent"] = [OLD_NAMES.get(name, name) for name in state["sent"]]
    fresh = totals.new_totals()  # 0.8.8 and 0.8.9 had no missed spans
    state["totals"] = {**fresh, **{k: v for k, v in state["totals"].items() if k in fresh}}
    return state


def _top_step(miner: MinerSnapshot, steps: list[float]) -> float:
    return _ladder(miner, steps)[-1]


class DayMessages:
    def __init__(self, data: dict | None = None) -> None:
        self.state = _load(data)
        self._memory: dict = {}  # per rule, for today
        # Per miner, from the polls it was readable in (a miner unavailable for a while keeps them)
        self._limits: dict[str, float | None] = {}  # its limit at the last poll
        self._mining: dict[str, bool] = {}  # counted as mining (a stop not yet confirmed still is)
        self._down_polls: dict[str, int] = {}  # polls in a row not mining (restarts excluded)
        self._reasons: dict[str, str] = {}  # the reason of the plan that lowered or stopped it

    def report(self, date: str | None = None) -> dict | None:
        """The day report's figures: a kept day, or today so far (None: not kept)."""
        if date is None or date == self.state["date"]:
            return day_report.build_report(self.state) if self.state["date"] else None
        return next((k["report"] for k in self.state["kept_days"] if k["date"] == date), None)

    def as_dict(self) -> dict:
        return copy.deepcopy(self.state)  # Store serialises it in a thread

    def note_command(self, at: str, miner: str, action: str, limit_w: float | None = None) -> None:
        """A command of the integration that finished ok (at: when it was sent)."""
        kind = COMMAND_KINDS.get(action)
        if kind is not None:
            self.state["commands"].append({"at": at, "miner": miner, "kind": kind, "limit_w": limit_w})

    def update(self, ctx: DayContext, policy: tuple) -> list[tuple[str, str]]:
        """One poll, after the decision. Returns (rule name, text) for each rule that fired."""
        self._begin_day(ctx.now)
        totals.add_poll(self.state["totals"], ctx.now, ctx.energy, ctx.snapshot.miners)
        self._observe(ctx)
        ctx.memory = self._memory
        state = self.state
        events = []
        for rule in policy:
            if rule.NAME in state["sent"]:
                continue
            text = rule.check(ctx, state)
            if text is not None:
                state["sent"].append(rule.NAME)
                events.append((rule.NAME, text))
        return events

    def _begin_day(self, now: datetime) -> None:
        today = now.date().isoformat()
        if self.state["date"] == today:
            return
        old = self.state
        kept = old["kept_days"]
        if old["date"] is not None and old["totals"]["last_poll"] is not None:
            kept = [*kept, day_report.finish_day(old)][-day_report.KEPT_DAYS:]
        commands = [c for c in old["commands"] if c["at"][:10] == today]  # sent just before
        self.state = new_day(today)
        self.state.update(commands=commands, kept_days=kept)
        self._memory, self._reasons = {}, {}

    def _observe(self, ctx: DayContext) -> None:
        state = self.state
        snapshot = ctx.snapshot
        ctx.miners = [m for m in snapshot.miners if m.is_available]
        ctx.mining = [m for m in ctx.miners if is_mining(m)]
        ctx.at_top = [m for m in ctx.mining if m.power_limit_w == _top_step(m, ctx.steps)]
        state["miners"] = max(state["miners"], len(ctx.miners))
        state["peak_at_top"] = max(state["peak_at_top"], len(ctx.at_top))
        if ctx.sun_up:
            state["sun_seen_up"] = True
        if ctx.mining:
            state["last_mining_at"] = ctx.stamp
        self._note_first_start(ctx)
        self._note_reasons(snapshot, ctx.miners)
        ctx.down = self._stepped_down(ctx.miners, ctx.restarting)

    def _note_first_start(self, ctx: DayContext) -> None:
        """The day's first start (one definition, for the morning, the peak, going down and the
        evening): the first start command the integration sent today that finished ok, while
        the sun is up. A miner started by hand opens nothing. A day picked up by HA with a
        miner mining has it at once."""
        state = self.state
        if state["first_start_at"] is not None or ctx.sun_up is False:
            return
        starts = [c["at"] for c in state["commands"] if c["kind"] == "start"]
        if ctx.picking_up and ctx.mining:
            state.update(first_start_at=ctx.stamp, picked_up=True)
        elif starts:
            state["first_start_at"] = min(starts)

    def _note_reasons(self, snapshot: CoordinatorSnapshot, miners: list[MinerSnapshot]) -> None:
        """The reason of each plan that lowers or stops a miner, for the going-down message."""
        if snapshot.decision is None:
            return
        limits = {m.miner_id: m.power_limit_w for m in miners}
        for miner_id, plan in snapshot.decision.plans.items():
            current = limits.get(miner_id)
            lowers = (
                plan.action == ACTION_SET_LIMIT
                and plan.limit_w is not None and current is not None and plan.limit_w < current
            )
            if lowers or plan.action == ACTION_STOP:
                self._reasons[miner_id] = plan.reason

    def _stepped_down(self, miners: list[MinerSnapshot], restarting: set[str]) -> tuple | None:
        """The first miner whose limit went down, or that stopped: not mining for CONFIRM_POLLS
        polls in a row and not restarting after a limit change (a restart reads as a stop for a
        minute or two). One per poll. Returns (miner, old W, new W or None for a stop, reason)."""
        found = None
        for m in miners:
            old = self._limits.get(m.miner_id)
            new = m.power_limit_w
            was_mining = self._mining.get(m.miner_id, False)
            if is_mining(m) or m.miner_id in restarting:
                self._down_polls[m.miner_id] = 0
            elif was_mining:
                self._down_polls[m.miner_id] = self._down_polls.get(m.miner_id, 0) + 1
            stopped = self._down_polls.get(m.miner_id, 0) >= CONFIRM_POLLS
            if found is None and old is not None and new is not None and new < old:
                found = (m, old, new)
            elif found is None and was_mining and stopped:
                found = (m, new, None)
            self._limits[m.miner_id] = new
            self._mining[m.miner_id] = is_mining(m) or (was_mining and not stopped)
        if found is None:
            return None
        return (*found, self._reasons.pop(found[0].miner_id, ""))
