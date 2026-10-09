"""Protocol interfaces and shared data classes for Solar Smart Miner."""
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class EnergySnapshot:
    solar_production_w: float | None
    grid_consumption_w: float | None = None  # house consumption, miners included
    battery_soc_pct: float | None = None
    miner_consumption_sum_w: float | None = None
    solar_fault: bool = False
    mock_solar: bool = False
    mock_consumption: bool = False
    grid_net_w: float | None = None  # + exporting to grid, - importing
    available_for_miners_w: float | None = None  # power miners could draw without importing
    # Reference readings, shown to the AI and written to its log. Not used by the rules.
    pv_power_w: float | None = None  # actual PV output (may be curtailed by the inverters)
    forecast_now_w: float | None = None  # forecast: what the panels could give right now
    forecast_next_hour_w: float | None = None
    forecast_remaining_kwh: float | None = None  # forecast: energy still to come today
    voltage_v: float | None = None  # supply voltage, if a voltage sensor is configured (rules use it)


@dataclass
class MinerSnapshot:
    miner_id: str
    ip: str
    power_w: float | None
    power_limit_w: float | None
    min_power_w: float | None
    max_power_w: float | None
    temperature_c: float | None
    is_available: bool
    power_limit_entity_id: str | None
    hashrate_th: float | None = None
    efficiency_jth: float | None = None
    name: str = ""
    # How the miner can be stopped and started again (see decision.py): a relay switch
    # the user configured, else the miner's own hass-miner "active" switch (pause).
    switch_entity_id: str | None = None
    relay_entity_id: str | None = None
    is_stopped: bool = False  # paused via its switch, or its relay is off
    # Minutes since the power limit was last seen to change. A miner re-tunes for
    # a few minutes after that, so None (never seen to change) counts as settled.
    minutes_since_limit_change: float | None = None


# MinerPlan.action values
ACTION_SET_LIMIT = "set_limit"  # running miner: move to another power step
ACTION_START = "start"  # stopped miner: switch on, then run at limit_w
ACTION_STOP = "stop"  # running miner: not enough power even for the lowest step
ACTION_HOLD = "hold"  # leave as is

STOP_METHOD_RELAY = "relay"
STOP_METHOD_PAUSE = "pause"


@dataclass
class MinerPlan:
    """What the controller wants for one miner. Carried out only by control.py, on an Apply."""

    action: str
    limit_w: float | None = None  # the power step to run at (set_limit / start)
    reason: str = ""
    method: str | None = None  # how a stop / start is done: relay or pause
    target_entity_id: str | None = None  # the switch that does it

    @property
    def fingerprint(self) -> str:
        """What an Apply carries out. The reason text is left out, so rewording doesn't block it."""
        limit = "" if self.limit_w is None else f"{self.limit_w:g}"
        return f"{self.action}|{limit}|{self.method or ''}|{self.target_entity_id or ''}"


@dataclass
class Decision:
    """Outcome of one decision cycle. Applied to miners only through control.py."""

    summary: str
    trace: list[str] = field(default_factory=list)
    plans: dict[str, MinerPlan] = field(default_factory=dict)  # miner_id -> plan

    @property
    def proposals(self) -> dict[str, float]:
        """miner_id -> power limit W, for the plans that set one."""
        return {
            mid: plan.limit_w
            for mid, plan in self.plans.items()
            if plan.action in (ACTION_SET_LIMIT, ACTION_START) and plan.limit_w is not None
        }


@dataclass
class AiAdvice:
    """The AI advisor's latest answer about the decision. Advisory only: it is never applied."""

    text: str
    model: str
    requested_at: str  # ISO timestamp
    latency_s: float
    error: str | None = None
    summary: str = ""  # one-line verdict (empty if the answer wasn't structured)
    actions: list[dict[str, str]] = field(default_factory=list)  # miner / action / reason / note
    raw: str = ""  # the model's reply exactly as received


@dataclass
class CoordinatorSnapshot:
    energy: EnergySnapshot
    miners: list[MinerSnapshot] = field(default_factory=list)
    decision: Decision | None = None
    decision_history: list[dict[str, str]] = field(default_factory=list)
    ai_advice: AiAdvice | None = None
