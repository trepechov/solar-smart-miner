"""The rules the decision enforces, by group, in the order they run (CLAUDE.md, "Fewer Rules").

An earlier group always wins, and a later one never undoes it. Each rule is named by its
knowledge-base id, and the entry's `stage` names the same group (tests/test_knowledge.py checks
both ways). Reviewing a rule's priority means moving it here, replaying the farm's moments and
releasing; it is never a setting.
"""
from __future__ import annotations

RULES: dict[str, tuple[str, ...]] = {
    "safety": (
        "rule.miner-range",
        "rule.battery-floor",
        "rule.sustained-low-voltage",
        "rule.required-inputs",
    ),
    "pacing": ("rule.ramp-lock", "rule.one-miner-per-change"),
    "limits": (
        "rule.sunset-one-by-one",
        "rule.transition-by-agreement",
        "rule.temperature-band",
        "rule.temperature-is-braiins",
    ),
    "target": (
        "rule.small-import-target",
        "rule.down-slowly-up-promptly",
        "rule.forecast-reference-only",
    ),
    "allocation": ("rule.step-down-allocation", "rule.stop-below-lowest-step"),
    "tidy": ("rule.power-steps",),
}
STAGE_OF = {rule: stage for stage, rules in RULES.items() for rule in rules}
# Plan reasons of the Safety and Limits rules that bring a miner down. A miner brought down by
# one isn't raised or started again within the step-down delay (rule.step-down-allocation):
# otherwise its condition clears once it has acted and the target undoes it the next cycle.
HELD_DOWN_REASONS = ("low voltage", "too warm", "battery low")


def decided_by(rule: str) -> str:
    """The trace line that names the group and rule a decision came from."""
    return f"Decided by: {STAGE_OF[rule].title()} / {rule}"
