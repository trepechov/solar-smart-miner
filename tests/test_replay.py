"""Replay net: real farm moments must give the same proposal as when they were recorded.

tests/replay/*.jsonl holds decisions.jsonl lines: the readings and every argument of
build_decision, with the plans and summary the code gave. They were rebuilt from the reference
farm's recorder history with `scripts/replay.py export` (10-08: a paused morning, meter gaps,
the restarts read as stops, the sunset stop/start loop; 10-11 and 10-16: raises on chips still
cooled by the miner's restart, band 64/10 and 67/10). A rule change that alters one of them
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


def _record(name: str, at: str) -> dict:
    lines = (REPLAY_DIR / name).read_text(encoding="utf-8").splitlines()
    return next(json.loads(line) for line in lines if json.loads(line)["ts"][11:19] == at)


@pytest.mark.parametrize("at", ["11:39:04", "12:05:04"])
def test_a_too_warm_miner_isnt_raised_on_its_restart_cooled_chips(at) -> None:
    # 2026-10-11 (0.8.1): Brod3 stepped down 2,500 -> 2,300 W at 74 to 75 °C; five minutes later
    # its chips read 55 to 57 °C and the farm raised it straight back, 19 times that day. Now it
    # holds until its chips have recovered; without the recovery it is raised, as on the farm.
    snapshot, inputs = load_record(_record("2026-10-11-recovery.jsonl", at))

    assert inputs["recovering"]["miner3"] >= 74.0
    assert build_decision(snapshot, **inputs).plans["miner3"].action == "hold"
    assert build_decision(snapshot, **{**inputs, "recovering": {}}).plans["miner3"].limit_w == 2500.0


def test_a_miner_far_below_the_band_is_raised_while_its_chips_recover() -> None:
    # 2026-10-16 10:16: Brod2 raised 2,300 -> 2,500 W four minutes after its own change, chips at
    # 51 °C; before the change it ran at 56 °C, more than the tolerance below the 67 °C target.
    # One step can't take it over the band (it settled at about 62 °C), so it still goes up.
    snapshot, inputs = load_record(_record("2026-10-16-recovery.jsonl", "10:16:24"))

    assert inputs["recovering"] == {"miner2": 56.0}
    assert build_decision(snapshot, **inputs).plans["miner2"].limit_w == 2500.0
