"""The day's story for the owner: four Telegram messages, each at most once a day.

- morning: the first miner starts mining
- all_max: every miner seen today mines at its top power step, nothing is settling, every hottest
  chip is below the temperature band's top; seen on two polls in a row
- step_down: after all_max, the first time a miner's limit goes down or it stops
- evening: after the morning, no miner mines and it is sunset or the sun is down

It only observes: it reads the snapshot after the decision and never feeds the decision. It
reuses the existing definitions: mining (control.is_mining), settled (no ramp lock running),
restarting after a limit change (the controller's settling record), sunset
(transition.Transition), a miner's steps (decision's ladder), the band (decision's too_warm_c).
A day begins at the first poll on a new date. Energy is added up per poll (pv, miners, import),
and the day survives a restart of HA (the coordinator saves `as_dict`).
"""
from __future__ import annotations

import copy
from datetime import datetime

from .control import is_mining
from .decision import _ladder
from .decision.describe import _w
from .protocols import (
    ACTION_SET_LIMIT,
    ACTION_STOP,
    CoordinatorSnapshot,
    EnergySnapshot,
    MinerSnapshot,
)

MORNING = "morning"
ALL_MAX = "all_max"
STEP_DOWN = "step_down"
EVENING = "evening"
GAP_POLLS = 3  # a gap longer than this many polls (HA down) isn't added to the energy
CONFIRM_POLLS = 2  # all at max, and a stop, must be seen this many polls in a row


def _new_day(date: str | None) -> dict:
    return {
        "date": date,
        "sent": [],
        "first_start_at": None,  # ISO time
        "first_miner": None,
        "first_limit_w": None,
        "forecast_at_start_kwh": None,
        "all_max_at": None,
        "all_max_miners": 0,
        "step_down": None,  # {"at", "miner", "old_w", "new_w", "reason", "forecast_w", "pv_w"}
        "last_stop_at": None,
        "peak_at_top": 0,  # most miners seen at their top step at once
        "miners": 0,  # most miners seen available at once
        "pv_kwh": 0.0,
        "miners_kwh": 0.0,
        "import_kwh": 0.0,
        "mining_minutes": {},  # miner id -> minutes mined
        "last_poll": None,  # ISO time of the last poll, for the energy
    }


def actual_pv_w(energy: EnergySnapshot) -> float | None:
    """What the panels give now: the actual PV sensor, else the solar production reading."""
    return energy.pv_power_w if energy.pv_power_w is not None else energy.solar_production_w


def _top_step(miner: MinerSnapshot, steps: list[float]) -> float:
    return _ladder(miner, steps)[-1]


class DayReport:
    def __init__(self, data: dict | None = None) -> None:
        self.state = {**_new_day(None), **(data or {})}
        self._all_max_polls = 0
        # Per miner, from the polls it was readable in (a miner unavailable for a while keeps them)
        self._limits: dict[str, float | None] = {}  # its limit at the last poll
        self._mining: dict[str, bool] = {}  # counted as mining (a stop not yet confirmed still is)
        self._down_polls: dict[str, int] = {}  # polls in a row not mining (restarts excluded)
        self._reasons: dict[str, str] = {}  # the reason of the plan that lowered or stopped it

    def as_dict(self) -> dict:
        return copy.deepcopy(self.state)  # Store serialises it in a thread

    def update(
        self,
        now: datetime,
        snapshot: CoordinatorSnapshot,
        *,
        settled: bool,
        restarting: set[str],
        steps: list[float],
        too_warm_c: float,
        sun_up: bool | None,
        sunset: bool,
        poll_s: float,
    ) -> list[str]:
        """One poll, after the decision. Returns the events to report now, in order."""
        state = self.state
        today = now.date().isoformat()
        miners = [m for m in snapshot.miners if m.is_available]
        mining = [m for m in miners if is_mining(m)]
        if state["date"] != today:
            self.state = state = _new_day(today)
            self._reasons = {}
            if mining:  # picked up while miners mine (a fresh install, HA down overnight)
                state["sent"].append(MORNING)
        self._add_energy(now, snapshot, mining, max_gap_s=GAP_POLLS * poll_s)
        self._note_reasons(snapshot, miners)
        state["miners"] = max(state["miners"], len(miners))
        at_top = [m for m in mining if m.power_limit_w == _top_step(m, steps)]
        state["peak_at_top"] = max(state["peak_at_top"], len(at_top))

        events: list[str] = []
        sent = state["sent"]
        stamp = now.isoformat(timespec="seconds")
        if MORNING not in sent and mining:
            first = mining[0]
            state.update(
                first_start_at=stamp,
                first_miner=first.name or first.miner_id,
                first_limit_w=first.power_limit_w,
                forecast_at_start_kwh=snapshot.energy.forecast_remaining_kwh,
            )
            events.append(MORNING)

        all_max = (
            MORNING in sent + events
            and miners
            and len(miners) >= state["miners"]  # none of today's miners is missing
            and len(at_top) == len(miners)
            and settled
            and all(m.temperature_c is None or m.temperature_c < too_warm_c for m in miners)
        )
        self._all_max_polls = self._all_max_polls + 1 if all_max else 0
        if ALL_MAX not in sent and self._all_max_polls >= CONFIRM_POLLS:
            state.update(all_max_at=stamp, all_max_miners=len(miners))
            events.append(ALL_MAX)

        down = self._stepped_down(miners, restarting)
        if ALL_MAX in sent and STEP_DOWN not in sent and down is not None:
            miner, old, new = down
            energy = snapshot.energy
            state["step_down"] = {
                "at": stamp, "miner": miner.name or miner.miner_id, "old_w": old, "new_w": new,
                "reason": self._reasons.pop(miner.miner_id, ""),
                "forecast_w": energy.forecast_now_w, "pv_w": actual_pv_w(energy),
            }
            events.append(STEP_DOWN)

        if MORNING in sent + events and EVENING not in sent and not mining and (sunset or sun_up is False):
            state["last_stop_at"] = stamp
            events.append(EVENING)

        sent.extend(events)
        return events

    def _note_reasons(self, snapshot: CoordinatorSnapshot, miners: list[MinerSnapshot]) -> None:
        """The reason of each plan that lowers or stops a miner, for the step-down message."""
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

    def _stepped_down(
        self, miners: list[MinerSnapshot], restarting: set[str]
    ) -> tuple[MinerSnapshot, float | None, float | None] | None:
        """The first miner whose limit went down, or that stopped: not mining for CONFIRM_POLLS
        polls in a row and not restarting after a limit change (a restart reads as a stop for a
        minute or two). Returns (miner, old W, new W or None for a stop)."""
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
        return found

    def _add_energy(
        self, now: datetime, snapshot: CoordinatorSnapshot, mining: list[MinerSnapshot], *, max_gap_s: float
    ) -> None:
        state = self.state
        last = state["last_poll"]
        state["last_poll"] = now.isoformat(timespec="seconds")
        if last is None:
            return
        seconds = (now - datetime.fromisoformat(last)).total_seconds()
        if not 0 < seconds <= max_gap_s:
            return
        hours = seconds / 3600
        energy = snapshot.energy
        state["pv_kwh"] += (actual_pv_w(energy) or 0.0) * hours / 1000
        state["miners_kwh"] += (energy.miner_consumption_sum_w or 0.0) * hours / 1000
        if mining and energy.grid_net_w is not None and energy.grid_net_w < 0:
            state["import_kwh"] += -energy.grid_net_w * hours / 1000
        for m in mining:
            state["mining_minutes"][m.miner_id] = state["mining_minutes"].get(m.miner_id, 0.0) + seconds / 60


# --- the messages ---------------------------------------------------------------


def _time(iso: str | None) -> str:
    return datetime.fromisoformat(iso).strftime("%H:%M") if iso else "?"


def _kw(watts: float) -> str:
    return f"{watts / 1000:.1f} kW"


def _duration(start: str, end: str) -> str:
    minutes = round((datetime.fromisoformat(end) - datetime.fromisoformat(start)).total_seconds() / 60)
    hours, minutes = divmod(minutes, 60)
    return f"{hours} h {minutes} min" if hours else f"{minutes} min"


def describe(event: str, state: dict, snapshot: CoordinatorSnapshot) -> str:
    """The Telegram text for one event. A reading that is missing leaves its part out."""
    energy = snapshot.energy
    if event == MORNING:
        lines = [
            f"☀️ Production is up: {state['first_miner']} started at {_w(state['first_limit_w'])} "
            f"({_time(state['first_start_at'])})."
        ]
        forecast = []
        if energy.forecast_remaining_kwh is not None:
            forecast.append(f"{energy.forecast_remaining_kwh:.1f} kWh for the rest of today")
        if energy.forecast_next_hour_w is not None:
            forecast.append(f"{_kw(energy.forecast_next_hour_w)} next hour")
        if forecast:
            lines.append("Forecast: " + ", ".join(forecast) + ".")
        return "\n".join(lines)
    if event == ALL_MAX:
        pv = actual_pv_w(energy)
        first = f"⚡ All {state['all_max_miners']} miners run at their top step, settled ({_time(state['all_max_at'])}"
        first += f", PV {_kw(pv)})." if pv is not None else ")."
        lines = [first]
        if state["first_start_at"]:
            lines.append(
                f"It took {_duration(state['first_start_at'], state['all_max_at'])} from the first start "
                f"({_time(state['first_start_at'])})."
            )
        return "\n".join(lines)
    if event == STEP_DOWN:
        down = state["step_down"]
        change = "stopped" if down["new_w"] is None else f"{_w(down['old_w'])} → {_w(down['new_w'])}"
        lines = [f"🌥 Stepping down: {down['miner']} {change}" + (f" ({down['reason']})." if down["reason"] else ".")]
        if down["forecast_w"] is not None and down["pv_w"] is not None:
            share = f" ({down['pv_w'] / down['forecast_w']:.0%})" if down["forecast_w"] > 0 else ""
            lines.append(f"Forecast {_kw(down['forecast_w'])}, panels give {_kw(down['pv_w'])}{share}.")
        elif down["forecast_w"] is not None:
            lines.append(f"Forecast {_kw(down['forecast_w'])}.")
        return "\n".join(lines)
    # EVENING
    miner_hours = sum(state["mining_minutes"].values()) / 60
    lines = [f"🌙 Day done: the last miner stopped at {_time(state['last_stop_at'])}."]
    today = (
        f"Today: PV {state['pv_kwh']:.1f} kWh, miners {state['miners_kwh']:.1f} kWh "
        f"({state['import_kwh']:.1f} kWh from the grid), {miner_hours:.1f} miner-hours"
    )
    if state["first_start_at"]:
        today += f", mining {_time(state['first_start_at'])}–{_time(state['last_stop_at'])}"
    lines.append(today + ".")
    if state["forecast_at_start_kwh"] is not None:
        lines.append(f"Forecast this morning: {state['forecast_at_start_kwh']:.1f} kWh.")
    if ALL_MAX not in state["sent"] and state["miners"]:
        lines.append(f"Peak: {state['peak_at_top']} of {state['miners']} miners at their top step.")
    return "\n".join(lines)
