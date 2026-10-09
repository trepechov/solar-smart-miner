"""Append-only log of what was applied to the miners: <config>/solar_smart_miner/actions.jsonl.

One line per command event (sent, refused, finished), joined by `command_id`. It records the
plan, the miner before and after, the energy picture, the rule summary and what the AI
said about the same miner, so the manual phase can show how often the owner applied a plan
the AI disagreed with, and whether applied plans held up (open.ai-learning).
"""
from __future__ import annotations

from collections import deque
from datetime import datetime
from typing import Any

from homeassistant.core import HomeAssistant
from homeassistant.util import dt as dt_util

from .control import RESULT_PENDING, CommandEvent
from .decision import _describe_plan
from .jsonl_log import JsonlLog
from .protocols import AiAdvice, CoordinatorSnapshot, MinerPlan, MinerSnapshot

ACTIONS_FILE = "actions.jsonl"
HISTORY_SIZE = 20  # entries kept in memory for the "Last action" sensor and the card


def _miner_state(miner: MinerSnapshot | None) -> dict[str, Any] | None:
    if miner is None:
        return None
    return {
        "limit_w": miner.power_limit_w,
        "power_w": miner.power_w,
        "temp_c": miner.temperature_c,
        "hashrate_th": miner.hashrate_th,
        "stopped": miner.is_stopped,
    }


def _ai_view(advice: AiAdvice | None, miner_name: str) -> dict[str, Any] | None:
    """What the AI's latest answer said about this miner, and how old that answer is."""
    if advice is None or advice.error:
        return None
    action = next(
        (a for a in advice.actions if str(a.get("miner", "")).strip().lower() == miner_name.lower()),
        None,
    )
    if action is None:
        return None
    try:
        age_s = round((dt_util.now() - datetime.fromisoformat(advice.requested_at)).total_seconds())
    except (TypeError, ValueError):
        age_s = None
    return {
        "action": action.get("action"), "target_w": action.get("target_w"),
        "reason": action.get("reason"), "age_s": age_s,
    }


def build_entry(
    event: CommandEvent, snapshot: CoordinatorSnapshot | None, *, first: bool
) -> dict[str, Any]:
    """The log line for one command event.

    The first event of a command carries `before` (the miner as the owner pressed Apply);
    the later one carries `after` (the miner when the outcome was known).
    """
    plan = event.plan
    miner = next((m for m in snapshot.miners if m.miner_id == event.miner_id), None) if snapshot else None
    energy = snapshot.energy if snapshot else None
    entry: dict[str, Any] = {
        "ts": event.ts,
        "command_id": event.command_id,
        "trigger": event.trigger,
        "miner": event.miner_name,
        "miner_id": event.miner_id,
        "plan": {
            "action": plan.action,
            "limit_w": plan.limit_w,
            "method": plan.method,
            "reason": plan.reason,
        },
        "result": event.status,
        "reason": event.reason,
        "calls": event.calls,
        ("before" if first else "after"): _miner_state(miner),
    }
    if first:
        decision = snapshot.decision if snapshot else None
        entry["energy"] = {
            "grid_net_w": energy.grid_net_w,
            "available_w": energy.available_for_miners_w,
            "solar_w": energy.solar_production_w,
        } if energy else None
        entry["rule_summary"] = decision.summary if decision else None
        entry["ai"] = _ai_view(snapshot.ai_advice if snapshot else None, event.miner_name)
    return entry


def summarise(entry: dict[str, Any]) -> dict[str, Any]:
    """Short form of a log entry for the sensor attribute and the card."""
    ts = str(entry.get("ts") or "")
    plan = entry.get("plan") or {}
    text = _describe_plan(
        MinerPlan(
            plan.get("action") or "hold",
            limit_w=plan.get("limit_w"),
            reason=plan.get("reason") or "",
            method=plan.get("method"),
        )
    )
    return {
        "time": ts[11:19] if len(ts) >= 19 else ts,
        "miner": entry.get("miner"),
        "plan": text,
        "result": entry.get("result"),
        "reason": entry.get("reason"),
        "trigger": entry.get("trigger"),
        "command_id": entry.get("command_id"),
    }


class ActionLog(JsonlLog):
    file_name = ACTIONS_FILE
    label = "action"

    def __init__(self, hass: HomeAssistant) -> None:
        super().__init__(hass)
        self.history: deque[dict[str, Any]] = deque(maxlen=HISTORY_SIZE)  # newest first
        self._open: set[str] = set()  # commands whose first line is written, outcome still due
        self.last_sent_ts: str | None = None  # newest line that sent something, as loaded from disk

    async def async_load_history(self) -> None:
        entries = await self.async_read_tail(HISTORY_SIZE)
        if entries is None:
            return
        self.history.clear()
        self.history.extendleft(summarise(e) for e in entries)
        self.last_sent_ts = next((e.get("ts") for e in reversed(entries) if e.get("calls")), None)

    async def async_record(self, event: CommandEvent, snapshot: CoordinatorSnapshot | None) -> None:
        first = event.command_id not in self._open
        if event.status == RESULT_PENDING:
            self._open.add(event.command_id)
        else:
            self._open.discard(event.command_id)
        entry = build_entry(event, snapshot, first=first)
        self.history.appendleft(summarise(entry))
        await self.async_write(entry)
