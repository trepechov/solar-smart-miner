"""The knowledge base (knowledge/*.yaml) as the AI is given it.

The facts are loaded once, off the event loop, and each AI request gets the ones that fit
the moment, following knowledge/README.md: every P0 and P1 rule always, then P2 and P3
entries for the current situation within a size budget. Unverified entries are marked as
such, and open questions, conflicts and retired entries are never sent as facts.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path

import yaml

from .const import KB_PROMPT_BUDGET_CHARS, KB_TRANSITION_ELEVATION, KB_NIGHT_ELEVATION

_LOGGER = logging.getLogger(__name__)

KB_DIR = Path(__file__).parent / "knowledge"
FACT_FILES = ("rules.yaml", "site.yaml", "miners.yaml", "energy.yaml")  # rules first
SENDABLE = {"decided", "verified", "assumed"}

NIGHT = "night"
SUNRISE = "sunrise"
SUNSET = "sunset"
MIDDAY = "midday"
SITUATION_TAGS = {NIGHT, SUNRISE, SUNSET, MIDDAY, "cloud"}
# Tags that count as "now" in each situation. A cloud can pass any time the sun is up.
_MATCHING = {
    NIGHT: {NIGHT},
    SUNRISE: {SUNRISE, "cloud"},
    MIDDAY: {MIDDAY, "cloud"},
    SUNSET: {SUNSET, "cloud"},
}


@dataclass(frozen=True)
class Fact:
    id: str
    title: str
    statement: str
    priority: str
    status: str
    tags: tuple[str, ...]


def load_facts(directory: Path = KB_DIR) -> list[Fact]:
    """Every entry of the fact files, in file order. Blocking: run it in an executor."""
    facts: list[Fact] = []
    for name in FACT_FILES:
        data = yaml.safe_load((directory / name).read_text(encoding="utf-8")) or {}
        for e in data.get("entries") or []:
            facts.append(
                Fact(
                    id=e["id"],
                    title=e["title"],
                    statement=" ".join(str(e["statement"]).split()),
                    priority=e["priority"],
                    status=e["status"],
                    tags=tuple(e.get("tags") or ()),
                )
            )
    return facts


def situation(sun_state) -> str | None:
    """Night, sunrise, midday or sunset from Home Assistant's sun.sun (None if missing).

    This only picks which facts to send. The controller's own sunrise and sunset modes
    need several signals to agree (rule.transition-by-agreement).
    """
    if sun_state is None:
        return None
    try:
        elevation = float(sun_state.attributes["elevation"])
    except (KeyError, TypeError, ValueError):
        return None
    if elevation <= KB_NIGHT_ELEVATION:
        return NIGHT
    if elevation < KB_TRANSITION_ELEVATION:
        return SUNRISE if sun_state.attributes.get("rising") else SUNSET
    return MIDDAY


def _fits(fact: Fact, now: str | None) -> bool:
    if "always" in fact.tags:
        return True
    if now is None:
        return False
    moments = SITUATION_TAGS.intersection(fact.tags)
    if moments:
        return bool(moments & _MATCHING[now])
    # Topic-only facts (allocation, temperature, ...) matter whenever the sun is up.
    return now != NIGHT


def _rank(fact: Fact, now: str | None) -> int:
    """0 = tagged for this moment, 1 = a topic, 2 = always: the most specific goes first."""
    if now is not None and SITUATION_TAGS.intersection(fact.tags) & _MATCHING[now]:
        return 0
    return 2 if "always" in fact.tags else 1


def select_facts(
    facts: list[Fact], now: str | None, budget_chars: int = KB_PROMPT_BUDGET_CHARS
) -> list[Fact]:
    """P0 and P1 always; then P2 before P3 for this situation until the budget is used.

    Within a priority, facts tagged for this moment come before topic facts, and those
    before facts that are always true, so a tight budget drops the generic ones first.
    """
    sendable = [f for f in facts if f.status in SENDABLE]
    chosen = [f for f in sendable if f.priority in ("P0", "P1")]
    used = 0
    for priority in ("P2", "P3"):
        ranked = sorted(
            (f for f in sendable if f.priority == priority and _fits(f, now)),
            key=lambda f: _rank(f, now),
        )
        for f in ranked:
            size = len(f.title) + len(f.statement)
            if used + size > budget_chars:
                continue
            chosen.append(f)
            used += size
    return chosen


def format_facts(facts: list[Fact], now: str | None) -> str:
    """The knowledge section of the system prompt (empty when there is nothing to send)."""
    if not facts:
        return ""
    lines = [
        f"KNOWLEDGE BASE (situation: {now or 'unknown'}). "
        "P0 = hard limit, never broken. P1 = operating rule, followed unless a P0 says "
        "otherwise. P2 = guidance you may depart from with a reason, given in the note. "
        "P3 = context for reasoning. Entries marked unverified are believed, not measured. "
        "Where the rule-based proposal goes against a P0 or P1 here, say so."
    ]
    for f in facts:
        mark = f"{f.priority}, unverified" if f.status == "assumed" else f.priority
        lines.append(f"- [{mark}] {f.title}: {f.statement}")
    return "\n".join(lines)
