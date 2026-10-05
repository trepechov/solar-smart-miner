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


@dataclass
class Decision:
    """Outcome of one decision cycle. Preview only — never applied to miners."""

    summary: str
    trace: list[str] = field(default_factory=list)
    proposals: dict[str, float] = field(default_factory=dict)  # miner_id -> limit W


@dataclass
class AiAdvice:
    """The AI advisor's latest answer to the decision preview. Advisory only."""

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
