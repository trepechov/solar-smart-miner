"""The knowledge base (knowledge/*.yaml) and the farm's own data, as the AI is given them.

Two layers: the rules and principles ship with the integration (knowledge/); what is true of
one farm is the user's: a few fields in Configure -> Farm (describe_farm, always sent) and
measurements and notes in <config>/solar_smart_miner/farm.yaml (load_farm_facts, P3 only,
picked by situation like the shipped P3 entries). Both survive updates.

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

from .const import (
    CONF_FARM_BASE_LOAD,
    CONF_FARM_BATTERY,
    CONF_FARM_COOLING,
    CONF_FARM_CUTOFF,
    CONF_FARM_EXPORT,
    CONF_FARM_INVERTERS,
    CONF_FARM_MINER_MODEL,
    CONF_FARM_NOTES,
    CONF_FARM_PV_ARRAY,
    KB_PROMPT_BUDGET_CHARS,
)

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


def _fact(e: dict) -> Fact:
    return Fact(
        id=e["id"],
        title=e["title"],
        statement=" ".join(str(e["statement"]).split()),
        priority=e["priority"],
        status=e["status"],
        tags=tuple(e.get("tags") or ()),
    )


def load_facts(directory: Path = KB_DIR) -> list[Fact]:
    """Every entry of the fact files, in file order. Blocking: run it in an executor."""
    facts: list[Fact] = []
    for name in FACT_FILES:
        data = yaml.safe_load((directory / name).read_text(encoding="utf-8")) or {}
        facts.extend(_fact(e) for e in data.get("entries") or [])
    return facts


FARM_HEADER = """\
# Facts about this farm, for the AI: what it is, what was measured on it, notes.
# Same format as the integration's knowledge base (see its knowledge/README.md), but P3 only:
# context for reasoning, never rules (a P0 to P2 entry here is skipped). Ids start with "farm.".
# Edit freely; reload the integration to apply. Example entry:
#
#   - id: farm.meter-dropouts
#     title: Grid meter gaps
#     statement: The grid meter drops out for under a minute a few times an evening.
#     priority: P3
#     status: verified
#     source: "measured: 2026-10-05"
#     date: "2026-10-05"
#     tags: [always]
entries: []
"""


def load_farm_facts(path: Path) -> list[Fact]:
    """The farm's own facts from farm.yaml (blocking). A missing file is no facts; a broken
    file or entry is logged and skipped, never an error: the integration works without it."""
    if not path.exists():
        return []
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        entries = data.get("entries") or []
        if not isinstance(entries, list):
            raise TypeError("entries is not a list")
    except (OSError, ValueError, TypeError, yaml.YAMLError, AttributeError) as err:
        _LOGGER.warning("Farm file %s not loaded, the AI gets no farm facts from it: %s", path, err)
        return []
    facts: list[Fact] = []
    for e in entries:
        try:
            fact = _fact(e)
        except (KeyError, TypeError, AttributeError):
            _LOGGER.warning("Farm file %s: an entry is missing a field, skipped: %s", path, e)
            continue
        if fact.priority != "P3" or not str(fact.id).startswith("farm."):
            # The user's file can't add rules that would sit next to the code's.
            _LOGGER.warning("Farm file %s: %s skipped (only P3 entries with farm. ids)", path, fact.id)
            continue
        facts.append(fact)
    return facts


def write_farm_header(path: Path) -> None:
    """Create farm.yaml with its explanation on first setup (blocking); never overwrite it."""
    if not path.exists():
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(FARM_HEADER, encoding="utf-8")


_FARM_LINES = (
    (CONF_FARM_INVERTERS, "Inverters: {}"),
    (CONF_FARM_EXPORT, "Export to the grid: {}"),
    (CONF_FARM_BATTERY, "Battery: {}"),
    (CONF_FARM_PV_ARRAY, "PV array: {}"),
    (CONF_FARM_COOLING, "Cooling: {}"),
    (CONF_FARM_MINER_MODEL, "Miners: {}"),
    (CONF_FARM_CUTOFF, "The miners' own temperature cutoff: {:.0f} deg C"),
    (CONF_FARM_BASE_LOAD, "House load besides the miners: about {:.0f} W"),
    (CONF_FARM_NOTES, "Notes: {}"),
)
_WORDS = {"zero_export": "zero export (the inverters hold output to the load)",
          "export_allowed": "allowed", "none": "none", "present": "present"}


def describe_farm(options) -> str:
    """The "This farm" block of the system prompt, from Configure -> Farm (empty if unset)."""
    lines = []
    for key, text in _FARM_LINES:
        value = options.get(key)
        if value in (None, ""):
            continue
        lines.append("- " + text.format(_WORDS.get(value, value) if isinstance(value, str) else value))
    return "THIS FARM (the owner's description):\n" + "\n".join(lines) if lines else ""


def situation(sun_state, transition: str | None = None) -> str | None:
    """Night, sunrise, midday or sunset (None without Home Assistant's sun.sun).

    Night when the sun is below the horizon; otherwise sunrise and sunset come from the
    production-based helper (transition.py, the same one the decision uses), else midday.
    """
    if sun_state is None or sun_state.state not in ("above_horizon", "below_horizon"):
        return None
    if sun_state.state == "below_horizon":
        return NIGHT
    if transition == SUNRISE:
        return SUNRISE
    if transition == SUNSET:
        return SUNSET
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
        "Numbers here are measured on a reference farm and show how to reason; this farm's "
        "settings and readings always win over them. "
        "Where the rule-based proposal goes against a P0 or P1 here, say so."
    ]
    for f in facts:
        mark = f"{f.priority}, unverified" if f.status == "assumed" else f.priority
        lines.append(f"- [{mark}] {f.title}: {f.statement}")
    return "\n".join(lines)
