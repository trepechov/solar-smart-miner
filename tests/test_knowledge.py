"""The knowledge base (custom_components/solar_smart_miner/knowledge) keeps to its own rules."""
from __future__ import annotations

import re
from pathlib import Path

import pytest
import yaml

# Imported first so "custom_components" resolves to this repo, not the HA test harness's copy.
import custom_components.solar_smart_miner  # noqa: F401

KB = Path(__file__).parent.parent / "custom_components" / "solar_smart_miner" / "knowledge"
FACT_FILES = ["site.yaml", "miners.yaml", "energy.yaml", "rules.yaml", "open-questions.yaml"]

PRIORITIES = {"P0", "P1", "P2", "P3"}
STATUSES = {"decided", "verified", "assumed", "open", "conflict", "retired"}
SEVERITIES = {"info", "warning", "critical"}
CATEGORIES = {"energy", "sensor", "miner", "control", "ai", "system"}
ACTIONS = {"none", "hold", "freeze", "step_down", "pause_all"}
ACTORS = {"rules", "ai", "human"}
CHANNELS = {"log", "dashboard", "push"}
ID = re.compile(r"^[a-z]+\.[a-z0-9]+(-[a-z0-9]+)*$")
DATE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def _load(name: str) -> dict:
    return yaml.safe_load((KB / name).read_text(encoding="utf-8"))


def _facts() -> list[tuple[str, dict]]:
    return [(f, e) for f in FACT_FILES for e in _load(f)["entries"]]


FACTS = _facts()
SITUATIONS = _load("situations.yaml")["situations"]
ALERTS_FILE = _load("alerts.yaml")
ALERTS = ALERTS_FILE["alerts"]
NOT_ALERTS = ALERTS_FILE["not_alerts"]
FACT_IDS = {e["id"] for _, e in FACTS}


def _label(item) -> str:
    return item[1]["id"] if isinstance(item, tuple) else item["id"]


# --- facts ----------------------------------------------------------------------


def test_ids_are_unique_across_all_facts() -> None:
    ids = [e["id"] for _, e in FACTS]
    assert len(ids) == len(set(ids)), sorted({i for i in ids if ids.count(i) > 1})


@pytest.mark.parametrize("item", FACTS, ids=_label)
def test_every_fact_has_the_required_fields_in_the_right_shape(item) -> None:
    _, e = item
    for field in ("id", "title", "statement", "priority", "status", "source", "date", "tags"):
        assert e.get(field), f"{e['id']}: missing {field}"
    assert ID.match(e["id"]), e["id"]
    assert e["priority"] in PRIORITIES
    assert e["status"] in STATUSES
    assert DATE.match(e["date"]), f"{e['id']}: date must be a quoted YYYY-MM-DD string"
    assert isinstance(e["tags"], list) and all(isinstance(t, str) and t for t in e["tags"])
    assert len(e["statement"].strip()) <= 700, f"{e['id']}: one fact per entry, keep it short"
    assert set(e) <= {
        "id", "title", "statement", "priority", "status", "source", "date",
        "tags", "conflicts_with", "situations", "note", "stage",
    }, f"{e['id']}: unknown field"


@pytest.mark.parametrize("item", FACTS, ids=_label)
def test_binding_entries_are_not_guesses(item) -> None:
    """P0 and P1 are what the AI's answer is checked against: no unverified claims.

    A retired entry is never sent or checked against, so it keeps its old priority.
    """
    _, e = item
    if e["status"] == "retired":
        return
    if e["priority"] == "P0":
        assert e["status"] in {"decided", "verified"}, e["id"]
    if e["priority"] in {"P0", "P1"}:
        assert e["status"] in {"decided", "verified", "conflict"}, e["id"]


def test_open_questions_live_in_their_own_file_only() -> None:
    for name, e in FACTS:
        assert (e["status"] == "open") == (name == "open-questions.yaml"), e["id"]


def test_conflicts_name_entries_that_exist() -> None:
    for _, e in FACTS:
        if e["status"] == "conflict":
            refs = e.get("conflicts_with") or []
            assert len(refs) >= 2, f"{e['id']}: a conflict names the entries that disagree"
            assert set(refs) <= FACT_IDS, f"{e['id']}: {set(refs) - FACT_IDS}"
        else:
            assert "conflicts_with" not in e, e["id"]


def test_every_priority_and_the_situations_we_care_about_are_covered() -> None:
    assert {e["priority"] for _, e in FACTS} == PRIORITIES
    tags = {t for _, e in FACTS for t in e["tags"]}
    assert {"always", "sunrise", "sunset", "midday", "cloud"} <= tags


def test_the_agreed_decisions_are_in_the_base() -> None:
    """Spot-check that the things decided in the sessions didn't get lost in an edit."""
    by_id = {e["id"]: e for _, e in FACTS}
    assert by_id["rule.power-steps"]["priority"] == "P1"
    assert "configured power steps" in by_id["rule.power-steps"]["statement"]
    assert "900 to 2,500 W in 200 W steps" in by_id["rule.power-steps"]["note"]
    assert by_id["rule.guard-above-ai"]["priority"] == "P0"
    assert by_id["rule.one-controller"]["priority"] == "P0"
    assert by_id["rule.temperature-is-braiins"]["priority"] == "P0"
    # Requirements doc rounds 1 to 5 (2026-10-05), moved into the base on 2026-10-06.
    for rule_id in (
        "rule.goal",
        "rule.ai-is-advisor",
        "rule.down-slowly-up-promptly",
        "rule.temperature-band",
        "rule.transition-by-agreement",
        "rule.sunset-one-by-one",
    ):
        assert by_id[rule_id]["status"] == "decided", rule_id
    # Merged into the ramp lock in 0.8.0 (one wait after a change): kept for the record.
    assert by_id["rule.temperature-after-change"]["status"] == "retired"
    assert "rule.ramp-lock" in by_id["rule.temperature-after-change"]["note"]
    # Round 5 reversed "down quickly, up slowly"; the old rule must not be sent as a fact.
    assert by_id["rule.asymmetric-reaction"]["status"] == "retired"
    # Round 6 dropped Full power as a profile.
    assert by_id["rule.full-power"]["status"] == "retired"


# --- alerts ---------------------------------------------------------------------


def test_alert_ids_are_unique() -> None:
    ids = [a["id"] for a in ALERTS] + [n["id"] for n in NOT_ALERTS]
    assert len(ids) == len(set(ids))


@pytest.mark.parametrize("alert", ALERTS, ids=_label)
def test_every_alert_is_complete_and_valid(alert) -> None:
    for field in (
        "id", "title", "severity", "category", "condition", "normal", "not_alert",
        "thresholds", "action", "acts", "notify", "cooldown_min", "resolves_when",
    ):
        assert field in alert, f"{alert['id']}: missing {field}"
    assert ID.match(alert["id"]), alert["id"]
    assert alert["severity"] in SEVERITIES
    assert alert["category"] in CATEGORIES
    assert alert["id"].split(".")[0] == alert["category"], "the id starts with its category"
    assert alert["action"] in ACTIONS
    assert alert["acts"] in ACTORS
    assert alert["notify"] and set(alert["notify"]) <= CHANNELS
    assert isinstance(alert["thresholds"], dict)
    assert isinstance(alert["cooldown_min"], int) and alert["cooldown_min"] >= 0


@pytest.mark.parametrize("alert", ALERTS, ids=_label)
def test_severity_decides_the_route(alert) -> None:
    if alert["severity"] == "critical":
        assert "push" in alert["notify"], alert["id"]
        assert alert["action"] != "none", f"{alert['id']}: a critical alert needs a safe action"
    if alert["severity"] == "info":
        assert "push" not in alert["notify"], alert["id"]


@pytest.mark.parametrize("alert", ALERTS, ids=_label)
def test_alerts_only_point_at_things_that_exist(alert) -> None:
    not_alert_ids = {n["id"] for n in NOT_ALERTS}
    assert set(alert["not_alert"]) <= not_alert_ids, alert["id"]
    assert set(alert.get("related") or []) <= FACT_IDS, (
        alert["id"], set(alert.get("related") or []) - FACT_IDS
    )


def test_normal_look_alikes_point_at_facts_that_exist() -> None:
    for n in NOT_ALERTS:
        assert n["what"] and n["why"], n["id"]
        assert set(n.get("related") or []) <= FACT_IDS, n["id"]


def test_the_scenario_that_actually_happened_is_covered() -> None:
    """2026-10-05: the miners ran after sunset and imported for about 45 minutes."""
    by_id = {a["id"]: a for a in ALERTS}
    assert by_id["energy.night-consumption"]["severity"] == "critical"
    assert by_id["energy.night-consumption"]["action"] == "pause_all"
    assert by_id["energy.sustained-import"]["action"] == "step_down"
    assert "normal.transition-import" in by_id["energy.sustained-import"]["not_alert"]


def test_every_category_has_at_least_one_alert() -> None:
    assert {a["category"] for a in ALERTS} == CATEGORIES


# --- the readme -----------------------------------------------------------------


def test_the_readme_lists_every_file() -> None:
    readme = (KB / "README.md").read_text(encoding="utf-8")
    for name in [*FACT_FILES, "alerts.yaml", "situations.yaml"]:
        assert f"`{name}`" in readme, name


def test_there_are_no_stray_files() -> None:
    expected = {"README.md", "alerts.yaml", "situations.yaml", *FACT_FILES}
    assert {p.name for p in KB.iterdir()} == expected


# --- situations -------------------------------------------------------------------

KINDS = {"measured", "forecast", "time", "derived"}
WEIGHTS = {"strong", "medium", "weak"}
SITUATION_STATUSES = {"proposed", "in_use", "retired"}
FACT_AREAS = {"site", "miner", "energy", "rule", "conflict", "open", "situation"}
SITUATION_IDS = {x["id"] for x in SITUATIONS}


def _signals(situation) -> list[tuple[str, dict]]:
    return [("for", x) for x in situation["for"]] + [("against", x) for x in situation["against"]]


def test_situation_ids_and_tags_are_unique_and_match() -> None:
    assert len(SITUATION_IDS) == len(SITUATIONS)
    assert len({x["tag"] for x in SITUATIONS}) == len(SITUATIONS)
    for x in SITUATIONS:
        assert x["id"] == f"situation.{x['tag']}", x["id"]


@pytest.mark.parametrize("situation", SITUATIONS, ids=_label)
def test_every_situation_is_complete_and_valid(situation) -> None:
    for field in ("id", "name", "tag", "scope", "summary", "expected", "for", "against",
                  "exceptions", "in_code", "status"):
        assert situation.get(field), f"{situation['id']}: missing {field}"
    assert situation["scope"] in {"site", "miner"}
    assert situation["status"] in SITUATION_STATUSES
    assert str(situation["in_code"]).split()[0].strip(":") in {"none", "partial", "full"}, (
        f"{situation['id']}: in_code starts with none, partial or full"
    )


@pytest.mark.parametrize("situation", SITUATIONS, ids=_label)
def test_signals_are_well_formed_and_ids_unique_within_a_situation(situation) -> None:
    ids = []
    for side, sig in _signals(situation):
        for field in ("id", "says", "kind", "weight", "available"):
            assert field in sig, f"{situation['id']}/{side}: signal missing {field}"
        assert sig["kind"] in KINDS, (situation["id"], sig["id"])
        assert sig["weight"] in WEIGHTS, (situation["id"], sig["id"])
        assert isinstance(sig["available"], bool), (situation["id"], sig["id"])
        assert isinstance(sig.get("uses", []), list)
        ids.append(sig["id"])
    assert len(ids) == len(set(ids)), f"{situation['id']}: duplicate signal ids"


@pytest.mark.parametrize("situation", SITUATIONS, ids=_label)
def test_a_situation_needs_evidence_of_more_than_one_kind(situation) -> None:
    """'Likely' needs two kinds of signal to agree, so there have to be two kinds to agree."""
    kinds = {sig["kind"] for sig in situation["for"]}
    assert len(kinds) >= 2, f"{situation['id']}: only {kinds}"
    assert len(situation["for"]) >= 3 and situation["against"], situation["id"]
    assert any(sig["weight"] != "weak" for sig in situation["for"]), situation["id"]


@pytest.mark.parametrize("situation", SITUATIONS, ids=_label)
def test_the_clock_alone_is_never_strong_evidence(situation) -> None:
    for _, sig in _signals(situation):
        if sig["kind"] == "time":
            assert sig["weight"] in {"weak", "medium"}, (situation["id"], sig["id"])


@pytest.mark.parametrize("situation", SITUATIONS, ids=_label)
def test_situation_signals_only_use_things_that_exist(situation) -> None:
    assert "effects" not in situation, "name the situation on the rule instead (situations: [...])"
    for _, sig in _signals(situation):
        for used in sig.get("uses") or []:
            area = used.split(".")[0]
            if area in FACT_AREAS:  # otherwise it is a Home Assistant entity id
                assert used in FACT_IDS | SITUATION_IDS, (situation["id"], sig["id"], used)


def _referrers() -> list[tuple[str, list[str]]]:
    return [(e["id"], e.get("situations") or []) for _, e in FACTS] + [
        (a["id"], a.get("situations") or []) for a in ALERTS
    ]


def test_everything_that_names_a_situation_names_one_that_exists() -> None:
    for owner, names in _referrers():
        assert set(names) <= SITUATION_IDS, f"{owner}: {set(names) - SITUATION_IDS}"


def test_every_active_situation_is_used_by_a_rule_fact_or_alert() -> None:
    """A situation nothing refers to does nothing; retire it or use it."""
    named = {n for _, names in _referrers() for n in names}
    for situation in SITUATIONS:
        if situation["status"] != "retired":
            assert situation["id"] in named, f"{situation['id']}: no rule, fact or alert names it"


def test_decisions_can_refer_to_the_miner_situation_tuning() -> None:
    by_id = {e["id"]: e for _, e in FACTS}
    assert "situation.tuning" in by_id["rule.no-step-up-while-tuning"]["situations"]
    assert "situation.tuning" in by_id["miner.tuning-signature"]["situations"]


def test_tuning_matches_what_was_measured_on_brod1_and_brod2() -> None:
    """Power flat at the limit; hashrate climbing steadily; J/TH high and falling. Not noisy."""
    tuning = next(x for x in SITUATIONS if x["tag"] == "tuning")
    signals = {sig["id"]: sig for sig in tuning["for"]}
    assert signals["efficiency-poor"]["weight"] == "strong"
    assert signals["hashrate-climbing"]["weight"] == "strong"
    assert signals["power-flat-at-limit"]["weight"] == "weak"  # a tuned miner looks the same
    assert "hashrate-unsteady" not in signals  # contradicted by the measurements
    against = {sig["id"] for sig in tuning["against"]}
    assert {"efficiency-settled", "hashrate-at-settled", "long-since-change"} <= against
    assert "dips" in tuning["exceptions"]  # settled miners dip briefly; don't read it as tuning


def test_the_tuning_measurements_are_recorded_as_verified_facts() -> None:
    by_id = {e["id"]: e for _, e in FACTS}
    for fact_id in ("miner.tuning-duration", "miner.settled-values", "miner.hashrate-dips",
                    "miner.limit-change-cost"):
        assert by_id[fact_id]["status"] == "verified", fact_id
        assert "situation.tuning" in by_id[fact_id]["situations"], fact_id
        assert by_id[fact_id]["date"] == "2026-10-06"


def test_every_situation_tag_is_used_by_some_fact() -> None:
    """A situation nothing is tagged with does nothing; retire it or tag the facts."""
    used = {t for _, e in FACTS for t in e["tags"]}
    for situation in SITUATIONS:
        if situation["status"] != "retired":
            assert situation["tag"] in used, f"{situation['id']}: no fact is tagged {situation['tag']}"


def test_the_situations_we_discussed_exist_and_tuning_is_honest_about_the_code() -> None:
    by_tag = {x["tag"]: x for x in SITUATIONS}
    assert {"tuning", "sunrise", "midday", "sunset", "night", "cloud", "curtailed", "dropout"} <= set(by_tag)
    assert by_tag["tuning"]["scope"] == "miner"
    assert by_tag["tuning"]["in_code"].startswith("partial")  # one signal of five is implemented
    assert by_tag["tuning"]["status"] == "in_use"


def test_rules_sent_to_the_ai_carry_no_reference_farm_numbers() -> None:
    """The farm the base was measured on is an example: its numbers live in notes.

    A P0 or P1 rule is sent on every request, so a wattage, voltage or miner name in its
    statement would reach every other farm's AI as a hard rule (review 2026-10-07).
    """
    import re

    farm_specific = re.compile(r"\d[\d,]* ?(W|V)\b|Brod\d|Brodilovo")
    for _, e in FACTS:
        if e["id"].startswith("rule.") and e["priority"] in ("P0", "P1") and e["status"] == "decided":
            assert not farm_specific.search(e["statement"]), f"{e['id']}: {e['statement']}"


# --- rules and the code: groups ---------------------------------------------------


STAGES = {"safety", "pacing", "limits", "target", "allocation", "tidy", "control", "advice"}
ACTIVE_RULES = [e for f, e in FACTS if f == "rules.yaml" and e["status"] not in ("retired", "open", "conflict")]


@pytest.mark.parametrize("rule", ACTIVE_RULES, ids=lambda e: e["id"])
def test_every_active_rule_has_a_stage(rule) -> None:
    # Two axes: priority says how binding a rule is, the stage and its place what it overrides.
    assert rule.get("stage") in STAGES, rule["id"]


def test_the_decisions_rule_list_and_the_knowledge_base_agree() -> None:
    from custom_components.solar_smart_miner.decision.rules import RULES, STAGE_OF

    by_id = {e["id"]: e for e in ACTIVE_RULES}
    for rule, stage in STAGE_OF.items():
        assert rule in by_id, f"{rule} is in the code but not an active rule"
        assert by_id[rule]["stage"] == stage, rule
    pipeline = {e["id"] for e in ACTIVE_RULES if e["stage"] in RULES}
    assert pipeline == set(STAGE_OF), "a rule in a decision group is missing from decision/rules.py"


def test_fewer_rules_than_before_the_audit() -> None:
    # 2026-10-09 (U9): 33 active rules before the audit. A new rule should replace one.
    assert len(ACTIVE_RULES) <= 22
