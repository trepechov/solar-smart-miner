"""Replay net: real farm moments must give the same proposal as when they were recorded.

tests/replay/*.jsonl holds decisions.jsonl lines: the readings and every argument of
build_decision, with the plans and summary the code gave. They were rebuilt from the reference
farm's recorder history with `scripts/replay.py export` (10-08: a paused morning, meter gaps,
the restarts read as stops, the sunset stop/start loop). A rule change that alters one of them
must update the line in the same commit and say which moments changed and why
(docs/plans/2026-10-09-001-refactor-decision-pipeline-plan.md, U0).
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from custom_components.solar_smart_miner.decision import build_decision
from custom_components.solar_smart_miner.decision_log import load_record, plans_fingerprint
from custom_components.solar_smart_miner.protocols import MinerPlan

REPLAY_DIR = Path(__file__).parent / "replay"
RECORDS = [
    pytest.param(json.loads(line), id=f"{path.stem} {json.loads(line)['ts'][11:19]}")
    for path in sorted(REPLAY_DIR.glob("*.jsonl"))
    for line in path.read_text(encoding="utf-8").splitlines()
    if line.strip()
]


def test_there_are_replay_records() -> None:
    assert len(RECORDS) >= 10


@pytest.mark.parametrize("record", RECORDS)
def test_a_recorded_moment_gives_the_recorded_proposal(record) -> None:
    snapshot, inputs = load_record(record)

    decision = build_decision(snapshot, **inputs)

    expected = ";".join(
        f"{mid}:{MinerPlan(**plan).fingerprint}" for mid, plan in sorted(record["plans"].items())
    )
    assert plans_fingerprint(decision) == expected
    assert decision.summary == record["summary"]
