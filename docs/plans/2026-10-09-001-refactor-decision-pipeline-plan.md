---
title: "refactor: Decision pipeline (§11), farm data apart from the rules, and the remaining Solar-follow rules, without regressing the live farm"
type: refactor
date: 2026-10-09
updated: 2026-10-09  # owner's answers; smoothing dropped; U3 reworked; U7 farm data, U8 hard-coded values
status: completed  # 0.8.0, 2026-10-09
origin: docs/brainstorms/2026-10-05-decision-making-requirements.md
related_plans:
  - docs/plans/2026-10-06-001-feat-semi-automatic-apply-plan.md  # S10-S14, A2 still open
  - docs/plans/2026-10-09-002-feat-telegram-notifications-plan.md  # where the alerts below get pushed
---

# refactor: Decision pipeline, farm data, and the remaining Solar-follow rules

## Summary

The requirements doc ends with "turn this into a plan for the rule engine (the `decision/` package, §11)". This
is that plan. It also covers two things the owner asked for on 2026-10-09:

- **The integration is for any farm.** No miner model, firmware, site or entity id in the code, the prompt or
  the shipped knowledge base; values that depend on the miner or the site are settings.
- **Rules apart from farm data.** The decision rules ship with the integration. What is true of one farm (its
  inverters, miners, roof, base load, measurements) is structured data that the user fills in through
  Configure, with the owner's farm as the worked example.

**The main constraint:** the farm runs in **Automatic** mode (v0.7.2+). Any change to the decision changes what
the miners do within 15 s of HA picking up the release. 9b0b2c4 is the proof: a restart read as a stop gave
"start at 900 W" plans that undid three changes on 2026-10-08, and it was found on the farm, not in tests.
So this plan first builds a net that catches a changed proposal before a release (U0), refactors without
changing behaviour (U1), and then changes behaviour one rule per release.

Implemented in 0.8.0 (2026-10-09), one release instead of one per unit (owner); see the commits from 1a25d94 to the 0.8.0 bump.

---

## Owner's answers (2026-10-09)

| Question | Answer | Effect on the plan |
|---|---|---|
| After 30 min without the grid meter, step down or only warn? | Rare; usually the meter is gone for a couple of minutes. Rely on an estimate from the current solar production, and alert (Telegram, later). | U3 reworked: an estimated import from solar production after a 5-min grace, no blind step-down; the alert goes through the Telegram plan's notifier. (Review: meter gaps are daily on the farm, 42 on 10-08, all under 4 min.) |
| Should smoothing (U4) happen at all? | No, remove it. | U4 dropped. `rule.smooth-inputs` retired, `rule.down-slowly-up-promptly` note updated (in U3's commit, since both touch rules.yaml). |
| Which entity gives the voltage? | Add it, configurable. | U5 confirmed: an optional voltage entity in Configure. |
| Stored profile? | The farm runs Solar-max with the 200–400 W import range. | Confirmed: `solar_max` is the live logic. |
| Rename / drop profiles (U2)? | Reduce them, as discussed (second answer, 2026-10-09; the first was read as "keep the select for now"). | U2 active: one profile, Solar-follow. U1 still moves all four branches unchanged; U2 removes three right after. |
| What is "morning" for the longer step-down delay? | Morning means sunrise: not a fixed elevation, but the period while the sun is rising and production is still changing quickly (owner, 2026-10-09, replacing the earlier "15°"). | Overlap 2: one transition helper (sunrise and sunset) built on production change, not degrees. | U9 overlap 2 decided: the longer delay applies only during sunrise, with one shared definition of sunrise. |
| Model names and hard-coded values | Remove them during this plan. | U8. |
| Farm-specific information | Formatted data the user adds in Configure; review with an example from the current farm. | U7, example below. |

---

## Changes since the earlier plans

The apply plan's progress list stops at Phase 2 (v0.7.2). These shipped after it, from the farm's first days
in Automatic. They are part of the behaviour U0 must preserve:

| Commit | Release | What it changed |
|---|---|---|
| 8003b62 | 0.7.3 | Solar-max starts miners on an import below the floor; step-down waits (step-down delay, longer while the sun rises) |
| 05b2b27 | 0.7.3 | Solar-max keeps the grid import in a range, 200 to 400 W by default |
| fc69adc | 0.7.4 | The ramp lock ends early once the changed miner draws within 5% of its new limit (after at least 1 min) |
| 8f6a8aa, bd8d293 | 0.7.5–0.7.6 | An import range narrower than one step is widened; the import minimum has its own setting |
| 9b0b2c4 | 0.7.6 | A miner restarting after a limit change is not read as stopped (the pause switch shows off for 1–2.5 min) |
| 0955d09 | 0.7.7 | Even load once every miner runs; tuning settles in 5 minutes |

Consequences for this plan and the apply plan:

- **S11** (verify against the miner) is partly covered: 9b0b2c4 handles "the decision treats the miner as
  ramping rather than stopped" during a restart. Still open: a command logged `ok` on hass-miner's echo.
- The **ramp lock** is now two values (4 min upper bound, 5% / 1 min early end) and the **tuning window** is
  5 min. U8 decides which become settings.
- The step-down delay is what the requirements called the **cloud tolerance**; no separate value is needed.
- These shipped without plan units, so the apply plan gets a short "Shipped after Phase 2" note pointing here.

---

## Simplification first (owner, 2026-10-09)

> The rules were added over time, with bug fixes on top, and may conflict. Simplify while doing this plan.
> Always aim to reduce the rules: the more rules, the more ways they break.

This is now a project principle (CLAUDE.md, "Fewer rules"). For this plan it means: **every unit leaves the
same or fewer rules than it found**, and one unit (U9) exists only to remove and merge.

### Collisions already seen (from the commit history, 2026-10-06 to 2026-10-08)

Each of these was two rules or two settings meeting, found on the farm, fixed by adding code:

| When | Rules that met | What happened | Fix added |
|---|---|---|---|
| 10-08 | ramp lock ends early when a miner is stopped (fc69adc) × start a stopped miner first × hass-miner shows the pause switch off during a restart | "start at 900 W" undid three changes (Brod3 13:09, Brod1 15:22, Brod3 15:28) | 9b0b2c4: a restart isn't read as stopped (a third rule on top) |
| 10-08 | budget allocation (needs spare power for a step) × zero export (the budget never shows spare power) | the farm stayed off from 07:50 to 09:54 with sun available | 8003b62: `step_up_anyway`, a second steering path that bypasses the budget |
| 10-08 | the old import target setting × the new import range | range "400 W to 400 W", step up and down around one value | 8f6a8aa + bd8d293: widen the range, new key, form check |
| 10-08 | tuning window (60 min stored) × no step-up while tuning × one change per proposal | one step up per miner per hour; the farm lagged the sun at 0 W import | 0955d09: default 5 min, stored 60 rewritten |
| 10-07 | forecast headroom in the budget × PV sum dropouts | the budget jumped by the headroom whenever the PV reading went missing | 55e5f48: forecast reference only (a rule removed: the right kind of fix) |
| 10-07 | three miners changed together | farm load to ~0 W, inverters throttled (16:02 incident) | 3ca794c: one miner per proposal + ramp lock |

### Collisions in the farm's own history (HA recorder, 2026-10-05 to 10-09, read over Tailscale)

Pulled read-only from the farm's HA (`config/farm-history/`, git-ignored): the decision log summaries, every
applied command (`last_action`), limits, pause switches, the grid meter, PV and the forecast. The decision
*trace* attribute isn't recorded by HA, so the summaries and readings are what's available.

| Finding (2026-10-08, Automatic) | Evidence | Rules that met |
|---|---|---|
| **Stop/start loop at sunset.** 9 stop → start → stop rounds between 16:48 and 18:13, a 10-minute cycle: Brod3 ×2, Brod2 ×4, Brod1 ×3 | import 500–1,200 W → (5-min delay) stop → import ~0 W → start at 900 W 3 min later → import ~900–1,200 W → stop | import range 200–400 W (narrower than the smallest move at the bottom: a whole miner, 900 W) × "import below the minimum → start a stopped miner" × ramp lock ends early for a stopped miner × no sunset rule (`rule.stop-trigger-below-start`, `rule.sunset-one-by-one` are in the knowledge base, not the code). **New: overlap 8.** |
| Restart read as a stop: **4** cases, not 3 | Brod3 13:09, Brod1 15:22, Brod3 15:28, Brod2 15:43: a limit change followed 1–3 min later by "start at 900 W" | fixed by 9b0b2c4 (that evening) |
| **Grid meter gaps held the farm** | 1 min (10-06), 13 min (10-07), **40 min** (10-08) of daylight in "Safety: solar sensor unavailable". All 27 such decisions on 10-08 came within 60 s of a **grid meter** gap (only 8 near a PV gap): the farm's "solar entity" is the grid meter (net type), so `solar_fault` means "meter lost". Meter gaps per day: 10 → 20 → 42, longest 59 s → 233 s → 214 s | the hold is the right behaviour for a short meter gap (it is U3's grace); the cause of the growing gaps (meter or dongle polling load?) is not known (overlap 7) |
| Waiting for a restart: 118 min of daylight on 10-08 | ~60 commands; the ramp lock held the farm for 2 h | expected cost of one change at a time; the loop above added ~40 min of it |

### Overlaps visible in the code today (to check against the farm's logs)

1. **Two steering paths in Solar-max.** The budget (`available + import_min`, then `+ import_max − import_min −
   HOLD_TOLERANCE_W`, with `down_at_w = inf` to switch the budget's own step-down off) *and* the measured import
   thresholds (`step_up_anyway`, the step-down delay). Each fix since 404ee71 added to one or the other. The
   measured import already decides when; the budget only sizes the cut. **Decided (owner, 2026-10-09): steer
   on the import alone**. Without a budget:
   - **Down:** the smallest cut that brings the import under the maximum (today's fit check, rewritten as
     import − Δlimit ≤ maximum).
   - **Up:** one increment: start a stopped miner at its lowest step, else one step for the weakest running
     miner (coolest of equals).
   - **Even load:** the ceiling is "one step above the next-weakest" (no budget share term).

   That removes `HOLD_TOLERANCE_W`, `UP_MARGIN_W`, `down_at_w`, `step_up_anyway` and the budget arithmetic
   from Solar-max. Expected replay changes: multi-step jumps up become single steps (each one a restart, so
   the sunrise ramp-up gets slower by ramp locks). The commit lists the changed cycles.
2. **Two "sunrise" definitions.** The decision uses `sun.sun`'s `rising` attribute, which is true from solar
   midnight **until solar noon**, so the 30-min morning step-down delay applies all morning, not just at
   sunrise: a cloud at 11:00 waits 30 min before anything steps down. `kb.py` uses elevation below 15°.
   **Decided (owner, 2026-10-09): morning means sunrise, and sunrise is a period, not an elevation:** the sun
   is rising and production is still changing quickly. One **transition helper** decides it, for the
   decision and `kb.py` alike, and also decides **sunset** (the mirror: production falling), which overlap 8
   needs.
   - What the farm's history shows: on 10-06, 10-07 and 10-08 the potential rose steeply from about 07:45
     until about 10:30 (PV 300–600 W → 4,500–5,400 W), roughly three hours after sunrise. 15° (about 08:30)
     would have ended "sunrise" far too early, as the owner said.
   - The signal is the hard part on a zero-export site: throttled PV follows the load, and the forecast
     sensor moves in hourly jumps (0 W until 08:45 on all three mornings). Decided (owner, 2026-10-09): the
     import moving on its own, "without a change of ours". **How (from the 2026-10-09 review):** the import
     itself can't be used, because the controller keeps changing it (a change every 5–6 min in a transition,
     so a 15-min window with no change of ours rarely exists). Use what the import implies about production
     instead, which our own changes cancel out of:
     `production = measured miner draw + base load − import` (true while the import is above 0, i.e. the
     inverters aren't throttling), sampled every poll **outside ramp locks** into a 15-min ring buffer in the
     coordinator. Sunrise: it keeps rising; sunset: it keeps falling, by more than a threshold over the window.
   - **Sunset latches** (review): once detected it stays on until `sun.sun` is rising again, so the stop that
     sunset itself causes can't switch it off (that is how the 10-08 loop would survive a sliding window).
     Sunrise ends when production stops rising over the window. The latch is seeded on reload like the ramp
     lock (`seed_activity`).
   - **Gates** (`sun.sun` only opens the window, production decides): sunrise only while the sun is rising;
     sunset only in the last ~2 h before `next_setting`, so a 14:00 house load or long cloud can't latch
     "sunset" and block starts for the rest of a sunny afternoon. The forecast may confirm but never decides
     (`rule.forecast-reference-only`).
   - The window and threshold come from the farm's history (now available, `open.rise-and-fall-speed`) and
     are fixed values, not settings, unless another farm proves them wrong.
   - The longer step-down delay applies only during sunrise; the setting is renamed "Step-down delay during
     sunrise" (same stored key).
3. **Three "wait after a change" mechanisms.** Ramp lock (4 min, ends early at 5% / 1 min), tuning window (5 min:
   no step-up, temperature ignored), and the step-down delay counted again from the last change. Since every
   step is tuned, the tuning window and the ramp lock now measure almost the same thing. Candidate: one
   per-miner "settling" state with one duration; the tuning window remains only for a never-run step (off by
   default).
4. **The ramp lock's inputs.** `_last_change`, `_changed_at` per miner, `minutes_since_limit_change`, the
   pending check (0 min), `ramp_done`, and the restart-not-stopped fix. Candidate: one per-miner record set when
   a command is sent or a change is seen, cleared by "drawing its new limit", "stopped by our command" or the
   timeout.
5. **Even load × import range.** Even load may step the weakest miner up while the import is in range; if that
   pushes the import over the maximum, the delay then steps the hungriest down. Check the logs for an up on
   one miner followed by a down on another within ~15 min.
6. **Rules for cases this farm can't reach yet.** The `grid_agnostic`, `grid_independent`, `battery_focused`
   branches (removed in U2) and the battery floor without a battery sensor. They cost little at run time but every
   change to the decision must keep them working.
7. **"Solar sensor unavailable" is a grid-meter hold on the reference farm** (corrected after the
   2026-10-09 review). The configured solar entity can be a net (grid) type, and on the farm it is the grid
   meter, so `solar_fault` there means "meter lost". It is **not** removed on its own: it becomes U3's
   grace period, written against the configuration shapes: for a net-type entity, `solar_fault` is the meter
   lost; for a production-type entity with a house sensor, the derived grid balance is lost together with PV.
   Both go through U3's single "grid balance unknown" path, in U3's release. Separately worth finding out:
   why meter gaps grew from 10 to 42 a day (all shorter than the 5-min grace so far).
8. **Start/stop at the bottom of the ladder (the 10-08 sunset loop).** The range check makes the import range
   at least one *step* wide (200 W), but starting or stopping a miner moves the import by its *lowest step*
   (900 W). So at the bottom, every stop lands below the minimum and every start above the maximum. Simplest
   fix, one rule instead of new special cases: **during sunset, nothing starts** (**decided**, owner
   2026-10-09; an evening cloud that clears doesn't restart a miner until morning) (the transition helper of
   overlap 2), which is what `rule.sunset-one-by-one` and `rule.stop-trigger-below-start` already say. They
   become one enforced rule in group 3 (Limits, beside "sun down"), and "sun down blocks starts" merges into
   it. Acceptance (U0's closed-loop test on 10-08 16:45–18:15): no start after the first sunset stop, and no
   change reversed within 15 min (blocking starts alone would still allow up/down churn on running miners). If midday clouds show the same
   loop in later logs, the next candidate is "a miner we stopped isn't started again by the same rule within
   the step-down delay".

### The knowledge base: 33 active rules

Overlapping groups to merge into one entry each (the others retired with a pointer):

| Group | Entries | Merged into |
|---|---|---|
| Restarts | `minimise-restarts`, `one-miner-per-change`, `jump-several-steps` | one "one change at a time, as large as needed" |
| Allocation | `prefer-spread`, `even-load`, `step-down-allocation`, `consolidate-needs-sustained-deficit` | one allocation rule |
| Pace | `transitions-tolerant`, `down-slowly-up-promptly`, `sunrise-fast`, `smooth-inputs` (retired, U4 dropped) | one "step down after the delay, step up promptly" |
| After a change | `ramp-lock`, `no-step-up-while-tuning`, `temperature-after-change` | one "settling" rule (overlap 3) |
| Not in the code, design only | `transition-by-agreement`, `sunset-one-by-one`, `stop-trigger-below-start`, `sustained-low-voltage` (until U5) | kept, but marked `assumed`/not enforced so the AI doesn't read them as active |

Target: about 20 active rules, each either enforced by code (with a test) or clearly marked as advice for the AI.

### Groups and precedence (owner's suggestion, 2026-10-09)

Today a rule's **binding level** is in the knowledge base (P0 hard limit … P3 context), but which rule **wins**
when two disagree is set only by the order of the `if` blocks in `build_decision`, and nowhere written down.
Most collisions above are two rules whose precedence nobody chose. So the rules get **groups in a fixed order**,
and an order inside each group. Reviewing priority later means moving a rule in this list, replaying, and
releasing.

| # | Group | What its rules may do | Rules (today's code) |
|---|---|---|---|
| 1 | **Safety** | end the decision; may change several miners; skips the pacing | miner range / step ladder, required inputs (grid meter, U3), battery floor, voltage (U5) |
| 2 | **Pacing** | end the decision with "hold" | settling after a change (ramp lock + tuning, merged in U9), one change per proposal |
| 3 | **Limits** | block a direction, or require "reduce miner X" (Allocation carries it out first); never choose among miners | temperature band (no step-up; "reduce the too-warm miner one step"), sunset/sun down (no start) |
| 4 | **Target** (the profile) | say *up*, *down* or *hold*, and how much | Solar-max: import below the minimum → up; above the maximum for the delay → down; else hold |
| 5 | **Allocation** | turn up/down into one miner's step | start a stopped miner first; cut the hungriest that can take it, else stop the lowest; even load |
| 6 | **Tidy** | only when nothing else changed | move a limit that is off the steps onto the nearest step |

Rules for it:

- **An earlier group always wins.** A later group never undoes an earlier one: allocation can't start a miner
  that pacing holds (the 9b0b2c4 collision was allocation acting on a miner that pacing should have owned).
- **Across cycles too** (review): precedence is checked every 15 s, and once a Safety or Limits action lands its
  condition clears. So: **a miner reduced or stopped by Safety or Limits isn't raised or started again by Target
  within the step-down delay.** One generic rule instead of a fix per case (voltage, temperature, sunset).
- **Each group's rules are a list in the code, in order**, each with its knowledge-base id. The trace prints
  the group and the rule that decided ("Pacing / rule.ramp-lock: Brod1 changed 2 min ago"), so a log line
  shows which rule won.
- **The knowledge base records the group:** a `stage` field (`safety`, `pacing`, `limits`, `target`,
  `allocation`, `tidy`, or `advice` for rules only the AI reads) beside `priority`. Two axes: `priority` says
  how binding a rule is, `stage` and its position say what it overrides. `test_knowledge.py` checks every
  active P0/P1 rule has a stage, and a test checks the code's list and the entries agree.
- **Not a runtime setting.** Priority is reviewed by the owner and changed in the code with replays, not with
  numbers in Configure: user-adjustable precedence would multiply the combinations that can collide, the
  opposite of "fewer rules".
- Groups also keep the count honest: a group that grows past a handful of rules is the first place to look for
  a merge.

This is built in **U1** (the package split follows the groups: `safety.py`, `pacing.py`, `limits.py`,
`profiles.py`, `allocation.py`) and filled in by **U9** (the merged rules land in their group). Changing a
rule's group or position later is a one-line move, a replay run and a patch release.

---

## Progress

**Track A: safety net and refactor (no behaviour change)**

- [ ] **U0**: Replay net: log the full decision inputs, replay real cycles in tests, closed-loop farm test
- [ ] **U1**: Split `decision.py` into the `decision/` package (pure move)

**Track A2: simplification (changes behaviour on purpose)**

- [ ] **U9**: Rule audit and simplification: collisions found in the farm's logs, overlaps 1–6 and 8 removed or merged (7 goes with U3), knowledge base down to ~20 active rules

**Track B: generic integration (no behaviour change on the reference farm)**

- [ ] **U8**: No model names or hard-coded farm values in code and prompt; miner/site values become settings
- [ ] **U7**: Farm data apart from the rules: a Configure step and a farm file; the shipped knowledge base keeps principles only
- [ ] **U2**: One profile, Solar-follow: `solar_max` renamed, `grid_agnostic` / `grid_independent` / `battery_focused` removed and migrated

**Track C: decided rules not yet in the code (each changes behaviour)**

- [ ] **U3**: Grid meter lost: one "grid balance unknown" path, 5-min grace (today's hold), then an estimated import, alert
- [ ] **U5**: Voltage safety (optional, configurable voltage entity)

**Track D: AI answer (no effect on the miners)**

- [ ] **U6**: AI answer names a target step per miner, with `pause` / `resume` / `ramping` (`open.ai-answer-format`)

**On hold / dropped**

- ~~**U4**: energy input smoothing~~: dropped (owner, 2026-10-09). The step-down delay and the ramp lock cover it.

**Blocked on measurements, not planned in detail**

- Sunrise / sunset start and stop triggers with a gap between them (`open.rise-and-fall-speed`)
- Minimum hold time per miner beyond the tuning window (`open.temperature-settle-time`)

---

## Where the code is against the requirements

| Requirement (doc §) | State in the code | In this plan |
|---|---|---|
| Pipeline split, common + profile strategy (§11) | One 559-line `decision.py`; safety, ramp lock, temperature, profile and allocation in one function | U1 |
| Ramp lock with an early end (§5.2, round 7) | Done (fc69adc) | U8: setting |
| One miner per proposal, fewest restarts (§5.2) | Done (`_one_change`) | — |
| Pause threshold = lowest step; pause instead of minimum (§5.3) | Done | — |
| Start the next miner at the lowest step at sunrise (round 7) | Done | — |
| Small steady import range (§6.2) | Done (Solar-max, 200–400 W) | — |
| Step down slowly / cloud tolerance (§5.2) | Done as the step-down delay (5 min; 30 min while the sun rises) | — |
| Temperature target + tolerance; ignored while tuning (§3.2) | Done | — |
| Even load (§5.3) | Done (0955d09) | — |
| Miner paused by the schedule detected (round 7) | Done through `is_stopped`; restart not read as stop (9b0b2c4) | — |
| Input smoothing (round 7) | Not done | **dropped** |
| Profile rename and migration (round 7, §6.5) | Not done | U2 |
| Required inputs: grid import, not solar (§3.4) | Partly: on a net-type solar entity (the reference farm) the "solar fault" hold is already the meter-lost hold, but forever; an unknown derived balance holds forever | U3 |
| Voltage safety (§3.3) | Not done | U5 |
| AI vocabulary with a target per miner (§7) | Not done | U6 |
| Site and miner values as settings (§8, "the first farm is a reference") | Partly: steps, temperature, import range, delays, tuning window are settings; ramp lock, margins and the knowledge base are not | U7, U8 |

---

## Regression strategy

The farm can regress in three ways: a **different proposal** for the same situation, a **different timing**,
or a **crash** in the update cycle (entities go unavailable and Automatic stops applying). The plan answers each:

1. **Replay net (U0).** Real cycles from the farm, with the proposal the released code made, become test
   fixtures. Every later unit must reproduce them exactly, or its commit names the cycles that changed and why.
   The existing 73 decision tests and 76 coordinator tests stay. Include the 2026-10-08 restart cycles
   (9b0b2c4) and the 16:02 unseen load as fixtures: they are the cases synthetic tests missed.
2. **Refactor first, alone (U1).** A pure move, released on its own, replays unchanged.
3. **U7 and U8 change no decision on the reference farm.** Every new setting defaults to today's value, so
   replays stay identical. What changes is what the AI is told, which doesn't move the miners.
4. **One behaviour change per release** (each U9 item, U3, U5), patch bumps. An item that touches steering or
   the transitions (U9 overlaps 1, 2, 8) is watched through a **full sunset and the next sunrise** before the
   next release, since a midday check never runs those rules. Rollback: redownload the previous release in
   HACS.
5. **New behaviour off unless configured.** U5 does nothing without a voltage entity. U3 keeps today's hold
   for meter gaps under 5 min (dozens a day on the farm) and changes only longer ones.
6. **Manual for the first hours after a behaviour release.** Two clicks; a README line says so.
7. **Crash isolation.** U1 adds a hold-all fallback: an exception inside `build_decision` gives a decision
   that holds every miner, with the error in the trace, instead of a failed update.

---

## Implementation units

### U0. Replay net

**Goal:** Real farm cycles become regression tests, so a changed proposal is seen before release.

- Record everything `build_decision` takes, which no log has today: the full `CoordinatorSnapshot` (every
  `EnergySnapshot` field, including `solar_fault`, `battery_soc_pct` and `available_for_miners_w`, and every
  `MinerSnapshot` field, including `relay_entity_id` / `switch_entity_id`, serialised from the dataclasses)
  plus every keyword argument: `profile`, `temp_target`, `temp_tolerance`, `battery_floor`, `power_steps`,
  `tuning_settle_minutes`, `import_min_w`, `import_max_w`, `minutes_since_change`, `ramp_lock_minutes`,
  `ramp_done`, `minutes_import_high`, `step_down_delay_minutes`, `morning_step_down_delay_minutes`,
  `sun_up`, `sun_rising`.
- Where: the AI log writes only when an AI request is sent, and Automatic applies without one. So write the
  record to **its own `decisions.jsonl`** (a `JsonlLog` like the others), not to `actions.jsonl`: the action
  log's reload reads its last 20 lines to restore the ramp lock (`seed_activity`), and decision lines would
  push the commands out of that tail. Write when the **plans' fingerprints** change (not the summary text,
  which carries live wattages and changes almost every poll). S14's proposal lines refer to these records by
  timestamp.
- `tests/replay/` with 10–20 hand-picked records (entity ids made generic) and `tests/test_replay.py`, which
  rebuilds the inputs, calls `build_decision` and compares plans (action, limit, method) and the summary.
- `scripts/replay.py <actions.jsonl>` runs a day's log through the current code and prints the cycles whose
  plan differs. Used before every behaviour release; not a test.

- The script also reports **collisions** in a log, the cases where rules meet:
  - a change reversed within 15 min (same miner up then down, or stop then start);
  - a `start` on a miner within the ramp lock of a change on it (the 9b0b2c4 pattern);
  - an up on one miner and a down on another within 15 min (overlap 5);
  - a step-down that waited the morning delay after 10:00 (overlap 2);
  - a hold whose reason flips more than N times in 10 min;
  - more than one change inside one ramp lock.
  It runs on the logs the farm already has (`actions.jsonl`, `ai_log.jsonl`) and on the recorder history
  (`--recorder config/farm-history/`), so the audit (U9) can start before U0's new fields have collected a day.

- **Closed-loop test** (review: an open-loop replay can't show a loop, and 9b0b2c4 was a coordinator bug):
  `tests/test_farm_sim.py` drives the coordinator's stateful parts (ramp lock, import-high timer, transition
  helper) and `build_decision` against a small farm model: import = house + miner draw − min(PV potential,
  load); a changed miner draws ~0 W for the restart; PV potential from the 10-08 recorder curve. It asserts the
  collision checks above: no change reversed within 15 min, no start after the first sunset stop, at most one
  change per ramp lock. The 10-08 sunset and the 13:09/15:22 restarts are its first scenarios.

**Effort:** small–medium. ~60 lines logging, ~80 lines replay test + loader, ~120 lines script, ~150 lines
farm model and scenarios. 1–2 sessions, then a day of logs.
**Regression risk:** very low (a new log file and tests).

### U1. `decision/` package (pure move)

**Goal:** The §11 shape, with no behaviour change, so later rules each land in one small file.

```
decision/
  __init__.py      # build_decision(): runs the groups in order; re-exports what other modules import today
  describe.py      # _describe_energy, _describe_miner, _describe_plan, describe_proposal
  safety.py        # group 1: sensor health, battery floor (meter estimate U3, voltage U5)
  pacing.py        # group 2: ramp lock, one change per proposal
  limits.py        # group 3: temperature band, sun down
  profiles.py      # group 4: Solar-max import range; the other three as today (removed in U2)
  allocation.py    # groups 5-6: _ladder, _nearest_level, _one_change, even load, off-step tidy, min_import_range_w
```

The group order above is today's `if` order (safety → ramp lock → temperature → profile → allocation), so
the split changes nothing; it only names the groups. Where today's code breaks the order (the temperature
step-down picks a miner itself, which is allocation's job), U1 keeps the behaviour and U9 moves it.

- Each step either returns a finished `Decision` or passes a small context on. Trace text stays word for word
  (the card, the AI prompt and the replay fixtures read it).
- Importers keep working through `decision/__init__.py`: `coordinator.py` (`build_decision`,
  `describe_proposal`, `_describe_plan`), `control.py` (`_ladder`, `_describe_plan`), `action_log.py`
  (`_describe_plan`), `config_flow.py` and `tests/test_decision.py` (`min_import_range_w`).
- Keep the order exactly: safety → ramp lock → temperature → `grid_agnostic` branch → budget → allocation.
- `tests/test_decision.py` is not split here: it tests `build_decision` from the outside, which is what proves
  nothing changed.
- Hold-all fallback on an exception (regression strategy, point 7), with a test.

**Effort:** medium. ~560 lines moved into 7 files, ~40 new lines. 1 session.
**Regression risk:** low with U0, medium without it. Release alone.

### U9. Rule audit and simplification

**Goal:** Fewer rules, each with one purpose, one place in the code, one test and one knowledge-base entry.

1. **Inventory** (a table in `docs/solutions/`, kept up to date afterwards): every rule in the decision, in
   pipeline order, with the code location, the knowledge-base id, the test(s), and the incident or decision that
   added it. A rule with no test or no entry is either given one or removed.
2. **Collisions from the farm's logs:** run the U0 script over every day of `actions.jsonl` / `ai_log.jsonl`
   since 2026-10-06. Each collision found gets a line in the inventory: which rules, how often, and the fix.
3. **Simplify, one commit per item, replays and the closed-loop test checked each time:** the overlaps 1–4,
   6 and 8 (they remove or merge code), then 5 if the logs show it. Overlap 7 goes with U3. A change in a replayed plan is allowed only where the logs show the
   old plan was wrong; the commit names those cycles.
4. **Knowledge base:** merge the groups above; the active rule count goes in the commit message.
5. Keep the trace text where it means the same thing; where a rule disappears, its trace line goes too.

**Effort:** medium–large. Inventory and log analysis 1 session; simplification 1–2 sessions, mostly removing
code (overlap 1 alone is about 60 lines of `decision.py` and two constants). Net lines **down**.
**Regression risk:** medium, because it changes code that runs every cycle. Mitigated by U0 (replays and the
collision report before and after each commit) and by releasing each simplification alone with a few hours in
Manual, or through a sunset and sunrise for steering and transition items (regression strategy, point 4). U9
comes after U1 so each removal lands in one small file.

### U8. No model names or hard-coded farm values

**Goal:** Nothing in the code or the prompt assumes the reference farm's miners or site. Values that depend on
the miner type or the site are settings with today's values as defaults; generic mechanics stay constants.

What is there today, and what happens to it:

| Where | Today | Change |
|---|---|---|
| `decision.py` comment, `rule.temperature-is-braiins` | "Braiins OS cutoff" | "the miner's own temperature cutoff"; rule id kept (ids are never reused), title and statement generic, Braiins in `note` |
| `decision.py` docstring | "2 to 4 minutes at no power" | "while it restarts (the ramp lock)" |
| `DEFAULT_RAMP_LOCK_MINUTES = 4` | constant | **setting** "Restart time after a change" (miner type), default 4; the apply plan's deferred item. Thread it through all three uses: `coordinator._minutes_since_change`, the restart-not-stopped override in `_async_read_miners` (9b0b2c4), and `build_decision`'s `ramp_lock_minutes`; test a non-default value across the restart window. |
| `RAMP_DONE_FRACTION = 0.05`, `RAMP_MIN_MINUTES = 1` | constants | stay constants (generic: "drawing its new limit"); docstring says why |
| `HOLD_TOLERANCE_W = 150`, `UP_MARGIN_W = 100` | fixed watts, sized for 200 W steps | removed in U9 overlap 1 (Solar-follow) and U2 (other profiles); nothing to derive |
| `LIMIT_STEP_W = 10` | fallback rounding | stays (generic) |
| `DEFAULT_POWER_STEPS`, temperature, tuning window, import range, delays | settings with farm defaults | stay; the comments say "default from the reference farm" instead of describing it |
| `KB_NIGHT_ELEVATION`, `KB_TRANSITION_ELEVATION` | constants | stay (generic sun geometry) |
| `ai.py` system prompt | generic already, apart from wording about "the farm's load" | review the full prompt for site facts; any left move to the farm data (U7) |
| README examples (`Brod1 1,500 W`) | farm names | "Miner 1" style examples; the reference farm is named as such once |
| `knowledge/*.yaml` | 22 entries with farm values in the statement (site 8, miners 10, energy 4), 6 rules and 6 open questions with figures | handled in U7 |
| Tests | Brod names in fixtures | stay: test data, not product |

**Effort:** small. ~8 files, ~100 lines; new setting in `config_flow.py` + strings; tests for the setting.
1 session.
**Regression risk:** none at the default (4 min). The replays prove it.

### U7. Farm data apart from the rules

**Goal:** The shipped knowledge base holds **principles** (true on any farm, worded against the settings). The
**farm** (its equipment, layout, measured behaviour) is structured data the user owns and edits in Configure.
The AI gets both: the rules from the integration, the farm from the user.

**Two layers, two homes**

| Layer | Home | Edited by | Examples |
|---|---|---|---|
| Rules and principles | `custom_components/.../knowledge/` (ships with every release) | us, in the repo | `rule.ramp-lock`, `rule.small-import-target`, situations, alerts, "a settled miner draws about its limit" |
| Farm profile (structured) | the config entry's options, Configure → **Farm** | the user, in the UI | inverters, export policy, battery, cooling, miner model and firmware, base load, schedule automation |
| Farm notes and measurements | `<config>/solar_smart_miner/farm.yaml`, next to the AI and action logs | the user (file editor), or later the integration itself | measured curves, tuning times, settled values, meter dropouts |

Why two farm homes: the Configure form suits a few fields that every farm has and that the code may read
(export policy, base load, schedule automation). Measurements are many, dated, and in the knowledge-base entry
format already; a form would be clumsy for them. Both survive an update, since neither is inside the
integration's folder (HACS replaces that folder on update).

**The Farm step (Configure → Farm)**

| Field | Type | Used by | Reference farm |
|---|---|---|---|
| Inverters | text | prompt | 3 × Huawei SUN2000-5KTL-L1, 5 kW AC each, 15 kW total |
| Export to the grid | select: zero export / export allowed | prompt now; the decision later (§6.1 export policy) | zero export (inverters report "power limited") |
| Battery | select: none / present | prompt; profile choice later | none |
| PV array | text | prompt | east-west roof, 2 × 7.5 kWp, 80° and 260°, 15° tilt (assumed) |
| Cooling | select: air / immersion / hydro | prompt; temperature defaults hint | immersion, no fans |
| Miner model and firmware | text | prompt | Antminer S9, Braiins OS, via hass-miner |
| Miner's own temperature cutoff | number, °C, optional | prompt | about 80 °C |
| Base load besides the miners | number, W, optional | prompt now; U3's estimate | about 500 W |
| Schedule automation | entity (multiple), optional | apply plan S10 warning | the 07:00/19:00 pause/resume automations |
| Notes | multi-line text | prompt | free text |

Location and time zone come from HA (`hass.config`), not a field. Sensors stay where they are (Sensors step).

**The farm file (`farm.yaml`)**, same entry format as the knowledge base (`id`, `title`, `statement`,
`priority` P3 only, `status`, `source`, `date`, `tags`), ids prefixed `farm.`. Created empty with a commented
header on first setup. `kb.py` loads it after the shipped files; a broken file is logged once and skipped, never
fails setup. P0–P2 entries in it are refused (the user can't add rules that override the code; logged).

**Moving today's entries.** Each of the 22 farm entries is split, as CLAUDE.md asks: the principle stays in
the shipped file, worded against the settings; the farm's figures go to the farm data. The reference farm's
full set is kept in the repo as `docs/reference-farm/farm.yaml` plus its Configure values (a draft of both
is already there, uncommitted, generated 2026-10-09), as the worked
example and as a test fixture. The owner copies it to `<config>/solar_smart_miner/farm.yaml` once (README
step; not automatic: the integration can't tell which install is the reference farm).

| Today (shipped) | Principle that stays shipped | Goes to the farm data |
|---|---|---|
| `site.location` | — | HA location (automatic); first PV ~07:00, sun gone ~18:20 in early October → `farm.yaml` |
| `site.inverters` | — | Farm step: Inverters; the sum-sensor detail → `farm.yaml` |
| `site.array-orientation` | — | Farm step: PV array |
| `site.no-battery` | "the grid is always connected and absorbs swings" stays generic | Farm step: Battery = none |
| `site.grid-meter-sign` | sign convention of the configured grid sensor (code already handles it) | entity id → nothing (it's in the Sensors step) |
| `site.zero-export` | `energy.throttle-hides-headroom` already states the principle | Farm step: Export = zero export |
| `site.base-load` | "non-miner load decides how low the import can go" | Farm step: Base load 500 W |
| `site.forecast-setup`, `site.forecast-accuracy` | — | `farm.yaml` |
| `site.schedule-automation` | `rule.one-controller` | Farm step: Schedule automation |
| `site.profiles-planned` | — | retired (the profile is a setting) |
| `miner.fleet` | "miners are read through hass-miner: a power-limit number and a pause switch" | model, firmware → Farm step; names, ranges, entity ids → `farm.yaml` |
| `miner.immersion` | "temperature follows power and cooling" | Farm step: Cooling, cutoff |
| `miner.draw-vs-limit`, `miner.settled-values`, `miner.tuning-signature`, `miner.tuning-duration`, `miner.hashrate-dips`, `miner.efficiency-range` | one-line principles ("a settled miner draws about its limit", "efficiency (J/TH) shows tuning", "dips during tuning are the tuner probing") | the measured figures → `farm.yaml` |
| `miner.tuning-not-exposed`, `miner.revisit-is-a-restart` | stay, without entity ids or the 900–2,500 W range | — |
| `energy.evening-curve`, `energy.morning-curve`, `energy.pv-sum-unreliable`, `energy.meter-dropouts` | "short meter gaps are normal and must not make the controller act" | the dated curves and entity ids → `farm.yaml` |
| 6 rules, 6 open questions with figures | reworded against the settings ("the configured power steps") | figures to `note` (allowed by CLAUDE.md) or to `farm.yaml` |

**Example: the reference farm's `farm.yaml` (excerpt)**

```yaml
# Facts about this farm, for the AI. Same format as the integration's knowledge base,
# P3 only. Edit freely; reload the integration to apply.
entries:
  - id: farm.miners
    title: Three S9s on Braiins OS
    statement: >
      Three miners, Brod1, Brod2 and Brod3, Antminer S9 on Braiins OS. Each accepts 500 to
      3500 W in 100 W steps; every configured step from 900 to 2,500 W has been tuned once.
    priority: P3
    status: verified
    source: "measured: HA entity registry, 2026-10-05"
    date: "2026-10-05"
    tags: [always]

  - id: farm.settled-values
    title: Settled hashrate per step
    statement: >
      Settled: 1300 W gives about 59 TH/s, 1500 W 68, 1700 W 76 to 78, 1900 W 84 to 88, all
      near 22 J/TH, so efficiency is flat from 1300 to 1900 W.
    priority: P3
    status: verified
    source: "measured: median of 15 to 25 min, 2026-10-06"
    date: "2026-10-06"
    tags: [midday]

  - id: farm.meter-dropouts
    title: Grid meter gaps
    statement: >
      The grid meter drops out for about 35 seconds a few times an evening. Gaps of minutes
      are normal; longer than that has not been seen.
    priority: P3
    status: verified
    source: "measured: 2026-10-05 17:39 and 18:43; owner 2026-10-09"
    date: "2026-10-09"
    tags: [always, grid]
```

**How it reaches the AI.** The Farm step's fields are rendered into a short "This farm" block in the prompt (a
few lines, always sent). `farm.yaml` entries join the P3 pool and are picked by situation within
`KB_PROMPT_BUDGET_CHARS`, as P3 entries are today.

**Tests:** `test_knowledge.py` gains a check that shipped statements contain no entity ids
(`sensor.`/`switch.`), no names from the reference-farm fixture, and no model or firmware names (a short
denylist); `test_kb.py`: `farm.yaml` loaded after the shipped files, missing file is fine, broken file is
logged and skipped, P0–P2 in it refused; the Farm step saves and renders; the reference-farm fixture loads
cleanly.

**Effort:** large, the biggest unit. ~22 entries split and reworded (the main work, and it needs the owner's
review), `kb.py` ~60 lines, Farm step ~120 lines + strings, prompt block ~30 lines, README section; ~15 tests.
2 sessions (code; then the entry move with review).
**Regression risk:** none on the miners (the decision doesn't read the knowledge base). The AI's advice may
change in wording while the owner's `farm.yaml` is empty; copying the example back restores the context.

### U3. Grid meter lost: estimate, don't step down blind

**Goal:** §3.4 with the owner's 2026-10-09 answer. Solar-follow needs the grid import, not the solar sensor.
A short gap changes nothing; a long one uses an estimate from solar production and raises an alert.

- **One "grid balance unknown" path** (overlap 7): for a net-type solar entity, today's `solar_fault` *is*
  the meter lost; for a production-type entity, the balance derived from solar minus house is lost with it.
  A fault of the actual-PV sensor alone holds nothing; it only makes the estimate below unavailable.
- **Grid balance unknown, less than the grace period (5 min, owner 2026-10-09; a fixed value, not a setting):**
  every miner holds, as today. The trace says "grid meter unknown for N s". On the reference farm this is
  dozens of times a day (42 gaps on 10-08, longest 214 s), so this path is common, not rare.
- **Unknown for longer:** the import is **estimated** (U9 has removed the budget by then):
  `estimated import = measured miner draw + base load − actual PV`. Base load from the U7 Farm step; PV only
  from the actual-PV sensor (`CONF_PV_ENTITY`), never from the configured solar entity, which may be the lost
  meter itself. With no base load configured or no PV reading: hold, as today. Not the forecast: `55e5f48`
  decided the forecast is reference only.
  - The estimate may only **reduce** (step down, stop), never step up or start: on a zero-export site PV
    equals the load, so it can't show spare sun, only a shortfall.
  - **No extra rules for this case** (owner, 2026-10-09): the estimate stands in for the import reading and
    the normal pipeline decides, with the normal pacing. No special stop of the last miner, no own timers.
  - The step-down delay's timer (`_minutes_import_high`) today resets whenever the meter reads unknown, so
    every gap restarts the 5/30-min wait. During the grace period it is frozen (neither reset nor advanced);
    after it, it is fed from the estimated import. Tests for both.
- **Alert `sensor.meter-lost`** (warning) after 10 min unknown: persistent notification now, pushed to
  Telegram once the Telegram plan's U2/U5 land (it already marks this alert `push`). Resolved when the meter
  reads again.
- The coordinator tracks how long the grid has been unknown (like `_minutes_import_high`).
- **Knowledge base:** `rule.required-inputs` reworded (estimate instead of "step down at 30"), source
  "owner, 2026-10-09"; `sensor.meter-lost` alert note; `rule.smooth-inputs` → `retired` (U4 dropped);
  `rule.down-slowly-up-promptly` note: no smoothing window, the step-down delay covers it.
- **To record as a conflict, not decide here:** `energy.pv-sum-unreliable` says the PV sum reads unknown every
  few minutes in the evening (142 gaps on 10-08). The estimate then flips between "estimated" and "hold";
  acceptable, since a meter gap longer than 5 min hasn't been seen yet. Note it.

**Effort:** medium. ~70 lines decision (`decision/safety.py`), ~30 coordinator, alert wiring ~20, one setting;
~14 tests (gap < grace holds, for a net-type and a production-type solar entity; > grace with PV steps down
on a shortfall and never up; no PV or no base load holds; a PV-only fault holds nothing; the import-high timer
frozen during the grace and fed after it; alert after 10 min, resolved on return). 1 session.
**Regression risk:** medium. The < 5 min path runs dozens of times a day on the reference farm and must stay
exactly today's hold (replays prove it); the > 5 min estimate is new and rare.

### U5. Voltage safety

**Goal:** §3.3, confirmed 2026-10-09: an optional, configurable voltage entity.

- Configure → Sensors: voltage entity (optional). Configure → Settings: low-voltage threshold (default 210 V,
  from the reference farm's 230 V supply) and debounce (default 60 s).
- **Low voltage means too much load** (owner, 2026-10-09): stopping one miner raises the voltage. So
  `decision/safety.py`: voltage below the threshold for the whole debounce window → **stop one miner** (the
  one drawing the most), then the normal ramp lock, then check again; one miner at a time, never several at
  once (several stopping together is the 16:02 problem in reverse). No step-down variant and no separate
  hard-stop threshold: one rule.
- **Unlike the rest of Safety, it respects the pacing** (review): it acts only outside a ramp lock, and its
  debounce window restarts after each of our changes, so a lagging sensor can't stop a second miner 15 s later.
- **No restart loop** (review): a miner stopped for voltage isn't started again within the step-down delay
  (the general rule in "Groups and precedence"), so "import below the minimum → start" can't undo it at once.
- Tests: voltage still low during the ramp lock plans no second stop; a voltage stop isn't followed by a start
  within the delay.
- Knowledge base: `rule.sustained-low-voltage` reworded to "stop one miner at a time", `energy.voltage-source`
  gets the owner's explanation and moves from `assumed` once a sensor is chosen.
- Alert `site.low-voltage` if the catalogue has it, else added (warning, push).

**Effort:** medium. ~40 lines decision, ~30 coordinator (debounce), config flow + strings ~40; ~10 tests. 1 session.
**Regression risk:** none without the entity. With one: the debounce covers a noisy sensor.

### U6. AI answer with a target step

**Goal:** `open.ai-answer-format`, §7: the AI answer can later be applied and scored.

- `ai.py` prompt and parser: per miner `action` (`pause` / `resume` / `increase` / `reduce` / `hold`),
  `target_w` (one of the configured steps), reason vocabulary including `ramping`. An off-step wattage is shown
  and logged as invalid, never rounded. The parser accepts the old answer for one release.
- The prompt gets recent history: last change per miner and its age, the ramp lock state.
- AI advice sensor and card show the target next to the rule plan.

**Effort:** medium. ~80 lines `ai.py`, ~30 `sensor.py`; `test_ai.py` (31 tests) largely rewritten. 1 session.
**Regression risk:** none for the miners (`rule.ai-is-advisor`).

### U2. One profile: Solar-follow

**Goal:** Requirements round 6–7 (§0.5, §0.6 point 6, §6.5), confirmed by the owner on 2026-10-09: reduce the
profiles. Full power / Grid-agnostic and Grid-independent are dropped; the battery profiles are open and not
built, so `battery_focused` goes too. That leaves one profile, and three rule paths fewer (the "Fewer Rules"
principle).

- `const.py`: `PROFILES` holds `solar_follow` only ("Solar-follow: aim for a small steady grid import");
  `DEFAULT_PROFILE = "solar_follow"`.
- Migration: one helper resolves the stored profile; `async_setup_entry` rewrites `solar_max`, `grid_agnostic`,
  `grid_independent` and `battery_focused` to `solar_follow` once and logs it (same pattern as Preview → Manual
  and the import-key fix).
- Decision: the `grid_agnostic`, `grid_independent` and `battery_focused` branches go; the `profile ==
  "solar_max"` checks become the only path. The battery floor stays in Safety (it is inert without a battery
  sensor, and the battery profiles will need it).
- **The profile select is removed** (owner, 2026-10-09): with one profile it has nothing to choose. Add the
  select entity to `RETIRED_ENTITIES` and drop the Configure field; keep the stored `profile` option.
  Leave a short comment at `PROFILES` in `const.py` (and one in `select.py`'s retirement or `__init__.py`)
  for the battery version: "One profile until Setup B (battery). The battery profiles (requirements doc §6.3)
  add entries here, bring back the profile select (ProfileSelect, retired in vX) and the Configure field;
  the stored `profile` option is kept for that." The knowledge base's `open` battery entries point to it.
- Trace, AI prompt, knowledge base (`site.profiles-planned` retired, situations and rules renamed), README,
  strings: "Solar-follow" for "Solar-max".
- Replays: plans unchanged on the reference farm; the trace's profile label changes, so the fixtures'
  summaries are updated in the same commit.

**Tests:** each old stored value reads and is rewritten as `solar_follow` (rewrite the existing profile tests
into these, don't delete them); the dropped profiles' decision tests become migration tests; the select is
gone and retired; the options flow has no profile field.

**Effort:** small–medium. ~10 files, ~200 lines, mostly removals and names. 1 session.
**Regression risk:** low on the reference farm (its `solar_max` logic is unchanged). An install on a dropped
profile changes to Solar-follow; the migration log line says so.

---

## Effort overview

| Unit | Code files | Lines changed (approx.) | Tests | Sessions | Farm risk | Can be switched off |
|---|---|---|---|---|---|---|
| U0 replay net | 2–3 + script | ~400 | replay + closed-loop farm test | 1–2 (+1 day of logs) | very low | n/a |
| U1 package split | 7 new, 4 importers | ~600 moved, ~40 new | existing must pass | 1 | low (with U0) | no, release alone |
| U9 audit + simplify | 3–4 + KB | net **−150** or more | replays + rewritten | 2–3 | medium | each item released alone |
| U8 hard-coded values | ~8 | ~150 (less after U9 overlap 1) | ~8 new | 1 | none (replays equal) | defaults = today |
| U7 farm data | ~6 + 7 KB files + example | ~350 code/strings, ~22 entries reworded | ~15 new | 2 | none on the miners | n/a |
| U3 meter lost | 3 + KB | ~140 | ~14 new | 1 | medium (the < 5 min hold runs daily) | no |
| U5 voltage | 4 | ~110 | ~10 new | 1 | none without entity | yes |
| U6 AI format | 2–3 | ~120 | ~20 rewritten | 1 | none | n/a |
| ~~U4 smoothing~~ | | | | 0 | | dropped |
| U2 profiles | ~10 | ~200, mostly removed | ~10 rewritten | 1 | low | no |

**Total:** about 12–13 sessions and 10–11 patch releases (S11 not included), roughly 2,300 lines touched, of which
~600 are a move, and the decision code ends **smaller** than today. ~22 knowledge entries are reworded and ~13
rules merged or retired. Calendar time is set by the farm: a day of logs after U0, and some hours to a day of
observation after U1, each U9 simplification, U3 and U5, about 2.5–3 weeks.

The apply plan's follow-ups (S10–S14, A2) are a separate track (~5 sessions). U7's Schedule automation field
is the setting S10 needs, so S10 shrinks to the warning itself. U0's decision lines overlap with S14.

---

## Recommended order

0. **S11 from the apply plan first** (owner, 2026-10-09): verify a command against the miner's power and
   hashrate, not hass-miner's echo, since Automatic runs unattended. Its own release. **It replaces, not
   adds** (review): its "done" criterion becomes the single per-miner settling record of overlap 4, and the
   9b0b2c4 restart override and the fc69adc early end are expressed through it, named in the release notes.
   Without this it would be a third definition of "restart done".
0. **Before any code:** the collision report over the farm's existing logs (U0's script can be written first,
   in a scratch branch of thought, against the current log format). It may reorder what follows.
1. **U0** (release alone; collect a day of logs).
2. **U1** (release alone; replays unchanged).
3. **U9** (one simplification per release). Simplify *before* adding anything: U3 and U5 then land in a
   smaller decision.
4. **U2** (right after U9: fewer branches before anything new), then **U8**, then **U7** (no decision
   change; U7 needs the owner's review of the reworded entries).
5. **U3** (needs U7's base load), then **U5**, a day apart, Manual for the first hours.
6. **U6** any time.

Stopping after U0 + U1 + U9 is a valid outcome: the code is in the §11 shape, smaller, with its collisions
known, and the farm behaves as now or better.

---

## Questions answered by the owner (2026-10-09)

1. ~~**U3:** grace period, stop the last miner?~~ 5 minutes; no extra rules, the normal pipeline decides on
   the estimate (owner, 2026-10-09).
2. ~~**U7:** are the Farm step fields right?~~ Yes (owner, 2026-10-09). Still open, later: should the
   integration write its own measurements to `farm.yaml` (for example settled values per step)?
3. ~~**U2:** remove the profile select?~~ Decided: remove it, with a comment for the battery version.
4. ~~**U5:** hard stop or step down?~~ Neither: stop one miner at a time (owner, 2026-10-09).
5. ~~S11 first?~~ Yes (owner, 2026-10-09); first in the order.
6. ~~**The farm's logs**~~: resolved. The farm's HA is reachable over Tailscale (token in the git-ignored
   `secrets/ha-agent.env`), and its recorder history is in `config/farm-history/` (git-ignored). The
   integration's own `actions.jsonl` / `ai_log.jsonl` aren't exposed by HA's API; if the audit needs the
   traces, copy them with the File editor add-on, or U0's log lines will carry them from now on.
7. ~~**U9 overlap 1:** steer on the import alone?~~ Yes (owner, 2026-10-09).
8. ~~**U9 overlap 2:** what should "morning" mean?~~ Decided: the sunrise period, from how production
   changes, not an elevation (owner, 2026-10-09). ~~Is "the import keeps falling without a change of ours" an
   acceptable signal?~~ Yes (owner, 2026-10-09).
9. ~~**Overlap 8:** is "during sunset nothing starts" acceptable?~~ Yes (owner, 2026-10-09), accepting that an
   evening cloud that clears won't restart a miner until the next morning.

---

## Not in scope

Battery profiles (§6.3), tariffs, pool economics, AI authority (apply plan Phase 3), the sunrise/sunset
triggers and the minimum hold time until their measurements exist, input smoothing (dropped).
