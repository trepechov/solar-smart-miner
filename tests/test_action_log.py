"""Tests for the action log: one JSON line per command event."""
from __future__ import annotations

import json

from pytest_homeassistant_custom_component.common import MockConfigEntry, async_mock_service

from custom_components.solar_smart_miner import jsonl_log
from custom_components.solar_smart_miner.action_log import ActionLog, build_entry, summarise
from custom_components.solar_smart_miner.config_flow import (
    CONF_BATTERY_ENTITY,
    CONF_GRID_ENTITY,
    CONF_SOLAR_ENTITY,
)
from custom_components.solar_smart_miner.const import CONF_CONTROL_MODE, DOMAIN
from custom_components.solar_smart_miner.control import CommandEvent
from custom_components.solar_smart_miner.coordinator import SolarMinerCoordinator
from custom_components.solar_smart_miner.protocols import AiAdvice, MinerPlan

SOLAR = "sensor.solar_power"
GRID = "sensor.grid_consumption"
LIMITS = {"min": 500.0, "max": 3500.0}


def _event(status: str = "pending", command_id: str = "abc12345", **kw) -> CommandEvent:
    base = dict(
        command_id=command_id, trigger="manual", miner_id="192.168.1.10", miner_name="Brod1",
        plan=MinerPlan("set_limit", limit_w=1300.0, reason="budget"), status=status,
        reason="sent", calls=[{"service": "number.set_value", "entity_id": "number.x", "value": 1300.0}],
        ts="2026-10-06T11:30:00+02:00",
    )
    return CommandEvent(**{**base, **kw})


def _lines(log: ActionLog) -> list[dict]:
    return [json.loads(line) for line in log.path.read_text().splitlines()]


async def _setup(hass, add_hass_miner, *, ai_actions=None):
    add_hass_miner("192.168.1.10", name="Brod1", limit="1100", power="1100", temperature="55",
                   hashrate="40", limit_attrs=LIMITS)
    hass.states.async_set(SOLAR, "5000")
    hass.states.async_set(GRID, "1500")
    entry = MockConfigEntry(
        domain=DOMAIN,
        data={CONF_SOLAR_ENTITY: SOLAR, CONF_GRID_ENTITY: GRID, CONF_BATTERY_ENTITY: None},
        options={CONF_CONTROL_MODE: "manual"},
    )
    entry.add_to_hass(hass)
    coordinator = SolarMinerCoordinator(hass, entry)
    await coordinator.async_refresh()
    if ai_actions is not None:
        advice = AiAdvice(
            text="x", model="m", requested_at="2026-10-06T11:29:00+02:00", latency_s=1.0,
            actions=ai_actions,
        )
        coordinator._ai_advice = advice  # survives the refresh an apply does
        coordinator.data.ai_advice = advice
    return coordinator


async def test_apply_writes_a_pending_line_then_an_ok_line(hass, add_hass_miner, monkeypatch) -> None:
    import time as time_module

    now = [1000.0]
    monkeypatch.setattr(time_module, "monotonic", lambda: now[0])
    coordinator = await _setup(hass, add_hass_miner)
    calls = async_mock_service(hass, "number", "set_value")
    miner_id, plan = next(iter(coordinator.data.decision.plans.items()))

    await coordinator.async_apply_shown(miner_id, plan.fingerprint)
    # The number reads back the limit and the miner restarts with it (no power meanwhile) ...
    hass.states.async_set(calls[0].data["entity_id"], str(plan.limit_w), LIMITS)
    hass.states.async_set("sensor.miner_00_00_00_00_00_01_miner_consumption", "unknown")
    await coordinator._async_update_data()
    # ... then draws its new limit: only now is the command done.
    now[0] += 90
    hass.states.async_set("sensor.miner_00_00_00_00_00_01_miner_consumption", str(plan.limit_w))
    await coordinator._async_update_data()

    first, outcome = _lines(coordinator.action_log)
    assert (first["result"], outcome["result"]) == ("pending", "ok")
    assert first["command_id"] == outcome["command_id"]
    assert (first["miner"], first["trigger"]) == ("Brod1", "manual")
    assert first["plan"] == {
        "action": "set_limit", "limit_w": plan.limit_w, "method": None, "reason": "budget"
    }
    assert first["before"]["limit_w"] == 1100.0 and first["before"]["temp_c"] == 55.0
    assert first["energy"]["available_w"] is not None
    assert first["rule_summary"] == coordinator.data.decision.summary
    assert first["calls"][0]["service"] == "number.set_value"
    assert "after" in outcome and "before" not in outcome  # the outcome line shows the miner after


async def test_refused_press_is_logged_with_its_reason(hass, add_hass_miner) -> None:
    coordinator = await _setup(hass, add_hass_miner)
    async_mock_service(hass, "number", "set_value")
    miner_id, plan = next(iter(coordinator.data.decision.plans.items()))

    await coordinator.async_apply_shown(miner_id, "set_limit|900||")  # not what is proposed
    await hass.async_block_till_done()

    (line,) = _lines(coordinator.action_log)
    assert line["result"] == "refused"
    assert line["reason"].startswith("changed")


async def test_ai_view_is_captured_when_there_is_advice(hass, add_hass_miner) -> None:
    coordinator = await _setup(
        hass, add_hass_miner,
        ai_actions=[{"miner": "Brod1", "action": "hold", "reason": "still tuning", "note": ""}],
    )
    async_mock_service(hass, "number", "set_value")
    miner_id, plan = next(iter(coordinator.data.decision.plans.items()))

    await coordinator.async_apply_shown(miner_id, plan.fingerprint)

    ai = _lines(coordinator.action_log)[0]["ai"]
    assert ai["action"] == "hold" and ai["reason"] == "still tuning"
    assert isinstance(ai["age_s"], int)


async def test_ai_view_is_null_without_advice(hass, add_hass_miner) -> None:
    coordinator = await _setup(hass, add_hass_miner)
    async_mock_service(hass, "number", "set_value")
    miner_id, plan = next(iter(coordinator.data.decision.plans.items()))

    await coordinator.async_apply_shown(miner_id, plan.fingerprint)

    assert _lines(coordinator.action_log)[0]["ai"] is None


async def test_ai_view_is_null_when_the_advice_does_not_mention_the_miner(hass, add_hass_miner) -> None:
    coordinator = await _setup(
        hass, add_hass_miner, ai_actions=[{"miner": "Other", "action": "stop", "reason": "", "note": ""}]
    )
    event = _event()

    assert build_entry(event, coordinator.data, first=True)["ai"] is None


async def test_build_entry_without_a_snapshot_still_records_the_command() -> None:
    entry = build_entry(_event(), None, first=True)

    assert entry["before"] is None and entry["energy"] is None and entry["ai"] is None
    assert entry["plan"]["limit_w"] == 1300.0


async def test_history_is_newest_first_and_bounded_and_reloads(hass) -> None:
    log = ActionLog(hass)
    for i in range(25):
        await log.async_record(_event("refused", command_id=f"c{i}", reason=str(i)), None)

    assert len(log.history) == 20
    assert log.history[0]["reason"] == "24"
    assert log.history[0]["plan"] == "1,300 W" and log.history[0]["result"] == "refused"

    restarted = ActionLog(hass)
    await restarted.async_load_history()
    assert [h["reason"] for h in restarted.history][:2] == ["24", "23"]


async def test_summarise_describes_stops_and_starts() -> None:
    stop = {"plan": {"action": "stop", "method": "pause"}, "ts": "2026-10-06T11:30:00+02:00",
            "miner": "Brod1", "result": "ok"}
    start = {"plan": {"action": "start", "limit_w": 1100.0}, "ts": "2026-10-06T11:31:00+02:00"}

    assert summarise(stop)["plan"] == "stop (pause)" and summarise(stop)["time"] == "11:30:00"
    assert summarise(start)["plan"] == "start at 1,100 W"


async def test_action_log_rotates_like_the_ai_log(hass, monkeypatch) -> None:
    monkeypatch.setattr(jsonl_log, "MAX_BYTES", 600)
    log = ActionLog(hass)
    for i in range(40):
        await log.async_record(_event("refused", command_id=f"c{i}", reason="x" * 50), None)

    names = sorted(p.name for p in log.path.parent.iterdir() if p.name.startswith("actions"))
    assert names == ["actions.jsonl", "actions.jsonl.1", "actions.jsonl.2"]


async def test_a_write_failure_is_logged_not_raised(hass, monkeypatch, caplog) -> None:
    log = ActionLog(hass)

    def boom(_line: str) -> None:
        raise OSError("disk full")

    monkeypatch.setattr(log, "_write", boom)
    await log.async_record(_event("refused"), None)

    assert "Could not write the action log" in caplog.text
    assert log.history[0]["result"] == "refused"  # still shown on the sensor
