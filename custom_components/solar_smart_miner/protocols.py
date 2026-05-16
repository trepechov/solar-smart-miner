"""Protocol interfaces and shared data classes for Solar Smart Miner.

U9 stub: EnergySnapshot with mock_solar flag.
Full protocols (AgentProtocol, NotifierProtocol, SafetyProtocol, MinerSnapshot, etc.) land in U3.
"""
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class EnergySnapshot:
    solar_production_w: float | None
    mock_solar: bool = False
