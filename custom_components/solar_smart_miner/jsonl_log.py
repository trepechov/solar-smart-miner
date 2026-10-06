"""A rotating JSON-lines file under <config>/solar_smart_miner/, shared by the AI log and the action log.

One JSON object per line, so a log can be read with a text editor, `jq` or a script. The
file rotates at MAX_BYTES and keeps BACKUPS older files (name.1, name.2, ...). Writing
happens in the executor; a failed write is logged, never raised.
"""
from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

from homeassistant.core import HomeAssistant

_LOGGER = logging.getLogger(__name__)

LOG_DIR = "solar_smart_miner"
MAX_BYTES = 5_000_000
BACKUPS = 2


class JsonlLog:
    file_name = "log.jsonl"  # set by each log
    label = "log"  # how warnings name it

    def __init__(self, hass: HomeAssistant) -> None:
        self._hass = hass
        self.path = Path(hass.config.path(LOG_DIR, self.file_name))

    # --- file work (runs in the executor) ---------------------------------

    def _write(self, line: str) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        if self.path.exists() and self.path.stat().st_size + len(line) > MAX_BYTES:
            for n in range(BACKUPS, 0, -1):
                src = self.path if n == 1 else self.path.with_name(f"{self.file_name}.{n - 1}")
                if src.exists():
                    src.replace(self.path.with_name(f"{self.file_name}.{n}"))
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

    async def async_read_tail(self, count: int) -> list[dict[str, Any]] | None:
        """The last `count` entries, oldest first; None if the file couldn't be read."""
        try:
            return await self._hass.async_add_executor_job(self._read_tail, count)
        except OSError as err:
            _LOGGER.warning("Could not read the %s log %s: %s", self.label, self.path, err)
            return None

    async def async_write(self, entry: dict[str, Any]) -> None:
        line = json.dumps(entry, ensure_ascii=False, separators=(",", ":")) + "\n"
        try:
            await self._hass.async_add_executor_job(self._write, line)
        except OSError as err:
            _LOGGER.warning("Could not write the %s log %s: %s", self.label, self.path, err)
