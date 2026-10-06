"""Tests for the AI decision log (JSONL file, rotation, widget history)."""
from __future__ import annotations

import json

from custom_components.solar_smart_miner import ai_log, jsonl_log
from custom_components.solar_smart_miner.ai_log import (
    AiLog,
    build_record,
    complete_record,
    summarise,
)
from custom_components.solar_smart_miner.protocols import (
    ACTION_SET_LIMIT,
    AiAdvice,
    CoordinatorSnapshot,
    Decision,
    EnergySnapshot,
    MinerPlan,
    MinerSnapshot,
)

MESSAGES = [{"role": "system", "content": "sys"}, {"role": "user", "content": "the prompt"}]


def _snapshot() -> CoordinatorSnapshot:
    return CoordinatorSnapshot(
        energy=EnergySnapshot(
            solar_production_w=None,
            grid_consumption_w=3900.0,
            grid_net_w=-5.0,
            miner_consumption_sum_w=3850.0,
            available_for_miners_w=3845.0,
            pv_power_w=3926.0,
            forecast_now_w=9888.0,
            forecast_next_hour_w=9039.0,
            forecast_remaining_kwh=28.0,
        ),
        miners=[
            MinerSnapshot(
                miner_id="192.168.1.101",
                ip="192.168.1.101",
                name="Brod1",
                power_w=1300.0,
                power_limit_w=1300.0,
                min_power_w=500.0,
                max_power_w=1600.0,
                temperature_c=38.0,
                is_available=True,
                power_limit_entity_id="number.x",
                hashrate_th=58.0,
            )
        ],
        decision=Decision(
            summary="Solar-max: budget 3,845 W",
            plans={"192.168.1.101": MinerPlan(ACTION_SET_LIMIT, limit_w=1100.0, reason="budget")},
        ),
    )


def _advice(**kw) -> AiAdvice:
    return AiAdvice(
        text="Sun is setting | Brod1: reduce (not_enough_energy)",
        model="m/x",
        requested_at="t",
        latency_s=1.2,
        summary="Sun is setting",
        actions=[{"miner": "Brod1", "action": "reduce", "reason": "not_enough_energy", "note": ""}],
        raw='{"summary": "Sun is setting"}',
        **kw,
    )


def _record() -> dict:
    return build_record(
        _snapshot(), MESSAGES, profile="solar_max", temp_target=65.0, temp_tolerance=10.0, battery_floor=20.0
    )


def test_record_captures_inputs_forecast_miners_rules_and_prompt() -> None:
    rec = _record()

    assert rec["profile"] == "solar_max"
    assert (rec["temp_target_c"], rec["temp_tolerance_c"]) == (65.0, 10.0)
    assert rec["inputs"]["pv_actual_w"] == 3926.0
    assert rec["inputs"]["forecast_now_w"] == 9888.0
    assert rec["inputs"]["forecast_remaining_kwh"] == 28.0
    assert rec["inputs"]["available_for_miners_w"] == 3845.0
    assert rec["miners"][0]["name"] == "Brod1"
    assert rec["miners"][0]["limit_w"] == 1300.0
    assert rec["rules"] == {
        "summary": "Solar-max: budget 3,845 W",
        "proposals_w": {"Brod1": 1100.0},
        "plans": {
            "Brod1": {"action": "set_limit", "limit_w": 1100.0, "method": None, "reason": "budget"}
        },
    }
    assert rec["miners"][0]["stopped"] is False
    assert rec["prompt"] == "the prompt"
    assert rec["knowledge"] == {"situation": None, "facts": []}
    assert rec["ts"]


def test_record_keeps_which_knowledge_was_sent() -> None:
    rec = build_record(
        _snapshot(), MESSAGES, profile="solar_max", temp_target=65.0, temp_tolerance=10.0, battery_floor=20.0,
        knowledge={"situation": "sunset", "facts": ["rule.goal"]},
    )

    assert rec["knowledge"] == {"situation": "sunset", "facts": ["rule.goal"]}


def test_completed_record_adds_the_ai_answer_and_never_a_key() -> None:
    entry = complete_record(_record(), _advice())

    assert entry["model"] == "m/x"
    assert entry["error"] is None
    assert entry["ai"]["actions"][0]["action"] == "reduce"
    assert entry["ai"]["raw"] == '{"summary": "Sun is setting"}'
    assert "sk-" not in json.dumps(entry)


def test_summarise_gives_the_short_widget_form() -> None:
    short = summarise({**complete_record(_record(), _advice()), "ts": "2026-10-05T18:30:15+03:00"})

    assert short == {
        "time": "18:30:15",
        "summary": "Sun is setting",
        "actions": [{"miner": "Brod1", "action": "reduce", "reason": "not_enough_energy"}],
        "error": None,
        "available_w": 3845.0,
        "pv_w": 3926.0,
        "forecast_w": 9888.0,
    }


def test_summarise_falls_back_to_raw_text_when_not_structured() -> None:
    short = summarise({"ts": "2026-10-05T18:30:15+03:00", "ai": {"raw": "All good."}})

    assert short["summary"] == "All good."
    assert short["actions"] == []


async def test_append_writes_one_json_line_per_entry(hass) -> None:
    log = AiLog(hass)
    await log.async_append(complete_record(_record(), _advice()))
    await log.async_append(complete_record(_record(), _advice(error="x")))

    lines = log.path.read_text().splitlines()
    assert len(lines) == 2
    assert json.loads(lines[0])["inputs"]["pv_actual_w"] == 3926.0
    assert json.loads(lines[1])["error"] == "x"
    assert len(log.history) == 2


async def test_history_is_newest_first_and_bounded(hass) -> None:
    log = AiLog(hass)
    for i in range(ai_log.HISTORY_SIZE + 5):
        await log.async_append({"ts": f"2026-10-05T10:00:{i:02d}+00:00", "ai": {"summary": str(i)}})

    assert len(log.history) == ai_log.HISTORY_SIZE
    assert log.history[0]["summary"] == str(ai_log.HISTORY_SIZE + 4)


async def test_history_is_restored_from_the_file_after_restart(hass) -> None:
    first = AiLog(hass)
    for i in range(3):
        await first.async_append({"ts": f"2026-10-05T10:00:0{i}+00:00", "ai": {"summary": str(i)}})

    restarted = AiLog(hass)
    await restarted.async_load_history()

    assert [h["summary"] for h in restarted.history] == ["2", "1", "0"]


async def test_load_history_survives_a_missing_file_and_a_torn_line(hass) -> None:
    log = AiLog(hass)
    await log.async_load_history()
    assert list(log.history) == []

    await log.async_append({"ts": "2026-10-05T10:00:00+00:00", "ai": {"summary": "ok"}})
    with log.path.open("a") as fh:
        fh.write('{"ts": "2026-10-05T10:00:01+00:00", "ai": {"summ')  # crash mid-write
    fresh = AiLog(hass)
    await fresh.async_load_history()

    assert [h["summary"] for h in fresh.history] == ["ok"]


async def test_log_rotates_and_keeps_a_bounded_number_of_files(hass, monkeypatch) -> None:
    monkeypatch.setattr(jsonl_log, "MAX_BYTES", 400)
    log = AiLog(hass)
    for i in range(40):
        await log.async_append({"ts": "2026-10-05T10:00:00+00:00", "n": i, "pad": "x" * 100})

    names = sorted(p.name for p in log.path.parent.iterdir())
    assert names == ["ai_log.jsonl", "ai_log.jsonl.1", "ai_log.jsonl.2"]
    assert json.loads(log.path.read_text().splitlines()[-1])["n"] == 39


async def test_a_write_failure_is_logged_not_raised(hass, monkeypatch, caplog) -> None:
    log = AiLog(hass)

    def boom(_line: str) -> None:
        raise OSError("disk full")

    monkeypatch.setattr(log, "_write", boom)
    await log.async_append({"ts": "2026-10-05T10:00:00+00:00", "ai": {"summary": "kept in memory"}})

    assert "Could not write the AI log" in caplog.text
    assert log.history[0]["summary"] == "kept in memory"
