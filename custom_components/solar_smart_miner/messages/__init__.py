"""The day messages: a message policy per farm profile (plan 2026-10-15-002).

A message policy is an ordered list of message rules, like the decision's groups. Each poll,
after the decision, DayMessages works out what it needs from this poll and the ones before
(which miners mine, which are at their top step, a step down, the day's first usable
production), then runs the profile's rules in order. Each rule fires at most once a day; one
that fires gives the text to send. A policy changes by release, never by a setting.

The sun-driven policy (Solar-follow), in order:

1. morning (morning.py): the day's first usable production, or a day picked up with it.
2. peak (peak.py): every miner at its top step, settled.
3. going-down (going_down.py): a step down that matches the sun's decline; other step downs
   are clouds, counted.
4. evening (evening.py): the sun below the horizon.

The rules only observe: they read the snapshot and the decision's own definitions (mining,
settled, the ladder, the band, sun.sun) and never feed a decision. Each has one test group and
one knowledge-base entry (knowledge/messages.yaml). The day survives a restart of HA (the
coordinator saves `as_dict`).
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
from . import evening, going_down, morning, peak
from .context import CONFIRM_POLLS, DayContext, actual_pv_w

POLICIES = {
    "sun_driven": (morning, peak, going_down, evening),
}

COMMAND_KINDS = {ACTION_START: "start", ACTION_STOP: "stop", ACTION_SET_LIMIT: "limit"}
OLD_NAMES = {"all_max": peak.NAME, "step_down": going_down.NAME}  # 0.8.4 to 0.8.6


def policy_for(profile: str) -> tuple:
    """The profile's message rules, in order; none for a profile without a policy."""
    return POLICIES.get(MESSAGE_POLICIES.get(profile, ""), ())


def new_day(date: str | None) -> dict:
    return {
        "date": date,
        "sent": [],  # names of the rules that fired today
        "first_production_at": None,  # ISO time of the day's first usable production
        "picked_up": False,  # first usable production was already there when HA started
        "forecast_at_start_kwh": None,  # the forecast for the day, the last one before production
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
    }


def _load(data: dict | None) -> dict:
    """A saved day, the names of 0.8.4 to 0.8.6 read as today's; unknown keys dropped."""
    state = new_day(None)
    for key, value in (data or {}).items():
        if key in state:
            state[key] = value
    state["sent"] = [OLD_NAMES.get(name, name) for name in state["sent"]]
    return state


def _top_step(miner: MinerSnapshot, steps: list[float]) -> float:
    return _ladder(miner, steps)[-1]


class DayMessages:
    def __init__(self, data: dict | None = None) -> None:
        self.state = _load(data)
        self._memory: dict = {}  # per rule, for today
        self._usable_polls = 0  # polls in a row with the PV at the lowest step
        # Per miner, from the polls it was readable in (a miner unavailable for a while keeps them)
        self._limits: dict[str, float | None] = {}  # its limit at the last poll
        self._mining: dict[str, bool] = {}  # counted as mining (a stop not yet confirmed still is)
        self._down_polls: dict[str, int] = {}  # polls in a row not mining (restarts excluded)
        self._reasons: dict[str, str] = {}  # the reason of the plan that lowered or stopped it

    def as_dict(self) -> dict:
        return copy.deepcopy(self.state)  # Store serialises it in a thread

    def note_command(self, at: str, miner: str, action: str) -> None:
        """A command of the integration that finished ok (at: when it was sent)."""
        kind = COMMAND_KINDS.get(action)
        if kind is not None:
            self.state["commands"].append({"at": at, "miner": miner, "kind": kind})

    def update(self, ctx: DayContext, policy: tuple) -> list[tuple[str, str]]:
        """One poll, after the decision. Returns (rule name, text) for each rule that fired."""
        self._begin_day(ctx.now)
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
        commands = [c for c in self.state["commands"] if c["at"][:10] == today]  # sent just before
        self.state = new_day(today)
        self.state["commands"] = commands
        self._memory, self._reasons, self._usable_polls = {}, {}, 0

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
        self._note_first_production(ctx)
        self._note_reasons(snapshot, ctx.miners)
        ctx.down = self._stepped_down(ctx.miners, ctx.restarting)

    def _note_first_production(self, ctx: DayContext) -> None:
        """The day's first usable production (one definition, for the morning and the evening):
        the integration's own first start, or the PV at the lowest power step on two polls in a
        row, while the sun is up. A day picked up by HA with a miner mining or the PV at the
        lowest step has it at once."""
        state = self.state
        pv = actual_pv_w(ctx.energy)
        usable = pv is not None and ctx.steps and pv >= min(ctx.steps)
        self._usable_polls = self._usable_polls + 1 if usable else 0
        if state["first_production_at"] is not None:
            return
        if ctx.sun_up is False:
            return
        starts = [c["at"] for c in state["commands"] if c["kind"] == "start"]
        if ctx.picking_up and (ctx.mining or usable):
            state.update(first_production_at=ctx.stamp, picked_up=True)
        elif starts:
            state["first_production_at"] = min(starts)
        elif self._usable_polls >= CONFIRM_POLLS:
            state["first_production_at"] = ctx.stamp
        else:
            state["forecast_at_start_kwh"] = ctx.energy.forecast_remaining_kwh
            return
        if state["picked_up"]:
            state["forecast_at_start_kwh"] = None  # the morning's forecast wasn't seen

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
