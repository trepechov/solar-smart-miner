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
class CoordinatorSnapshot:
    energy: EnergySnapshot
    miners: list[MinerSnapshot] = field(default_factory=list)
    decision: Decision | None = None
    decision_history: list[dict[str, str]] = field(default_factory=list)
