---
date: 2026-10-06
topic: doc review: semi-automatic apply plan, cross-checked for conflicts
reviewed: docs/plans/2026-10-06-001-feat-semi-automatic-apply-plan.md
checked_against:
  - docs/brainstorms/2026-10-05-decision-making-requirements.md
  - docs/brainstorms/solar-smart-miner-requirements.md
  - docs/plans/2026-05-15-001-feat-solar-smart-miner-ha-integration-plan.md
  - custom_components/solar_smart_miner/knowledge/
  - code: decision.py, control.py, ai.py, button.py, hass-miner switch.py / number.py
---

# Doc review: rules vs AI autonomy (2026-10-06)

Question asked: find conflicts between the current docs, given that the main problem is the sweet
spot between deterministic rules and giving the AI more autonomy.

Reviewers: coherence, feasibility, adversarial, product-lens, scope-guardian. 13 actionable
findings after merging; 12 applied, 1 deferred. Nothing below changed code: the code bugs became
plan units S10-S14, to be built with tests.

> **Note:** a parallel session committed `8e9e9d8` (activity-log card) while this ran and swept the
> plan edits below into that commit. Everything else is uncommitted in the working tree.

---

## 1. The core conflict

The documents describe two different futures for the AI and never connect them.

| | Requirements doc §7 (decided) | Plan, code and knowledge base (before this review) |
|---|---|---|
| Who gains autonomy | The **AI**, in stages: advise → choose allocation → choose the watt delta, inside the rules' envelope | The **rules** (Phase 2 "Auto"); nothing for the AI |
| AI answer | `pause/resume/increase/reduce/hold` + a **target wattage per miner** | Direction only; prompt says "never suggest other wattages" |
| Why the AI isn't applied | Stage 1 of the roadmap | "The AI has no wattage in its answer" (Decision 1) |
| `rule.apply-by-hand-first` | | "never the AI answer", with no time limit |
| Rules the AI is told about | `rule.guard-above-ai` (filter AI answers), `rule.ramp-lock`, `rule.down-slowly-up-promptly` | None of the three exist in code |

**Where the review lands (the sweet spot):**

- **The rules own the envelope:** P0 safety, power steps, the miner's range, and the dynamics (ramp lock,
  hold time). Dynamics move into `decision.py` so that what is shown, what the AI sees and what Auto
  runs are the same plan.
- **The AI owns choices inside the envelope:** which miner moves, by how many steps (stage 2).
- **Authority grows on evidence**, with a gate for the AI that is separate from the gate for rule Auto
  mode, both measured from the action log.
- **Prerequisites:** (a) the AI answer names a target step per miner (deferred decision, §4);
  (b) the evidence is trustworthy: S11 (real verification) and S14 (proposal + outcome logging).

---

## 2. Changes by file

### 2.1 `docs/plans/2026-10-06-001-feat-semi-automatic-apply-plan.md` (committed in 8e9e9d8)

| Where | Change | Finding |
|---|---|---|
| Progress | S1-S8 checked (implemented in e7a24bf..60fabf6) | stale checklist |
| Progress | New **Phase 1 follow-ups** S10-S14; A2 retitled; new **Phase 3** AI1 | below |
| Where-we-are, Power steps row | "one step at a time" → "only the miner's steps … One plan can move several steps at once (`_allocate`); a step cap, if wanted, belongs in A2" | `_allocate` jumps several steps |
| Where-we-are, AI advice row | Cites `rule.ai-is-advisor`, `rule.apply-by-hand-first` instead of P0 `rule.guard-above-ai` | wrong rule cited |
| Decision 1 | "Only the rule plan is applied, **in this phase**". It now rests on `rule.ai-is-advisor`, not on "the AI has no wattage", and points to Phase 3 and the open question | AI-authority path |
| Decision 2 | "applies exactly what was shown" → "applies the plan that is current at the press, and nothing newer", plus the honest gap (fingerprint read at press time, static confirm text) and S13 | fingerprint guarantee |
| Decision 4 | `rule.one-controller` scoped to automatic control; the warning needs S10, and until then there is none | schedule conflict |
| S5 goal | Narrowed: S5 is "the first half" of the outcome fields; after-effects and unpressed proposals come with S14 | evidence gap |
| **New S10** | Schedule-automation setting; `schedule_conflict` attribute, card warning, notification text; Manual still applies | warning had no unit |
| **New S11** | Verify against power and hashrate, not hass-miner's echo; `set_limit` done only after a hass-miner refresh and the miner hashing again; lock held and miner treated as ramping through the restart | optimistic state; restart looks like stop |
| **New S12** | Any exception from the service call = `failed`, lock released, logged | `APIError`/`TypeError` escape `_send` |
| **New S13** | Refuse a press when the plan changed within the last poll interval (min 30 s); Apply all skips those | fingerprint gap |
| **New S14** | `trigger: "proposed"` lines (appear/change/expire), outcome line 60 min after `ok` (import, hashrate, restarts, reversed), rule-engine version on every line | "skipped"/"reversed" not measurable |
| A2 | Split: hold time, `rule.ramp-lock` and `rule.down-slowly-up-promptly` go **into `decision.py`** (all modes, should land before go-live evidence counts); `control.py` keeps change cap, flapping guard, schedule conflict (S10 setting), failure freeze; repeated refusals logged once | dynamics in wrong layer |
| Phase 2 evidence | Per situation, "expired unpressed" instead of "skipped", needs S14, valid only for the engine version that produced it | evidence/version |
| **New Phase 3** | AI chooses allocation; AI plans become `MinerPlan`s through the same safety + dynamics gate, `trigger="ai"`; own evidence (`open.ai-authority-evidence`); prerequisite: answer names a step | no AI path |
| Risks | Schedule row points to S10; new row "logged `ok` but the miner never acted → S11" | |
| **New** Deferred / Open Questions | AI answer format (see §4) | deferred |

### 2.2 `knowledge/rules.yaml`

**`rule.one-controller`** (P0, still `decided`)

- Before: "…never control the miners together. The schedule is switched off when applying starts."
- After: "…never control the miners **automatically** together. The schedule is switched off before
  automatic applying starts. In Manual mode the owner is the controller and is warned while the
  schedule is on."
- `source` adds "Manual exception: plan 2026-10-06-001 Decision 4"; `date` 2026-10-06; `note` keeps
  the old wording and says the warning needs S10.
- **Please confirm:** this narrows a P0 that came from requirements §0 point 10. If you'd rather the
  Manual mode refuse while the schedule is on, revert this and change Decision 4 instead.

**`rule.guard-above-ai`** (P0): statement unchanged; new `note`: enforced once AI proposals can be
applied (stage 2 / Phase 3); today no AI answer reaches `control.py`; enforcement will be the
`async_apply` guards.

**`rule.apply-by-hand-first`** (P1)

- Before: "Only the rule plan is applied, never the AI answer."
- After: "In this phase only the rule plan is applied, not the AI answer (the AI gains authority
  later, in stages, requirements doc §7)."

### 2.3 `knowledge/site.yaml`, `site.schedule-automation`

- Before: "…to be switched off when the integration starts applying decisions, never both at once."
- After: "…to be switched off before the integration applies decisions automatically
  (rule.one-controller); with manual Apply they may still run, and the owner is warned."

### 2.4 `knowledge/alerts.yaml`

- `control.apply-failed`: `thresholds: {grace_s: 60}` → `{grace_s: 60, relay_start_grace_s: 300}`.
  (The note from S8 already said Phase 1 only notifies.)
- `control.schedule-conflict`: note now says Auto refuses, Manual only warns, both need the
  automation entity setting (S10).

### 2.5 `knowledge/miners.yaml`: new `miner.hass-miner-optimistic` (P3, verified from code)

> hass-miner's pause switch and power-limit number show the new value as soon as the command is
> sent, not when the miner has acted, and the switch keeps that value until the miner agrees. A
> stop/start error is only logged by hass-miner, never raised. Whether a command took shows in the
> miner's power and hashrate, not in those two entities.

Source: hass-miner `switch.py` `async_turn_on/off` (sets `_attr_is_on`, `updating_switch`, catches
`Exception`) and `number.py` `async_set_native_value` (sets `_attr_native_value`, raises
`pyasic.APIError` / `TypeError`). Verified by reading `config/custom_components/miner/`.

### 2.6 `knowledge/open-questions.yaml`

- `open.go-live-evidence` note: "skipped" → "expired unpressed", per situation, needs S14, counts only
  for the engine version that produced it.
- **New `open.ai-authority-evidence`:** what evidence moves the AI to stage 2; separate gate from
  rule Auto; candidate measure: AI-vs-rule disagreement (incl. hold plans) and outcomes when the
  owner applied a plan the AI agreed vs disagreed with.
- **New `open.ai-answer-format`:** the deferred decision (§4).

### 2.7 `docs/brainstorms/solar-smart-miner-requirements.md`

Banner at the top: partly superseded; R5-R7, R9-R11, R14-R17 describe the earlier AI-controller
design and are kept for the record; links to the decision-making requirements and the apply plan.

### 2.8 `docs/plans/2026-05-15-001-feat-solar-smart-miner-ha-integration-plan.md`

Banner at the top: R5-R17, AE2/AE5, U4-U6, the dry-run switch in U7 and AI-applied changes in U8 are
replaced; safety plans no longer fire in a dry-run, and in Manual they wait for the button.

### 2.9 `README.md`

- Intro: "uses an AI agent to dynamically control…" → "steers ASIC miner power limits … using a
  rule-based controller with an AI advisor".
- How-it-works paragraph: the rules propose a step (or stop/start) per miner that you apply with a
  button; the AI advisor reviews the same inputs and the proposal.
- Features: "AI-driven power control" → "Rule-based power control with an AI advisor … gains
  authority only in later, evidence-gated stages".

Tests: `tests/test_knowledge.py` passes (303).

---

## 3. Findings and what happened to each

| # | Sev | Finding | Raised by | Outcome |
|---|---|---|---|---|
| 1 | P1 | P0 `rule.one-controller` contradicted by Decision 4; promised warning has no unit or code | adversarial, scope, feasibility | Applied: S10, KB scoped |
| 2 | P1 | Verification reads hass-miner's optimistic state; restart window looks like a stopped miner | feasibility, adversarial | Applied: S11, KB fact |
| 3 | P1 | hass-miner errors don't become `failed` (`control.py:255` catches only HA/vol errors) | feasibility | Applied: S12 |
| 4 | P1 | "Applies exactly what was shown" doesn't hold (fingerprint read at press time) | adversarial, feasibility | Applied: Decision 2 reworded, S13 |
| 5 | P1 | Action log can't measure skipped / reversed / after-effects | product, adversarial, feasibility | Applied: S14, S5 narrowed |
| 6 | P1 | AI answer format: requirements §7 target wattage vs prompt "never suggest wattages" | coherence, product, adversarial | **Deferred** (§4) |
| 7 | P1 | No path or evidence gate for AI authority | product, scope, feasibility | Applied: Phase 3, open question |
| 8 | P1 | Hold time / flapping guard in executor instead of decision | adversarial, feasibility | Applied: A2 split |
| 9 | P1 | Original requirements, parent plan and README still promise AI control and dry-run safety | product, scope | Applied: banners, README |
| 10 | P2 | Plan cites `rule.guard-above-ai` for "never apply AI"; that rule describes a filter that doesn't exist | product, adversarial, scope | Applied: citation, note |
| 11 | P2 | `control.apply-failed` alert: 60 s only; plan uses 300 s for relay starts | scope, feasibility | Applied: threshold |
| 12 | P2 | "Plans are one step at a time" is false | feasibility | Applied: reworded |
| 13 | P2 | Go-live evidence not tied to a rule-engine version | product | Applied: Phase 2 + S14 |

---

## 4. Your decision: AI answer format (deferred)

Requirements §7 decided the AI answer carries a **target wattage per miner**. The prompt in
`ai.py` now says "never suggest other wattages", and the answer is direction only. With direction
only, no AI answer can ever be applied, and the log can't score what the AI would have done.

Recommendation: a **target power step per miner** (not an arbitrary wattage). It fits
`rule.power-steps` and is the prerequisite for Phase 3. Tracked as `open.ai-answer-format` and in the
plan's Deferred / Open Questions.

---

## 5. FYI (no action taken)

- The AI's view is recorded only for pressed plans; disagreements on `hold` plans are lost. S14's
  proposal lines cover actionable plans only; `ai_log.jsonl` remains the unbiased source.
- A start can cost two restarts: resume at the old limit, then the planned limit.
- hass-miner's resume restores the mining mode saved at pause, so a limit set while paused may be
  overwritten. In S9 step 4, check the limit **after** resuming.
- Hold time still waits on `conflict.ramp-vs-tuning` (about 4 min ramp lock vs about 50 min tuning
  window, measured).
- Decision 7 (safety plans wait for the button) is harmless today, but matters once a voltage sensor
  exists while still in Manual mode.

## 6. Open questions the reviewers raised

- What numeric threshold (applied-as-shown rate, reversal rate, coverage per situation) is enough for
  Auto, and who signs it off (a KB entry moving `open.go-live-evidence` to decided)?
- Does rule Auto (Phase 2) have to come before AI stage 2, or can the Manual phase also collect
  owner-applied AI allocations?
- Does the active switch flip off on every limit restart, including step-downs? S11 depends on it
  (`miner.limit-change-cost` has two step-up samples only).
- Should the S9 checklist require deciding on the schedule automation before the first press?
