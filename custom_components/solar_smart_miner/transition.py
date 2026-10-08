"""Sunrise and sunset as periods of changing production, not sun angles (owner, 2026-10-09).

One definition for the decision (the sunrise step-down delay, nothing starting at sunset) and
for the knowledge base (which facts the AI gets).

On a zero-export site the meter can't show production directly: the inverters hold their output
to the load. What the import implies about it can be used while there is some import (the
inverters aren't throttling): production = what the miners draw + the house − the import. The
house is unknown but steady, so `miners' draw + grid balance` (export positive) moves with
production and our own changes cancel out of it. It is sampled only outside ramp locks, into a
window of TRANSITION_WINDOW_MIN.

- Sunrise: the sun is rising (`sun.sun`) and production rose by more than
  TRANSITION_CHANGE_W over the window. It ends when production stops rising.
- Sunset: within SUNSET_GATE_MIN before `sun.sun`'s next setting, production fell by more than
  TRANSITION_CHANGE_W over the window. It then stays on until the sun is rising again (the next
  morning), so the stop it causes can't switch it off (the 2026-10-08 stop/start loop).

`sun.sun` only opens the window; production decides. The forecast is never used
(rule.forecast-reference-only).
"""
from __future__ import annotations

from collections import deque

from .const import (
    SUNSET_GATE_MIN,
    TRANSITION_CHANGE_W,
    TRANSITION_MIN_SPAN_MIN,
    TRANSITION_WINDOW_MIN,
)


class Transition:
    def __init__(self) -> None:
        self._samples: deque[tuple[float, float]] = deque()  # (time.monotonic(), production W)
        self.sunrise = False
        self.sunset = False  # latched until the sun rises again

    def change_w(self) -> float | None:
        """Production at the end of the window minus at its start (means of the first and last
        third), or None while the window covers less than TRANSITION_MIN_SPAN_MIN."""
        if len(self._samples) < 3:
            return None
        start, end = self._samples[0][0], self._samples[-1][0]
        if end - start < TRANSITION_MIN_SPAN_MIN * 60:
            return None
        third = (end - start) / 3
        first = [v for t, v in self._samples if t <= start + third]
        last = [v for t, v in self._samples if t >= end - third]
        return sum(last) / len(last) - sum(first) / len(first)

    def update(
        self,
        now: float,
        production_w: float | None,
        *,
        sun_up: bool | None,
        sun_rising: bool | None,
        minutes_to_setting: float | None,
    ) -> None:
        """One poll at `now` (time.monotonic()). `production_w` is None when it can't be read (a
        ramp lock, no import); `minutes_to_setting` until sun.sun's next setting."""
        if production_w is not None:
            self._samples.append((now, production_w))
        while self._samples and now - self._samples[0][0] > TRANSITION_WINDOW_MIN * 60:
            self._samples.popleft()
        change = self.change_w()
        if sun_rising:
            self.sunset = False  # a new day
        self.sunrise = bool(sun_rising and sun_up and change is not None and change > TRANSITION_CHANGE_W)
        near_setting = (
            minutes_to_setting is not None
            and sun_up is not False
            and minutes_to_setting <= SUNSET_GATE_MIN
        )
        if near_setting and change is not None and change < -TRANSITION_CHANGE_W:
            self.sunset = True

    @property
    def name(self) -> str | None:
        """"sunrise", "sunset" or None (neither)."""
        return "sunrise" if self.sunrise else "sunset" if self.sunset else None
