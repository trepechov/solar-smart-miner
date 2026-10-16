"""Whether a miner's chips have recovered from its own restart (rule.temperature-band).

A change restarts the miner and its chips cool 15 to 25 °C; they are back near their settled
temperature about 5 minutes after the restart's low point (reference farm, 2026-10-15). Until
then a reading is lower than the miner runs at, and the band would read the restart as headroom
(2026-10-11: a too-warm miner stepped down and raised again on its restart-cooled chips, every
12 minutes for hours).

The chips have recovered once, measured from the lowest reading since the change, they rose less
than RECOVERY_RISE_C over the last RECOVERY_WINDOW_MIN. The window starts at the low point, never
across the restart: before and after it the chips read the same and would look flat.
"""
from __future__ import annotations

from collections import deque

RECOVERY_WINDOW_MIN = 5.0
RECOVERY_RISE_C = 1.0
_MAX_READINGS = 400  # about 100 minutes at the 15-second poll


class ChipRecovery:
    """Per miner: its last change, the hottest chip before it, and the readings since."""

    def __init__(self) -> None:
        self._last: dict[str, float] = {}  # miner id -> the latest reading (any time)
        self._changed: dict[str, float] = {}  # miner id -> time of its last change, until recovered
        self._before: dict[str, float | None] = {}  # miner id -> the reading before that change
        self._readings: dict[str, deque[tuple[float, float]]] = {}  # since the change

    def note_change(self, miner_id: str, now: float) -> None:
        """A change on the miner, sent or seen. A change while it still recovers keeps the hotter
        of the reading before the first one and the latest: a restart-cooled reading is never
        the temperature it runs at, a warmer one may be (2026-10-11: 69 °C at the first change,
        74 °C when the band stepped it down during the warm-up)."""
        last = self._last.get(miner_id)
        if miner_id in self._changed:
            before = self._before.get(miner_id)
            if last is not None and (before is None or last > before):
                self._before[miner_id] = last
        else:
            self._before[miner_id] = last
        self._changed[miner_id] = now
        self._readings[miner_id] = deque(maxlen=_MAX_READINGS)

    def observe(self, miner_id: str, temperature_c: float | None, now: float) -> None:
        if temperature_c is None:
            return
        self._last[miner_id] = temperature_c
        if miner_id in self._changed:
            self._readings[miner_id].append((now, temperature_c))
            if self._recovered(miner_id):
                self._forget(miner_id)

    def recovering(self) -> dict[str, float | None]:
        """Miners whose chips haven't recovered yet -> their reading before the change (None if
        unknown)."""
        return {miner_id: self._before.get(miner_id) for miner_id in sorted(self._changed)}

    def _recovered(self, miner_id: str) -> bool:
        readings = self._readings[miner_id]
        low = min(temp for _, temp in readings)
        low_at = next(t for t, temp in readings if temp == low)
        now, temp_now = readings[-1]
        if now - low_at < RECOVERY_WINDOW_MIN * 60:
            return False
        then = [temp for t, temp in readings if t <= now - RECOVERY_WINDOW_MIN * 60][-1]
        return temp_now - then < RECOVERY_RISE_C

    def _forget(self, miner_id: str) -> None:
        del self._changed[miner_id]
        self._before.pop(miner_id, None)
        self._readings.pop(miner_id, None)
