"""Protocol interfaces and shared data classes for Solar Smart Miner."""
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class EnergySnapshot:
    solar_production_w: float | None
    grid_consumption_w: float | None = None
    battery_soc_pct: float | None = None
    miner_consumption_sum_w: float | None = None
    solar_fault: bool = False
    mock_solar: bool = False
    mock_consumption: bool = False


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


@dataclass
class CoordinatorSnapshot:
    energy: EnergySnapshot
    miners: list[MinerSnapshot] = field(default_factory=list)
