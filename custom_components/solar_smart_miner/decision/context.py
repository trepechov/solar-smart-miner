"""What one decision carries from group to group: the readings, the settings, the trace and the
plans decided so far, and the ways to write a plan (hold, stop, move to a step)."""
from __future__ import annotations

from dataclasses import dataclass, field

from ..protocols import (
    ACTION_HOLD,
    ACTION_SET_LIMIT,
    ACTION_START,
    ACTION_STOP,
    CoordinatorSnapshot,
    Decision,
    MinerPlan,
    MinerSnapshot,
)
from .allocation import _ladder, _stop_procedure
from .describe import _describe_plan
from .rules import decided_by


@dataclass
class Context:
    snapshot: CoordinatorSnapshot
    steps: list[float]
    profile: str
    profile_label: str
    temp_target: float
    temp_tolerance: float
    battery_floor: float
    import_min_w: float
    import_max_w: float
    minutes_since_change: float | None
    ramp_lock_minutes: float
    ramp_done: list[str] | tuple[str, ...]
    minutes_import_high: float | None
    step_down_delay_minutes: float
    morning_step_down_delay_minutes: float
    sun_up: bool | None
    sunrise: bool
    sunset: bool
    meter_lost_minutes: float | None
    base_load_w: float | None
    voltage_low_seconds: float | None
    voltage_debounce_s: float
    low_voltage_v: float
    held_down: list[str] | tuple[str, ...]
    estimate_used: bool
    trace: list[str] = field(default_factory=list)
    plans: dict[str, MinerPlan] = field(default_factory=dict)
    candidates: list[MinerSnapshot] = field(default_factory=list)
    # Filled by the groups on the way: the highest step a warm miner may have (Limits), and
    # what the target asks of the allocation (up, down or None) and from what import.
    caps: dict[str, int] = field(default_factory=dict)
    import_w: float | None = None
    import_estimated: bool = False  # the meter is lost: Safety estimated the import
    direction: str | None = None
    excess_w: float = 0.0
    may_step_up: bool = True
    no_up_reason: str = ""  # why nothing starts or steps up (Limits)

    @property
    def energy(self):
        return self.snapshot.energy

    @property
    def too_warm_c(self) -> float:
        return self.temp_target + self.temp_tolerance

    def hold(self, m: MinerSnapshot, reason: str) -> MinerPlan:
        return MinerPlan(ACTION_HOLD, limit_w=m.power_limit_w, reason=reason)

    def stop(self, m: MinerSnapshot, reason: str) -> MinerPlan:
        if m.is_stopped:
            return MinerPlan(ACTION_HOLD, reason="stays stopped")
        procedure = _stop_procedure(m)
        if procedure is None:
            low = _ladder(m, self.steps)[0]
            self.trace.append(f"{m.name}: no stop method (no relay or pause switch) → lowest step instead")
            return MinerPlan(ACTION_SET_LIMIT, limit_w=low, reason="no stop method")
        method, entity = procedure
        return MinerPlan(ACTION_STOP, reason=reason, method=method, target_entity_id=entity)

    def to_step(self, m: MinerSnapshot, limit_w: float, reason: str) -> MinerPlan:
        """Plan for moving a miner to a step, starting it first if it is stopped."""
        if m.is_stopped:
            procedure = _stop_procedure(m)
            method, entity = procedure if procedure else (None, None)
            return MinerPlan(
                ACTION_START, limit_w=limit_w, reason=reason, method=method, target_entity_id=entity
            )
        if m.power_limit_w == limit_w:
            return self.hold(m, reason)
        return MinerPlan(ACTION_SET_LIMIT, limit_w=limit_w, reason=reason)

    def others_wait(self, changed: MinerSnapshot | None, reason: str) -> None:
        for m in self.candidates:
            if m is not changed:
                self.plans[m.miner_id] = self.stop(m, reason) if m.is_stopped else self.hold(m, reason)

    def done(self, summary: str, rule: str | None = None) -> Decision:
        """The decision, with the rule that made it (decision/rules.py) named in the trace."""
        if rule is not None:
            self.trace.append(decided_by(rule))
        # In the order the miners are listed, whichever was decided first.
        names = {m.miner_id: m.name for m in self.snapshot.miners}
        plans = {
            m.miner_id: self.plans[m.miner_id] for m in self.snapshot.miners if m.miner_id in self.plans
        }
        if plans:
            self.trace.append("PROPOSE")
            parts = []
            for mid, plan in plans.items():
                text = _describe_plan(plan)
                self.trace.append(f"{names[mid]} → {text}")
                parts.append(f"{names[mid]} {text}")
            summary += " → " + ", ".join(parts)
        return Decision(summary=summary, trace=self.trace, plans=plans)
