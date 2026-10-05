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
        "tags", "conflicts_with", "note",
    }, f"{e['id']}: unknown field"


@pytest.mark.parametrize("item", FACTS, ids=_label)
def test_binding_entries_are_not_guesses(item) -> None:
    """P0 and P1 are what the AI's answer is checked against: no unverified claims."""
    _, e = item
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
    assert "900, 1100, 1300 and 1500" in by_id["rule.power-steps"]["statement"]
    assert by_id["rule.guard-above-ai"]["priority"] == "P0"
    assert by_id["rule.one-controller"]["priority"] == "P0"
    assert by_id["rule.temperature-is-braiins"]["priority"] == "P0"


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
    for name in [*FACT_FILES, "alerts.yaml"]:
        assert f"`{name}`" in readme, name


def test_there_are_no_stray_files() -> None:
    expected = {"README.md", "alerts.yaml", *FACT_FILES}
    assert {p.name for p in KB.iterdir()} == expected
