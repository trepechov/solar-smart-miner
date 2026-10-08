"""Log of the decision's inputs and outcome: <config>/solar_smart_miner/decisions.jsonl.

One line whenever the plans change (their fingerprints, not the summary text, which carries
live wattages). A line holds everything `build_decision` was given, so any line can be run
again through newer code (tests/test_replay.py, scripts/replay.py): a changed proposal is
seen before a release, not on the farm. Its own file, not actions.jsonl: the action log's
tail restores the ramp lock after a restart, and decision lines would push the commands out
of it.
"""
from __future__ import annotations

import inspect
from dataclasses import asdict, fields
from typing import Any

from homeassistant.util import dt as dt_util

from .jsonl_log import JsonlLog
from .protocols import CoordinatorSnapshot, Decision, EnergySnapshot, MinerSnapshot

DECISIONS_FILE = "decisions.jsonl"


def plans_fingerprint(decision: Decision) -> str:
    return ";".join(f"{mid}:{plan.fingerprint}" for mid, plan in sorted(decision.plans.items()))


def build_record(
    snapshot: CoordinatorSnapshot, inputs: dict[str, Any], decision: Decision
) -> dict[str, Any]:
    """One log line: the readings, the other arguments of build_decision, and what it decided."""
    return {
        "ts": dt_util.now().isoformat(timespec="seconds"),
        "energy": asdict(snapshot.energy),
        "miners": [asdict(m) for m in snapshot.miners],
        "inputs": dict(inputs),
        "summary": decision.summary,
        "plans": {mid: asdict(plan) for mid, plan in decision.plans.items()},
        "trace": decision.trace,
    }


def _known(cls, data: dict[str, Any]) -> dict[str, Any]:
    """Only the fields the dataclass still has: older lines replay on newer code."""
    names = {f.name for f in fields(cls)}
    return {k: v for k, v in data.items() if k in names}


def load_record(record: dict[str, Any]) -> tuple[CoordinatorSnapshot, dict[str, Any]]:
    """(snapshot, keyword arguments) to call build_decision with again. Arguments the decision
    no longer takes are left out (a line from an older version replays, by today's rules)."""
    from .decision import build_decision

    snapshot = CoordinatorSnapshot(
        energy=EnergySnapshot(**_known(EnergySnapshot, record["energy"])),
        miners=[MinerSnapshot(**_known(MinerSnapshot, m)) for m in record["miners"]],
    )
    takes = set(inspect.signature(build_decision).parameters)
    return snapshot, {k: v for k, v in record["inputs"].items() if k in takes}


class DecisionLog(JsonlLog):
    file_name = DECISIONS_FILE
    label = "decision"

    def __init__(self, hass) -> None:
        super().__init__(hass)
        self._last: str | None = None  # fingerprint of the plans last written

    async def async_record(
        self, snapshot: CoordinatorSnapshot, inputs: dict[str, Any], decision: Decision
    ) -> None:
        fingerprint = plans_fingerprint(decision)
        if fingerprint == self._last:
            return
        self._last = fingerprint
        await self.async_write(build_record(snapshot, inputs, decision))
