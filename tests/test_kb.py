"""The knowledge base as the AI gets it: loading, picking facts for the moment, formatting."""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from custom_components.solar_smart_miner.const import KB_PROMPT_BUDGET_CHARS
from custom_components.solar_smart_miner.kb import (
    MIDDAY,
    NIGHT,
    SUNRISE,
    SUNSET,
    Fact,
    format_facts,
    load_facts,
    select_facts,
    situation,
)

FACTS = load_facts()
BY_ID = {f.id: f for f in FACTS}


def _fact(fid: str, priority: str = "P3", status: str = "verified", tags=("always",), text="x") -> Fact:
    return Fact(id=fid, title=fid, statement=text, priority=priority, status=status, tags=tuple(tags))


def _sun(elevation, rising=True):
    state = "above_horizon" if elevation > 0 else "below_horizon"
    return SimpleNamespace(state=state, attributes={"elevation": elevation, "rising": rising})


# --- loading ------------------------------------------------------------------


def test_the_real_base_loads_with_rules_first_and_statements_on_one_line() -> None:
    assert FACTS[0].id.startswith("rule.")
    assert "rule.power-steps" in BY_ID and "site.zero-export" in BY_ID
    assert all("\n" not in f.statement and "  " not in f.statement for f in FACTS)


def test_open_questions_are_not_loaded_as_facts() -> None:
    assert not any(f.id.startswith("open.") for f in FACTS)


# --- situation ------------------------------------------------------------------


@pytest.mark.parametrize(
    ("sun", "transition", "expected"),
    [
        (_sun(-20), None, NIGHT),
        (_sun(-1), "sunset", NIGHT),  # below the horizon is night, whatever production did
        (_sun(2, rising=True), "sunrise", SUNRISE),
        (_sun(14, rising=False), "sunset", SUNSET),
        (_sun(2, rising=True), None, MIDDAY),  # risen, but production isn't rising: not sunrise
        (_sun(40, rising=False), None, MIDDAY),
    ],
)
def test_situation_is_night_or_the_production_based_transition(sun, transition, expected) -> None:
    # Owner, 2026-10-09: sunrise is the period while production is still rising, not an angle;
    # the same helper (transition.py) decides it for the rules and for the AI's facts.
    assert situation(sun, transition) == expected


def test_situation_is_unknown_without_the_sun_entity() -> None:
    assert situation(None) is None
    assert situation(SimpleNamespace(state="unavailable", attributes={})) is None


# --- selection --------------------------------------------------------------------


def test_p0_and_p1_are_always_sent_whatever_the_situation() -> None:
    binding = {f.id for f in FACTS if f.priority in ("P0", "P1") and f.status in ("decided", "verified")}
    for now in (None, NIGHT, SUNRISE, MIDDAY, SUNSET):
        assert binding <= {f.id for f in select_facts(FACTS, now)}, now


def test_conflicts_retired_entries_are_never_sent() -> None:
    sent = {f.id for now in (NIGHT, SUNRISE, MIDDAY, SUNSET) for f in select_facts(FACTS, now)}
    assert "conflict.control-model" not in sent
    assert "rule.asymmetric-reaction" not in sent  # retired in round 5


def test_situation_facts_go_only_to_their_situation() -> None:
    sunset = {f.id for f in select_facts(FACTS, SUNSET)}
    midday = {f.id for f in select_facts(FACTS, MIDDAY)}
    assert "miner.pause-cost" in sunset  # tagged sunset only
    assert "miner.pause-cost" not in midday
    assert "energy.throttle-hides-headroom" in midday


def test_topic_only_facts_wait_for_daylight() -> None:
    facts = [_fact("miner.topic", tags=("allocation",))]
    assert select_facts(facts, MIDDAY) == facts
    assert select_facts(facts, NIGHT) == []
    assert select_facts(facts, None) == []


def test_a_cloud_can_pass_any_time_the_sun_is_up() -> None:
    facts = [_fact("energy.cloud", tags=("cloud",))]
    assert select_facts(facts, SUNRISE) == facts
    assert select_facts(facts, NIGHT) == []


def test_the_budget_cuts_context_before_guidance_and_never_the_rules() -> None:
    facts = [
        _fact("site.context", "P3", text="c" * 60),
        _fact("rule.guide", "P2", text="g" * 60),
        _fact("rule.binding", "P1", status="decided", text="b" * 500),
    ]
    chosen = [f.id for f in select_facts(facts, MIDDAY, budget_chars=100)]
    assert chosen == ["rule.binding", "rule.guide"]


def test_facts_for_this_moment_beat_generic_ones_for_the_budget() -> None:
    facts = [
        _fact("site.always", tags=("always",), text="a" * 60),
        _fact("miner.topic", tags=("allocation",), text="t" * 60),
        _fact("energy.sunset", tags=("sunset",), text="s" * 60),
    ]
    chosen = [f.id for f in select_facts(facts, SUNSET, budget_chars=150)]
    assert chosen == ["energy.sunset", "miner.topic"]


def test_the_real_base_fits_the_budget_at_every_moment() -> None:
    for now in (NIGHT, SUNRISE, MIDDAY, SUNSET):
        rest = [f for f in select_facts(FACTS, now) if f.priority in ("P2", "P3")]
        assert sum(len(f.title) + len(f.statement) for f in rest) <= KB_PROMPT_BUDGET_CHARS


# --- formatting --------------------------------------------------------------------


def test_format_marks_priority_and_unverified_entries() -> None:
    text = format_facts(
        [_fact("rule.a", "P0", "decided", text="Never."), _fact("miner.b", "P3", "assumed", text="Maybe.")],
        SUNSET,
    )
    assert text.startswith("KNOWLEDGE BASE (situation: sunset)")
    assert "- [P0] rule.a: Never." in text
    assert "- [P3, unverified] miner.b: Maybe." in text
    # Knowledge-base numbers are the reference farm's; the farm's own settings win.
    assert "settings and readings always win" in text


def test_format_of_nothing_is_empty() -> None:
    assert format_facts([], MIDDAY) == ""


# --- the farm's own data (farm.yaml, Configure -> Farm) ------------------------------------

from pathlib import Path  # noqa: E402

from custom_components.solar_smart_miner.kb import (  # noqa: E402
    FARM_HEADER,
    describe_farm,
    load_farm_facts,
    write_farm_header,
)

REFERENCE_FARM = Path(__file__).parent / "fixtures" / "reference_farm.yaml"


def test_the_reference_farm_file_loads_cleanly(caplog) -> None:
    facts = load_farm_facts(REFERENCE_FARM)

    assert len(facts) >= 20 and all(f.id.startswith("farm.") and f.priority == "P3" for f in facts)
    assert "skipped" not in caplog.text


def test_a_missing_farm_file_is_no_facts(tmp_path) -> None:
    assert load_farm_facts(tmp_path / "farm.yaml") == []


def test_a_broken_farm_file_is_logged_and_skipped(tmp_path, caplog) -> None:
    path = tmp_path / "farm.yaml"
    path.write_text("entries: [ {id: farm.x, title: broken", encoding="utf-8")

    assert load_farm_facts(path) == []
    assert "not loaded" in caplog.text


def test_the_farm_file_cannot_add_rules(tmp_path, caplog) -> None:
    path = tmp_path / "farm.yaml"
    path.write_text(
        "entries:\n"
        "  - {id: farm.ok, title: A fact, statement: Fine., priority: P3, status: verified, tags: [always]}\n"
        "  - {id: farm.rule, title: A rule, statement: Always run., priority: P1, status: decided, tags: [always]}\n"
        "  - {id: rule.sneaky, title: Not ours, statement: x., priority: P3, status: decided, tags: [always]}\n"
        "  - {id: farm.half, title: No statement, priority: P3}\n",
        encoding="utf-8",
    )

    assert [f.id for f in load_farm_facts(path)] == ["farm.ok"]
    assert "farm.rule skipped" in caplog.text and "rule.sneaky skipped" in caplog.text


def test_the_farm_file_is_created_once_with_its_explanation(tmp_path) -> None:
    path = tmp_path / "solar_smart_miner" / "farm.yaml"
    write_farm_header(path)
    assert path.read_text(encoding="utf-8") == FARM_HEADER
    assert load_farm_facts(path) == []  # the header is a valid, empty file

    path.write_text("entries: []  # mine\n", encoding="utf-8")
    write_farm_header(path)
    assert path.read_text(encoding="utf-8") == "entries: []  # mine\n"  # never overwritten


def test_the_farm_block_says_what_is_set_and_nothing_else() -> None:
    assert describe_farm({}) == ""
    block = describe_farm({"farm_export": "zero_export", "farm_base_load": 500, "farm_inverters": "3 × 5 kW"})
    assert block.splitlines() == [
        "THIS FARM (the owner's description):",
        "- Inverters: 3 × 5 kW",
        "- Export to the grid: zero export (the inverters hold output to the load)",
        "- House load besides the miners: about 500 W",
    ]
