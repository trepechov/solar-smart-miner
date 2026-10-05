"""Append-only log of every AI advice request: what it was given and what it said.

One JSON object per line in <config>/solar_smart_miner/ai_log.jsonl, so it can be read
with a text editor, `jq` or a script. The file rotates at MAX_BYTES and keeps BACKUPS
older files (ai_log.jsonl.1, .2, ...). Nothing here changes the miners.
"""
from __future__ import annotations

import json
import logging
from collections import deque
from pathlib import Path
from typing import Any

from homeassistant.core import HomeAssistant
from homeassistant.util import dt as dt_util

from .protocols import AiAdvice, CoordinatorSnapshot

_LOGGER = logging.getLogger(__name__)

LOG_DIR = "solar_smart_miner"
LOG_FILE = "ai_log.jsonl"
MAX_BYTES = 5_000_000
BACKUPS = 2
HISTORY_SIZE = 20  # entries kept in memory for the sensor / dashboard widget


def build_record(
    snapshot: CoordinatorSnapshot,
    messages: list[dict[str, str]],
    *,
    profile: str,
    temp_ceiling: float,
    battery_floor: float,
) -> dict[str, Any]:
    """Everything the AI was given, captured when the request is sent."""
    energy = snapshot.energy
    decision = snapshot.decision
    names = {m.miner_id: m.name for m in snapshot.miners}
    return {
        "ts": dt_util.now().isoformat(timespec="seconds"),
        "profile": profile,
        "temp_ceiling_c": temp_ceiling,
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
            }
            for m in snapshot.miners
        ],
        "rules": {
            "summary": decision.summary if decision else None,
            "proposals_w": {names.get(k, k): v for k, v in decision.proposals.items()}
            if decision
            else {},
        },
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
            {"miner": a.get("miner"), "action": a.get("action"), "reason": a.get("reason")}
            for a in ai.get("actions") or []
        ],
        "error": entry.get("error"),
        "available_w": inputs.get("available_for_miners_w"),
        "pv_w": inputs.get("pv_actual_w"),
        "forecast_w": inputs.get("forecast_now_w"),
    }


class AiLog:
    def __init__(self, hass: HomeAssistant) -> None:
        self._hass = hass
        self.path = Path(hass.config.path(LOG_DIR, LOG_FILE))
        self.history: deque[dict[str, Any]] = deque(maxlen=HISTORY_SIZE)  # newest first

    # --- file work (runs in the executor) ---------------------------------

    def _write(self, line: str) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        if self.path.exists() and self.path.stat().st_size + len(line) > MAX_BYTES:
            for n in range(BACKUPS, 0, -1):
                src = self.path if n == 1 else self.path.with_name(f"{LOG_FILE}.{n - 1}")
                if src.exists():
                    src.replace(self.path.with_name(f"{LOG_FILE}.{n}"))
        with self.path.open("a", encoding="utf-8") as fh:
            fh.write(line)

    def _read_tail(self, count: int) -> list[dict[str, Any]]:
        if not self.path.exists():
            return []
        entries: list[dict[str, Any]] = []
        for line in self.path.read_text(encoding="utf-8").splitlines()[-count:]:
            try:
                entries.append(json.loads(line))
            except ValueError:
                continue  # a half-written line after a crash
        return entries

    # --- public API -------------------------------------------------------

    async def async_load_history(self) -> None:
        """Refill the in-memory history from the file so the widget survives restarts."""
        try:
            entries = await self._hass.async_add_executor_job(self._read_tail, HISTORY_SIZE)
        except OSError as err:
            _LOGGER.warning("Could not read the AI log %s: %s", self.path, err)
            return
        self.history.clear()
        self.history.extendleft(summarise(e) for e in entries)  # oldest first -> newest ends first

    async def async_append(self, entry: dict[str, Any]) -> None:
        self.history.appendleft(summarise(entry))
        line = json.dumps(entry, ensure_ascii=False, separators=(",", ":")) + "\n"
        try:
            await self._hass.async_add_executor_job(self._write, line)
        except OSError as err:
            _LOGGER.warning("Could not write the AI log %s: %s", self.path, err)
