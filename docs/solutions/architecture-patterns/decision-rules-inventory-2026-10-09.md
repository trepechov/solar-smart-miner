---
title: "Decision rules inventory: every rule, its group, code, test and origin"
date: 2026-10-09
category: architecture-patterns
module: solar_smart_miner / decision + control + transition
problem_type: architecture_pattern
component: decision
severity: high
applies_when:
  - Adding, changing or removing a decision rule
  - A farm log shows two rules undoing each other (a reversal, a flip-flop)
  - Reviewing which rule wins when two disagree
related_components:
  - decision
  - control
  - coordinator
  - knowledge
tags:
  - fewer-rules
  - precedence
  - replay
---

# Decision rules inventory

Kept up to date with the code (CLAUDE.md, "Fewer Rules"). Made in U9 of
`docs/plans/2026-10-09-001-refactor-decision-pipeline-plan.md`; the order below is `decision/rules.py`.
An earlier group always wins. "Decided by: <group> / <rule>" ends every decision trace.

## The rules, in the order they run

| # | Group | Rule (knowledge id) | Code | Tests | Added by |
|---|---|---|---|---|---|
| 1 | Safety | `rule.miner-range`: only the miner's own power steps | `allocation._ladder`; `control._limit_refusal` refuses, never clamps | `test_decision` (ranges), `test_control` (off-ladder refused) | requirements §3.1 |
| 2 | Safety | `rule.required-inputs`: the grid import is required; a meter gap holds | `safety.check` (solar fault), `profiles.target` (balance unknown) | `test_decision` (sensor fault, grid unknown) | 10-05 dropouts; round 7 |
| 3 | Safety | `rule.battery-floor`: below the floor every miner stops | `safety.check` | `test_decision` (battery floor) | first version (entry added in U9) |
| 4 | Pacing | `rule.ramp-lock`: every miner holds until the changed one has settled | `pacing.check`; `control.Settling`; `coordinator._minutes_since_change` | `test_decision`, `test_control` (settling), `test_coordinator`, `test_farm_sim` | 10-07 16:02 bundle; fc69adc, 9b0b2c4, S11 |
| 5 | Pacing | `rule.one-miner-per-change`: one change per proposal, a cut may skip steps | `allocation._one_change` returns one miner | `test_decision` (never more than one) | 10-07 16:02 |
| 6 | Limits | `rule.sunset-one-by-one`: sunset or sun down, nothing starts or steps up | `limits.check` (`may_step_up`) | `test_decision` (sunset), `test_farm_sim` (10-08 evening) | 10-08 sunset loop (overlap 8) |
| 7 | Limits | `rule.transition-by-agreement`: sunrise / sunset from production | `transition.py`; fed by `coordinator._update_transition` | `test_transition`, `test_coordinator`, `test_kb` | overlap 2 |
| 8 | Limits | `rule.temperature-band`: cap in the band, one step down above it | `limits.check` | `test_decision` (temperature) | requirements §3.2 |
| 9 | Limits | `rule.temperature-is-braiins`: too warm at the lowest step is left to the miner | `limits.check` | `test_decision` (lowest step) | requirements §0.3 |
| 10 | Target | `rule.small-import-target`: below the range up, above it down, inside hold | `profiles.target` | `test_decision` (Solar-follow section) | 05b2b27, 8003b62; overlap 1 |
| 11 | Target | `rule.down-slowly-up-promptly`: down only after the delay (longer at sunrise) | `profiles.target`; `coordinator._minutes_import_high` | `test_decision`, `test_coordinator` | 8003b62 |
| 12 | Target | `rule.forecast-reference-only`: the forecast never decides | (nothing reads it) | `test_decision` (forecast never changes the proposal) | 55e5f48 |
| 13 | Allocation | `rule.step-down-allocation`: which miner moves (cut, increment, even load) | `allocation._one_change` | `test_decision` | 0955d09 (even load) |
| 14 | Allocation | `rule.stop-below-lowest-step`: stop, don't idle | `allocation.allocate`, `context.stop` | `test_decision` (stop / start) | requirements §5.3 |
| 15 | Tidy | `rule.power-steps`: a limit off the steps moves to the nearest one | `allocation._one_change` (off step) | `test_decision` (off the ladder) | owner, 10-05 |

Outside the decision: `rule.guard-above-ai`, `rule.one-controller`, `rule.automatic-mode`, `rule.ai-is-advisor`
(stage `control`, enforced in `control.py`); `rule.goal`, `rule.no-import-cap`, `rule.sustained-low-voltage`
(stage `advice` until the voltage unit lands).

## What U9 removed or merged

| Before | After | Why |
|---|---|---|
| Four profiles, three of them unused | Solar-follow only (U2) | Owner, 10-09; three branches fewer |
| Two steering paths: the budget (with a 150 W tolerance and a 100 W margin) and the measured import | The measured import alone | Overlap 1; every fix since 404ee71 touched one or the other |
| Two "sunrise" definitions: `sun.sun` rising until noon (decision), elevation under 15° (AI facts) | One production-based helper, `transition.py` | Overlap 2; a cloud at 11:00 waited the 30-minute morning delay |
| Ramp lock, tuning window, step-down delay after a change, restart-not-stopped override, echo-based "ok" | One settling record per miner (`control.Settling`) + the step-down delay | Overlaps 3 and 4, S11 |
| No sunset rule; "sun down blocks starts" | `rule.sunset-one-by-one` (merged with `rule.stop-trigger-below-start`) | Overlap 8 |
| 33 active knowledge-base rules | 22, each with a stage | Merges listed in each retired entry's note |

## Collisions in the farm's logs (2026-10-06 to 10-09)

From `scripts/replay.py collisions --recorder config/farm-history/` (the recorder history of the reference farm):

| Collision | Count | Rules that met | Fixed by |
|---|---|---|---|
| Change reversed within 15 min at sunset (stop → start → stop) | 16 pairs, 10-08 16:48–17:54 | import range narrower than the lowest step × "start a stopped miner first" × early ramp-lock end | `rule.sunset-one-by-one` (the 10-08 farm simulation now has none) |
| Start while restarting | 4 (10-08 13:09, 15:22, 15:28, 15:43) | ramp lock ended early for a "stopped" miner × the pause switch reading off during a restart | 9b0b2c4, then the settling record (S11) |
| Two changes within a minute | 10-07 16:02 (three at once), 10-08 11:59/12:00 | several changes per proposal; early end | one change per proposal (3ca794c); the 10-08 pair is an allowed early end |
| Up on one miner, down on another within 15 min | 2 (10-08 15:06/15:19, 15:19/15:25) | a big cut (2,500 → 1,700 W) then a step up when the import fell below the range | Not changed (overlap 5): cloud dynamics, not even load; even load shipped later (0.7.7). Watch for it in decisions.jsonl |
| Reasons flipping more than 6 times in 10 min | 5 windows | meter gaps ("solar sensor unavailable") between normal cycles | Expected: a gap holds (U3 keeps it for 5 minutes) |
