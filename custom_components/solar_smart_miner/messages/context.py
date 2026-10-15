"""One poll of the day as the message rules see it, and the words they share.

The coordinator fills a DayContext after the decision; DayMessages (messages/__init__.py) adds
what it worked out from the polls before (mining, at the top step, a step down) and hands it to
each rule in turn. Nothing here feeds the decision.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime

from ..control import is_mining
from ..decision.describe import _w
from ..protocols import CoordinatorSnapshot, EnergySnapshot, MinerSnapshot

CONFIRM_POLLS = 2  # all at max and a stop must be seen this many polls in a row


@dataclass
class DayContext:
    now: datetime
    snapshot: CoordinatorSnapshot
    settled: bool  # no change is settling (no ramp lock running)
    restarting: set[str]  # miners restarting after a limit change (a restart reads as a stop)
    steps: list[float]  # the configured power steps
    too_warm_c: float  # the top of the temperature band
    sun_up: bool | None  # None: no sun.sun
    rising: bool | None  # sun.sun's `rising`: true until solar noon
    sunset: bool  # the sunset transition's latch; used only without sun.sun
    mode: str | None = None  # the control mode's label when it isn't Automatic
    manual: bool = False  # Manual mode: the decision's plans wait for the Apply button
    battery: bool = False  # Configure -> Farm says a battery is present
    picking_up: bool = False  # the first readable poll since HA started
    # Worked out by DayMessages before the rules run
    miners: list[MinerSnapshot] = field(default_factory=list)  # the available ones
    mining: list[MinerSnapshot] = field(default_factory=list)
    at_top: list[MinerSnapshot] = field(default_factory=list)  # mining at their top step
    down: tuple | None = None  # (miner, old W, new W or None for a stop, reason)
    memory: dict = field(default_factory=dict)  # per rule, for today (counters across polls)

    @property
    def energy(self) -> EnergySnapshot:
        return self.snapshot.energy

    @property
    def stamp(self) -> str:
        return self.now.isoformat(timespec="seconds")

    @property
    def noon_passed(self) -> bool | None:
        """Past solar noon: the sun is up and no longer rising. None without sun.sun."""
        if self.sun_up is None or self.rising is None:
            return None
        return self.sun_up and not self.rising


def actual_pv_w(energy: EnergySnapshot) -> float | None:
    """What the panels give now: the actual PV sensor, else the solar production reading."""
    return energy.pv_power_w if energy.pv_power_w is not None else energy.solar_production_w


def miner_name(miner: MinerSnapshot) -> str:
    return miner.name or miner.miner_id


def status_lines(ctx: DayContext) -> list[str]:
    """The farm's status, under every message: each miner's state, and the mode unless Automatic."""
    lines = []
    for m in ctx.snapshot.miners:
        if not m.is_available:
            state = "unavailable"
        elif is_mining(m):
            state = f"mining at {_w(m.power_limit_w)}" if m.power_limit_w is not None else "mining"
        else:
            state = "stopped"
        lines.append(f"• {miner_name(m)}: {state}")
    if ctx.mode:
        lines.append(f"Mode: {ctx.mode}")
    return lines


def time_of(iso: str | None) -> str:
    return datetime.fromisoformat(iso).strftime("%H:%M") if iso else "?"


def kw(watts: float) -> str:
    return f"{watts / 1000:.1f} kW"


def duration(start: str, end: str) -> str:
    minutes = round((datetime.fromisoformat(end) - datetime.fromisoformat(start)).total_seconds() / 60)
    hours, minutes = divmod(minutes, 60)
    return f"{hours} h {minutes} min" if hours else f"{minutes} min"


def message(*parts: str | list[str] | None) -> str:
    """Lines and lists of lines, the empty ones left out."""
    lines: list[str] = []
    for part in parts:
        if isinstance(part, list):
            lines += part
        elif part:
            lines.append(part)
    return "\n".join(lines)
