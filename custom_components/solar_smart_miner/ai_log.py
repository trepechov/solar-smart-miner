"""Append-only log of every AI advice request: what it was given and what it said.

One JSON object per line in <config>/solar_smart_miner/ai_log.jsonl (file handling in
jsonl_log.py). Nothing here changes the miners.
"""
from __future__ import annotations

from collections import deque
from typing import Any

from homeassistant.core import HomeAssistant
from homeassistant.util import dt as dt_util

from .jsonl_log import JsonlLog
from .protocols import AiAdvice, CoordinatorSnapshot

LOG_FILE = "ai_log.jsonl"
HISTORY_SIZE = 20  # entries kept in memory for the sensor / dashboard widget


def build_record(
    snapshot: CoordinatorSnapshot,
    messages: list[dict[str, str]],
    *,
    profile: str,
    temp_target: float,
    temp_tolerance: float,
    battery_floor: float,
    knowledge: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Everything the AI was given, captured when the request is sent.

    `knowledge` is the situation and the ids of the knowledge-base facts in the prompt.
    """
    energy = snapshot.energy
    decision = snapshot.decision
    names = {m.miner_id: m.name for m in snapshot.miners}
    return {
        "ts": dt_util.now().isoformat(timespec="seconds"),
        "profile": profile,
        "temp_target_c": temp_target,
        "temp_tolerance_c": temp_tolerance,
        "battery_floor_pct": battery_floor,
        "inputs": {
            "solar_w": energy.solar_production_w,
            "pv_actual_w": energy.pv_power_w,
            "grid_net_w": energy.grid_net_w,
            "house_w": energy.grid_consumption_w,
            "miners_w": energy.miner_consumption_sum_w,
            "available_for_miners_w": energy.available_for_miners_w,
            "battery_pct": energy.battery_soc_pct,
            "forecast_now_w": energy.forecast_now_w,
            "forecast_next_hour_w": energy.forecast_next_hour_w,
            "forecast_remaining_kwh": energy.forecast_remaining_kwh,
        },
        "miners": [
            {
                "name": m.name,
                "available": m.is_available,
                "power_w": m.power_w,
                "limit_w": m.power_limit_w,
                "min_w": m.min_power_w,
                "max_w": m.max_power_w,
                "temp_c": m.temperature_c,
                "hashrate_th": m.hashrate_th,
                "stopped": m.is_stopped,
                "min_since_limit_change": m.minutes_since_limit_change,
            }
            for m in snapshot.miners
        ],
        "rules": {
            "summary": decision.summary if decision else None,
            "proposals_w": {names.get(k, k): v for k, v in decision.proposals.items()}
            if decision
            else {},
            "plans": {
                names.get(k, k): {
                    "action": plan.action,
                    "limit_w": plan.limit_w,
                    "method": plan.method,
                    "reason": plan.reason,
                }
                for k, plan in decision.plans.items()
            }
            if decision
            else {},
        },
        "knowledge": knowledge or {"situation": None, "facts": []},
        "prompt": next((m["content"] for m in messages if m["role"] == "user"), ""),
    }


def complete_record(record: dict[str, Any], advice: AiAdvice) -> dict[str, Any]:
    """The finished log entry: the request record plus the AI's answer."""
    return {
        **record,
        "model": advice.model,
        "latency_s": advice.latency_s,
        "error": advice.error,
        "ai": {"summary": advice.summary, "actions": advice.actions, "raw": advice.raw or advice.text},
    }


def summarise(entry: dict[str, Any]) -> dict[str, Any]:
    """Short form of a log entry for the sensor attribute and the dashboard widget."""
    ts = str(entry.get("ts") or "")
    inputs = entry.get("inputs") or {}
    ai = entry.get("ai") or {}
    return {
        "time": ts[11:19] if len(ts) >= 19 else ts,
        "summary": ai.get("summary") or str(ai.get("raw") or "")[:160],
        "actions": [
            {"miner": a.get("miner"), "action": a.get("action"), "target_w": a.get("target_w"),
             "reason": a.get("reason")}
            for a in ai.get("actions") or []
        ],
        "error": entry.get("error"),
        "available_w": inputs.get("available_for_miners_w"),
        "pv_w": inputs.get("pv_actual_w"),
        "forecast_w": inputs.get("forecast_now_w"),
    }


class AiLog(JsonlLog):
    file_name = LOG_FILE
    label = "AI"

    def __init__(self, hass: HomeAssistant) -> None:
        super().__init__(hass)
        self.history: deque[dict[str, Any]] = deque(maxlen=HISTORY_SIZE)  # newest first

    async def async_load_history(self) -> None:
        """Refill the in-memory history from the file so the widget survives restarts."""
        entries = await self.async_read_tail(HISTORY_SIZE)
        if entries is None:
            return
        self.history.clear()
        self.history.extendleft(summarise(e) for e in entries)  # oldest first -> newest ends first

    async def async_append(self, entry: dict[str, Any]) -> None:
        self.history.appendleft(summarise(entry))
        await self.async_write(entry)
