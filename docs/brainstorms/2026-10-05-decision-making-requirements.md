# Decision Making — Requirements Notes

**Status:** collecting thoughts (living doc). Review rounds 1–4 done on 2026-10-05 (see §0–§0.3). Round 5 (§0.4) resolves conflicts between earlier rounds and is proposed by Claude. Round 6 (§0.5, 2026-10-06) redefines the profiles and leaves all battery configuration open. Round 7 (§0.6, 2026-10-07) answers the questions that blocked implementing Solar-follow. Round 8 (§0.7, 2026-10-07) sets the power steps and the temperature defaults after the first day of applying proposals.
**Decisions now live in the knowledge base** (`custom_components/solar_smart_miner/knowledge/`, moved on 2026-10-06). That is where they are kept up to date and what the AI is given; this doc stays as the record of how they were reached.
**Next step:** settle the remaining open questions (§9), then turn this into a plan for the rule engine (the `decision/` package, §11). The AI prompt comes after that.

How to use this doc: add thoughts anywhere under **Notes** blocks. Items marked **(suggestion)** are additions from Claude, not yet agreed. Items marked **(open)** need a decision. Items marked **(decided)** were agreed in a review round. Items marked **(proposed, round 5)** are Claude's resolution of a conflict. They count as decided unless you veto them.

---

## 0. Decisions from review round 1 (2026-10-05)

1. **The rules control the miners. The AI is an advisor on top.** Its advice goes to a separate log so the user can see when the AI proposed something different from the rules, and under what conditions. Building that comparison view comes later.
2. **AI interval: default 60 s, minimum 10 s.** Changed in code (`const.py`) together with this review. 10–15 s is expected in production.
3. **Goal (§1):** use as much of the excess energy as possible. On battery, efficiency comes first.
4. **Small constant grid import (Setup A) is confirmed.** Without a battery, importing a little is the only proof that all available solar is used. Otherwise the inverter throttles and nothing shows that the miners could go higher.
5. **The miners run in immersion mode, with no fans.** Temperature follows power and cooling directly. The only Braiins OS temperature setting that matters is the **cutoff**.
6. **Temperature is not an emergency.** A miner that is too hot at minimum power is left to the Braiins OS cutoff, which restarts it. ~~We monitor and log it.~~ *(Superseded by round 4, point 4: at most a note in the decision trace.)* Temperature steps follow the normal pace and never skip the queue.
7. **Below ~1000 W a miner is not worth running.** *(Round 7: 900 W is fine; the lowest power step is the pause threshold, §0.6.)* That is the point to pause it with Braiins OS pause, not to idle it at its hardware minimum.
8. **Restarts are the main cost to keep low** (refined in round 2: a trade-off, not a hard rule). Changing one miner by 600 W beats changing three miners by 200 W each. A decision **may** change several miners when needed (for example pause one and raise another), so the AI answer stays multi-miner.
9. **Some grid import is fine at sunrise, sunset and during clouds.** Production changes quickly then, so don't chase it.
10. **Handover:** when the integration starts applying decisions, the fixed-hour schedule automation is switched off. They never run together.
11. **The battery setup (B) is parked.** The current focus is Setup A (no battery) ~~and Full power~~. *(Round 6: Full power is dropped and the profiles are redefined, §0.5.)*
12. **Max grid import cap: dropped.** In practice the miners don't run at hardware max because of cooling, so temperature is the limit that matters.

## 0.1 Decisions from review round 2 (2026-10-05)

1. **Choosing which miners to change is a trade-off, not a fixed rule.** Take a 600–700 W drop. Cutting one miner by the whole amount costs one restart. Spreading it over three keeps them in the efficient range but costs three restarts. The best choice depends on the trend (the end of the day is different from a passing cloud). The rules are the **starting approach**. Over time, the AI drives more of these choices, uses more factors, and learns from the logs and past decisions (§7).
2. ~~Morning start at ~500 W of solar~~: withdrawn in round 3. The 500 W figure was only an example.
3. **A fast morning is expected.** About 3 minutes after the first miner starts, there may already be enough sun for more, so the morning pace can't wait for the normal hold times (§5.4).
4. **AI model:** a cheap paid model, not a free one, so free-tier request limits don't apply.
5. **Sunrise and sunset are confirmed by several signals agreeing:** measured solar, the time of day (expected sunrise or production hours) and the solar forecast. When most of them agree, start miners one by one in the morning, and stop them one by one in the evening.

## 0.2 Decisions from review round 3 (2026-10-05)

1. **Drop the 500 W start trigger and everything that followed from it.** The start and stop triggers are not set yet.
2. **Study how fast solar production rises in the morning and falls in the evening** before setting the triggers and the pace of transitions (§5.4).
3. **Importing during transitions is not a problem**, even for several minutes or longer, at sunrise, at sunset and when clouds pass. No "give up" rule is needed for these transitions.
4. **Confirmed: the AI can't learn from `ai_log.jsonl` yet.** The log needs outcome fields first (§7).

## 0.3 Decisions from review round 4 (2026-10-05)

1. **The Braiins OS cutoff is the final line of defense.** It is set on the miner. The plugin doesn't track it, warn about it or manage it.
2. **The plugin only follows target temperature + tolerance.** It doesn't do any other temperature monitoring. The "cutoff as information" and "smooth the temperature" suggestions are dropped.
3. **Important: every settings change restarts the miner, so its temperature always goes down first.** After the restart it climbs again and may reach the high band at the new setting. The old rule "after a step-down, wait for the temperature to settle before the next one" doesn't apply. A post-change reading is always low and says nothing.
4. **Too hot at minimum power is not the plugin's job.** It means something is wrong (cooling, ambient temperature, hardware). Other mechanisms handle it. Not even notify: at most it's noted in the decision trace.

## 0.4 Round 5: conflicts resolved (proposed by Claude, 2026-10-05)

A check of the whole doc found places where decisions from different rounds contradict each other. Each one is resolved below with Claude's proposal. They count as decided unless vetoed.

1. **Step-up size when the surplus can't be measured (§5.2 vs §6.2).** §5.2 says "step as large as the surplus supports". But in Setup A, a throttled inverter hides the surplus: zero import only says "there is *some*". **Resolution:**
   - Step **down** by the measured deficit (import − target), which is always measurable.
   - Step **up** by the measured export when there is export. When there is none, use the forecast headroom (*forecast PV now* − *actual PV*) if those sensors are configured. Otherwise use a fixed **probe step** (setting, default 400 W).
   - Never step by less than 200 W.
2. **Fast or slow step-down (§5.2 vs §5.4).** "Step down quickly, step up slowly" contradicted "wait out clouds" and the round 3 decision that import during transitions is fine. **Resolution: step down slowly, step up promptly.**
   - A deficit must last longer than the **cloud tolerance** (setting, from the §5.4 study) before a step-down. A cloud that passes then costs no restarts.
   - A surplus only needs to hold for the smoothing window. Extra sun is pure gain.
   - Fast reactions are kept for safety only (voltage, sensor health). Sunset mode handles the real evening decline.
3. **Reading temperature after a restart (§3.2 vs §5.3).** After a change, a miner's temperature is always low (round 4). The allocation rule picks "the miner with thermal headroom", so it would pick the miner that just restarted, which looks cool but isn't. **Resolution:** a miner's temperature counts as **settling** until its **minimum hold time** has passed. While settling, the miner isn't a step-up candidate and doesn't trigger a temperature step-down. This needs no extra setting: the hold time doubles as the warm-up time.
4. **Pausing on a small deficit (§5.3).** "Take the whole deficit from the highest miner, pause it if that drops it below the threshold" could pause a 1200 W miner for a 300 W deficit. That creates a 900 W surplus, and the next step-up restarts another miner. **Resolution:** take the deficit from the highest-power miner that can absorb it and **stay at or above the pause threshold**. Pause only when no running miner can, and then pause the lowest-power one.
5. **Editorial fixes:**
   - Temperature sits under §3 "Hard limits" but isn't a safety step. §3.2 now says it's applied in allocation (§11 step 4).
   - Input smoothing applies to the energy inputs only, not to temperature (round 4).
   - §0 point 6 is marked as superseded by round 4.
   - The status-line references now point to §9 and §11.

---

## 0.5 Decisions from review round 6 (2026-10-06): profiles

1. **Profiles differ by where the small steady draw comes from.** Only the first is in scope:
   - **Solar-follow** (no battery). The small, constant draw that proves all solar is used comes from the **grid**. Agreed as described in §6.2.
   - **Solar-follow with battery** (open). Probably the same controller, with the small draw coming from the **battery** instead of the grid.
   - **Solar + battery** (open). Uses the battery within predefined limits, aiming for the best efficiency while keeping a battery reserve.
2. **All battery configuration stays open.** The farm has no battery, so the work focuses on Setup A and the decision making for it (§2–§5, §6.2, §11). The two battery profiles are only placeholders for the shape of the set; nothing about them is decided.
3. **Full power is dropped.** Running every miner at maximum needs no decisions, rules or AI, so it is not a profile. Grid-agnostic is dropped with it.
4. Grid-independent stays dropped (§6.2). Battery-focused waits for the battery profiles (§6.3).

## 0.6 Decisions from review round 7 (2026-10-07): before implementing Solar-follow

1. **Pause threshold = the lowest power step (900 W).** Running at 900 W is fine; the 1000 W figure is dropped. A shortfall must outlast the cloud tolerance before a miner is paused.
2. **Ramp and tuning.** The 2–3 min ramp and 10–15 min hold figures are retired. On a step the miner has already tuned, it reaches full hashrate in **3–4 minutes**, so the ramp lock is about 4 minutes. **Temperature takes longer** to come back after the restart; how long is open (§9) and sets the minimum hold time. **Tuning** (about 50 minutes) is an exception, the `situation.tuning` for a step the miner has never run, not the normal cost of a change.
3. **Sunrise: start the next miner at the lowest step, don't raise the running one.** Production keeps rising at sunrise, and raising one miner again and again costs a restart each time. So start each miner at the lowest step, and start the next one when there is budget for its lowest step. The code's "fill a running miner first" is dropped.
4. **Required inputs (confirmed).** Solar-follow needs the grid import, not the solar sensor. While the grid import is unknown, nothing steps up; if it stays unknown, step down or pause (the meter-lost alert: warning at 10 min, step down at 30). This replaces "solar sensor unavailable → minimum".
5. **Temperature only limits.** A low temperature is never a reason to step up; a high one stops step-ups and forces step-downs. No extra margin below the target.
6. **Agreed as recommended:**
   - Input smoothing: energy inputs averaged over **3 minutes**; temperature isn't smoothed.
   - A `ramping` hold reason in the log and the AI vocabulary.
   - A miner paused by the schedule is detected (hass-miner state, else 0 W and 0 TH/s while reachable) and left out of allocation and the ramp check.
   - Moving off the old profiles: rename `solar_max` → `solar_follow`, and map stored `battery_focused`, `grid_agnostic` and `grid_independent` to `solar_follow`, logging the migration once.

## 0.7 Decisions from review round 8 (2026-10-07): after the first day of applying

1. **Power steps 900 to 2,500 W, in 200 W steps, are normal.** 900 to 1,500 W was only the first ladder. Every miner has run all of these steps, so a change between them is a restart, not a tuning. They are now the default steps.
2. **Higher steps are welcome later.** If the miners stay below the temperature limits, steps above 2,500 W may be added.
3. **Temperature defaults: target 60 °C, tolerance 10 °C** (step down at 70 °C). They were 65 °C and 10 °C.

---

## 1. Goal

**Use as much of the excess energy as possible for mining.** "Excess" means solar: what the panels can give, with a small steady draw from the grid as the proof that none of it is left unused.

**Scope (review 2026-10-07).** The integration serves farms of any size, miner model, cooling and inverter. The first farm (three S9s on Braiins OS, immersion cooled, zero-export inverters, no battery) is the **reference example**: its numbers in this document show how to reason and become defaults, never fixed rules. The §8 table says which values are which. Success criterion carried over from the original requirements: someone with a different solar brand and no battery can install the integration, configure it for their own miners in the HA UI, and reach a working preview.

- **This does not mean "highest power per miner".** On the reference farm the miners have similar efficiency between about **1000 W and 2000 W**, so 4000 W is better used by three miners at ~1330 W each than by two at 2000 W. With mixed models, the most efficient miners get power first.
- **On battery** (open, §6.3), efficiency was suggested to come first: hash per stored Wh.

Limits come in three kinds:

1. **Hard limits.** These are never broken, whatever the profile: voltage, sensor health, battery floor and power range.
2. **Energy limits.** These decide how much power the miners may draw, and the profile sets them.
3. **Control dynamics.** These cover how and when changes are applied. Every change restarts a miner, so they matter as much as the limits.

> **Notes:**

---

## 2. Where the code is today

- `decision.py` builds a *preview* only. Its proposals are logged and never applied.
- The order is: safety checks (solar sensor fault, battery floor, temperature ceiling), then the profile, then a power budget split **evenly** across all miners.
- Today "pause" means running at the hardware minimum (`_floor_w`). This is wrong for the hard limits: at minimum, a miner still drains the battery below its floor or imports at night. It has to become a real **Braiins OS pause** (§5.3).
- Temperature is a single hard ceiling (`DEFAULT_TEMP_CEILING = 80 °C`). There is no target or tolerance.
- No voltage input and no memory of previous changes. Every cycle decides from scratch, so it would change every miner at once.
- The AI answers in JSON with one action per miner: `increase` / `reduce` / `hold`, each with a reason. The answer has **no target wattage**, so it can't express "raise miner A by 600 W" or "pause B".
- Optional reference sensors (actual PV, forecast PV now / next hour / left today) feed the AI and the log, never the rules.
- Every AI request is logged to `ai_log.jsonl` with its inputs, the rule proposal and the answer.
- AI interval: default 60 s, minimum 10 s (since review round 1).

### 2.1 Current operational setup (outside this integration)

- Miners are stopped and started with the **Braiins OS pause / resume** function.
- A **separate Home Assistant automation** pauses and resumes mining at **fixed hours**.
- **(decided)** When the integration starts applying decisions, that automation is switched off.

**While the schedule still runs (advisory phase):**
- A miner at ~0 W and 0 TH/s may simply be **paused by the schedule**. The decision trace, the AI prompt and the log should show "paused (external)" so the advice isn't misread.
- **(decided, round 7)** Detect the paused state from hass-miner if it exposes it, otherwise from 0 W plus 0 TH/s while the miner is still reachable. Exclude paused miners from allocation and from the ramp check.
- **(suggestion)** When applying starts, the integration warns if the schedule automation is still enabled. This needs an option that names the automation entity.

> **Notes:**

---

## 3. Hard limits (always apply, override the profile)

### 3.1 Miner power range
- Each miner has a min/max power limit that hass-miner exposes (`min_power_w` / `max_power_w`).
- Proposals are clamped to this range and rounded to 10 W.
- The useful lower bound is the **pause threshold**, which is the lowest power step (900 W, round 7), not the hardware minimum.
- **(open, low priority)** A per-miner max below the hardware max. It may not be needed, because temperature already limits each miner in practice.

### 3.2 Temperature: target and tolerance
*Not a safety step: temperature is applied in allocation, at the normal pace (§11 step 4).*
- **(decided)** The miners run in immersion mode, with no fans. Temperature responds directly to power and cooling, so it is a usable control signal.
- **(decided)** The Braiins OS cutoff (~80 °C, set on the miner) is the **final line of defense**: the miner restarts and cools down. The plugin has **no emergency temperature action** and doesn't track the cutoff.
- **(decided)** The plugin only follows **target + tolerance**. Example: target 60 °C, tolerance 10 °C (the defaults since round 8). The user makes sure the range is wide enough.

| Miner temperature | Meaning | Action |
|---|---|---|
| below target (< 60 °C) | no limit from temperature | a step-up only if the **energy** calls for one; a low temperature is never the reason (round 7) |
| target … target + tolerance (60–70 °C) | in range | **hold** (no step up, even with spare energy) |
| at or above target + tolerance (≥ 70 °C) | too warm | **step down** (`temperature_limit`) |

- **(decided)** Temperature steps follow the normal pace (§5). They don't skip the queue.
- **(decided, round 4)** Too hot at the pause threshold or at minimum power: **not the plugin's job**. Something is wrong (cooling, ambient, hardware) and other mechanisms handle it. At most it's noted in the decision trace.
- **(decided, round 4) Every settings change restarts the miner, so the temperature always goes down first.** It drops to a very low level, then climbs as the miner ramps and hashes again. So:
  - A reading taken right after a change is always low and says nothing. Judge temperature only once the miner has ramped and warmed back up.
  - At the new setting it may climb back to target + tolerance. That's normal, and the next step down happens then, at the normal pace.
  - No separate "wait for it to settle before stepping down again" rule is needed. The restart itself makes the temperature fall.
- **(proposed, round 5)** A miner's temperature is **settling** until its minimum hold time has passed (§5.2). While settling, the miner isn't a step-up candidate and its temperature doesn't trigger a step-down.
  - **(decided, round 7)** Temperature takes longer to come back than the 3–4 min hashrate ramp. **(open)** How long (§9); it sets the minimum hold time.
- **(decided, round 7)** No margin below the target. Temperature only limits; it is never the reason for a step-up.

### 3.3 Inverter voltage
- Mining below **~210 V** is not recommended. The threshold is configurable.
- Instant dips can't be handled, so the system must not overreact.
- **(suggestion)** Act only on a *sustained* low voltage (for example an average over 30–60 s).
- **(suggestion)** On sustained low voltage, step miners down. Voltage is a real safety reason, so it may change several miners in one decision.
- **(open)** Which entity provides voltage (inverter AC or grid), and is there a lower hard-stop threshold (for example 200 V → pause all)?

### 3.4 Sensor health and battery floor
- **(decided, round 7)** Each profile declares the inputs it **requires**. If any of them is unknown, don't increase power. If it stays unknown for a long time, step down or pause (the meter-lost alert: warning at 10 min, step down at 30).
  - Solar-follow requires **grid import**. It does not need the solar sensor (§6.2).
  - This replaces today's rule "solar sensor unavailable → minimum", which in Solar-follow watches the wrong sensor.
- Battery floor (Setup B, open): below the floor, **pause**, don't go to minimum.

> **Notes:**

---

## 4. Energy limits

### 4.1 What "available energy" means
- **Setup A (no battery):** the measured solar is **not** reliable, because the inverter throttles when load is lower than PV. So the controller does **not** use "production minus house consumption". It steers on **grid import** instead (§6.2).
- **Setup B (battery, open):** the battery absorbs swings, so grid import stays near 0 W, and the signal would likely be the battery's charge/discharge power (§6.3).

### 4.2 Battery
- Open (§6.3). The battery floor stays a hard limit.

### 4.3 Grid
- **Assumption: a grid connection always exists.** It absorbs fluctuations, such as a miner ramping, a cloud, or the inverter throttling.
- The question is never "can we import?" but "how much import do we aim for?". Solar-follow sets that as an **import target** (§6.2).

### 4.4 Solar forecast
- The forecast sensors (now / next hour / energy left today) feed the AI and the log.
- Candidate uses for the rules:
  - **Sunrise (§5.4):** step up ahead of the sun when the forecast says production is rising.
  - **Late afternoon:** don't step up when production is about to fall.
  - **Throttle detection:** actual PV well below *forecast PV now*, with no import, means headroom exists.
- **(decided, round 2)** The rules use the forecast as **one of the signals that confirm sunrise and sunset** (§5.4). Other uses stay with the AI for now.

> **Notes:**

---

## 5. Control dynamics (important)

### 5.1 Every power change costs a restart
- **Measured (2026-10-06):** changing a miner's power limit restarts it. On a step it has run before, it is back at full hashrate in **3–4 minutes** (round 7). The ~50 min tuning only happens on a step it has never run (`situation.tuning`, an exception).
- **(open)** The cost of Braiins OS pause / resume, and how long the temperature takes to come back (§9).
- So:
  - A change costs about 3–4 min of hashing on that miner. The cost scales with the miner's **current** power, not with the step size.
  - While a miner ramps, readings are misleading: its draw is low, so import looks lower than it really is.
  - Reacting to short-term changes (a passing cloud, a kettle) loses more than it gains.

### 5.2 Rules that follow
- **(decided)** **Minimise restarts per decision.** Prefer one miner with a big step over several miners with small steps. A decision may still touch several miners when needed (for example pause one and raise another, §5.3), and safety reasons may always touch several.
- **Minimum step: 200 W.** A change is **as large as the surplus or deficit supports**, never smaller than 200 W. If the surplus or deficit is less than 200 W, hold.
- **(proposed, round 5) Step size when the surplus is hidden (Setup A):**
  - Step **down** by the measured deficit (import − target).
  - Step **up** by the measured export if there is any. Otherwise use the forecast headroom (*forecast PV now* − *actual PV*) when configured. Otherwise use a fixed **probe step** (default 400 W).
- **Ramp lock:** while any miner is ramping, the decision is "hold". A miner has finished ramping when its draw is within X % of its new limit, or a timeout passes (**about 4 min**, round 7). **(decided, round 7)** A `ramping` reason shows in the log why it held.
- **(decided, round 7)** **Minimum hold time per miner** after a change, except for safety. It is the temperature settling time; the value is open (§9). ~~10–15 min~~ is retired.
- **(decided, round 7)** **Smooth the energy inputs** (import, solar): decide on values averaged over **3 minutes** rather than the latest sample. Temperature isn't smoothed (round 4).
- **(proposed, round 5)** **Step down slowly, step up promptly.**
  - Step **down** only after the deficit has lasted longer than the **cloud tolerance**, so a passing cloud costs no restarts.
  - Step **up** once the surplus has held for the smoothing window.
  - Fast reactions are for safety only (§3.3, §3.4). The evening decline is handled by sunset mode (§5.4).

### 5.3 Allocation across miners: spread, pause, resume
- **(decided)** A miner below the **pause threshold** isn't worth running. The threshold is the lowest power step, **900 W** (round 7). It is **paused** (Braiins OS pause), not left idling at minimum.
- **(decided)** Spreading the load over more miners in the efficient range (~1000–2000 W) is better than running fewer miners harder.
- **(decided)** Consolidating (pausing one miner and raising the others) restarts every miner involved, and a passing cloud can force the reverse change soon after. So consolidating needs a **sustained** deficit, not a momentary one.
- New actions: `pause` and `resume` alongside `increase` / `reduce` / `hold`. Each action carries a **target wattage**.
- **(decided, round 2)** There is no exact deterministic answer for which miners to change. The rule below is the **baseline**. The AI may propose a different allocation (for example spread a cut at the end of the day, concentrate it during a cloud), and the logs show which choice worked better (§7).
- **(suggestion)** Baseline allocation rule that keeps restarts low but drifts towards a spread over time:
  - **Step up:** if the surplus is enough to run a paused miner at the threshold or above, **resume** it. Otherwise give the whole surplus to the running miner that is lowest in the efficient range and has thermal headroom. Push any miner above ~2000 W only when every running miner is already near the top of the range or too warm.
  - **Step down (proposed, round 5):** take the whole deficit from the **highest-power miner that stays at or above the pause threshold** after the cut. Only if no running miner can absorb it, **pause** the lowest-power one, and let the next step-up rebalance. Hot miners aren't chosen here. They already step down through the temperature rule (§3.2).
- **(open)** Is efficiency really worse above ~2000 W on these miners? That decides whether "prefer more miners" still holds when the sun is strong enough to run every miner at 2000 W or more.
- **(decided, round 7)** Pause threshold = lowest step (900 W). A deficit must outlast the cloud tolerance before a pause.

### 5.4 Transitions: sunrise, sunset, clouds
- **(decided)** Temporary import is acceptable while production changes quickly.

**Detecting sunrise and sunset (decided, round 2):** several signals have to agree:
- measured solar (or actual PV) rising or falling;
- the time of day against the expected sunrise or production window. **(suggestion)** Home Assistant's built-in `sun.sun` entity gives solar elevation and the next rising and setting times, so this needs no new config;
- the solar forecast (now vs next hour).

When most of them agree, the controller is in **sunrise mode** or **sunset mode**.

**Sunrise mode:**
- **(open)** What starts the first miner, and at what power. This will be set from the rise-speed study below.
- **(decided)** The sun can rise fast, so sunrise mode **relaxes the minimum hold time**. The ramp lock still applies: never decide while a miner's readings are still settling.
- **(decided, round 7)** In sunrise mode, **resume the next paused miner at the lowest step** (900 W) rather than raising the one that just started. Production keeps rising, so raising one miner again and again costs a restart each time. Start the next one when there is budget for its lowest step.

**Sunset mode:**
- Step down and pause in order, one miner at a time, without chasing every dip.
- **(suggestion)** Whatever the triggers turn out to be, the stop trigger must sit **below** the start trigger. Without that gap, the last miner would start and stop repeatedly when production hovers near one value.

**Clouds** (neither mode): don't step down until the deficit has lasted longer than the **cloud tolerance** (§5.2, round 5). Its default comes from the study below.

- **(decided, round 3)** Import during a transition is accepted, even for several minutes or longer. Sunrise and sunset mode have no give-up rule. Once a mode ends, the normal Solar-follow band applies again, so a wrong forecast (for example fog all morning) is corrected by the normal step-down.

**(open) Study: how fast does production rise and fall?** This sets the start and stop triggers, how far hold times relax in each mode, and how quickly miners are added or removed.
- **(suggestion)** Source: Home Assistant's recorder history for the solar or actual-PV sensor and the forecast, over a few weeks of clear, cloudy and mixed days.
- **(suggestion)** Measure:
  - watts gained per 10 minutes in the morning, and lost in the evening;
  - how long it takes from first production to "enough for one miner", then to "enough for two";
  - how long typical cloud dips last.
- **⚠ Throttling distorts the history.** When no miner is running, the inverter throttles to the house load. The recorded solar then shows the house load, not the sun. Use only periods with import (for example while the fixed-hour schedule had the miners running), or the forecast curve, or *actual PV* when it isn't limited. Otherwise the study measures the house and not the sun.

> **Notes:**

---

## 6. Use cases and profiles

All profiles obey §3 (hard limits) and §5 (dynamics).

### 6.1 Setups

| Setup | What it has | Main problem | Status |
|---|---|---|---|
| **A. Solar, no battery** | PV + grid | The inverter throttles when load is lower than PV, so the available extra sun is hidden. | **current focus** |
| **B. Solar + battery** | PV + battery + grid | Deciding when energy goes to miners vs the battery, and protecting battery cycles. | open (§6.3); no battery at this farm |

A second axis is the **export policy**. Setup A as described assumes zero-export inverters (the reference farm): throttling hides the surplus, so a small steady import is the proof that it is used. With export allowed, the export reading already shows the surplus and Solar-follow steers on the export instead of an import target (not designed yet).

| Profile | Setup | Small steady draw from | Status |
|---|---|---|---|
| Solar-follow | A | grid | decided, current focus |
| Solar-follow with battery | B | battery | open |
| Solar + battery | B | battery, spent within limits | open |

**(suggestion, open with Setup B)** The setup is a config choice, set once ("Do you have a battery?"). The profile selector then only shows profiles that fit it.

### 6.2 Setup A: Solar-follow (replaces Solar-max)

The small steady draw comes from the **grid**.

- **(decided)** **Aim for a small, constant grid import.** A small import proves that all available solar is used. At zero import, a throttled inverter gives no sign that more is available.
  - Import well below target, or none at all: there is spare (possibly throttled) solar. **Step up.**
  - Import well above target: not enough sun. **Step down.**
  - Import inside the band: **hold**.
- This is a rough controller, not a precise one. The band should be wide.
- **(decided)** The band must be **wider than the minimum step (200 W)**. Otherwise every step overshoots the band, the next step reverses it, and the controller oscillates. Config validation should enforce this.
- **(open)** Defaults for the import target and band. Tune them from the logs. The earlier example (target 100 W, ±150 W) had no basis, and its lower edge (−50 W) wrongly put 0 W import inside "hold".
- **(suggestion)** Second signal, when configured: actual PV well below *forecast PV now* means the inverter is throttling (§4.4).
- Grid-independent is dropped as a profile. A low import target in Solar-follow covers it.

### 6.3 Setup B: battery profiles (open)

**All battery configuration is open.** The farm has no battery, and the effort goes into the decision making for Setup A. Nothing here is decided; it only records the shape of the set from round 6, to be worked out when Setup B is picked up.

- **Solar-follow with battery.** Probably the same controller as §6.2, with the small steady draw coming from the battery instead of the grid (battery discharge as the signal, grid import near 0 W).
- **Solar + battery.** Uses the battery within predefined limits (for example a SOC band), aiming for the best efficiency (J/TH) while keeping a reserve.
- Points raised so far, to re-check then: a small constant discharge means continuous shallow battery cycling; discharge target and band defaults; an `efficiency` reason in the AI vocabulary; the earlier optimiser ideas (morning discharge, charging held back until the peak, forecast-driven battery spend, efficiency-ranked miners); the battery floor as a hard limit.

### 6.4 Dropped: Full power (round 6)

~~Raise each miner as far as temperature allows, ignoring the sun and the battery.~~ Running everything at maximum needs no decisions, rules or AI, so it is not a profile. A user who wants it sets the miners by hand.

### 6.5 Mapping from the current profiles

| Current (`const.py`) | New | Setup |
|---|---|---|
| `solar_max` | Solar-follow | A |
| `battery_focused` | battery profiles, open (§6.3) | B |
| `grid_independent` | dropped. Use Solar-follow with a low import target | A |
| `grid_agnostic` | dropped (with Full power) | — |

**(decided, round 7)** Rename `solar_max` → `solar_follow`. Installs stored on `battery_focused`, `grid_agnostic` or `grid_independent` are migrated to `solar_follow`, and the migration is logged once.

> **Notes:**

---

## 7. AI advisor

- **(decided)** The AI does not control the miners. It gets the same inputs as the rules, plus the rule proposal, and gives its own proposal.
- **(decided)** Rule and AI proposals are logged side by side. A later view shows **disagreements**: when they differed, and the inputs at that moment. This is the evidence for improving the rules.
- **(decided)** The answer stays multi-miner. It needs the new vocabulary: `pause` / `resume` / `increase` / `reduce` / `hold`, a **target wattage** per miner, and a `ramping` reason.
- **(suggestion)** The prompt includes recent history (last change per miner and how long ago, which miners are ramping). Without it the AI can't respect the ramp lock or the hold times.
- **(decided, round 2)** Request budget: use a cheap paid model, so free-tier limits don't apply. At 60 s that is about 1,440 requests a day.

**Roadmap: the AI gets more authority over time (decided, round 2).** The rules are the starting approach. Later, more decisions move to the AI: more factors, the trend, and lessons from the history. **(suggestion)** Stages:
1. **Advisory** (today). The AI's proposal is logged next to the rules' proposal.
2. **The AI chooses allocation.** The rules still decide safety, the dynamics gate and the watt delta. The AI picks *which* miners change and how (spread or concentrate, pause or reduce).
3. **The AI also decides the watt delta**, inside the rules' envelope.

In every stage, safety (§3.3–3.4) and the ramp lock still sit **on top of** the AI. They act as a guard that filters its answer, never as a suggestion it may ignore.

- **(decided, round 3)** **"Learning from the logs" needs outcomes in the log.** Today `ai_log.jsonl` records the inputs and the proposals, but not what happened next. Once decisions are applied, each record needs follow-up fields: what was applied, how many restarts it caused, the import and hashrate over the next N minutes, and whether it was reversed soon after. Only then can a log entry be scored as a good or bad decision.
- **(open)** How does the AI "learn": past examples placed in the prompt, a summary of statistics, or offline analysis that you turn into rule changes? The first is the simplest. Its cost grows with prompt size.

---

## 8. Settings implied by the above

All **(suggestion)** until agreed:

| Setting | Example default | Basis | Section |
|---|---|---|---|
| Power steps | 900 to 2,500 W in 200 W steps (decided, round 8) | miner type; reference farm (S9) | 5.1 |
| Target temperature | 60 °C (decided, round 8) | cooling; reference farm (immersion) | 3.2 |
| Temperature tolerance (above target) | 10 °C (decided, round 8) | cooling; reference farm (immersion) | 3.2 |
| Voltage sensor entity | — | site | 3.3 |
| Required-input gap before step-down | 10 min warning, 30 min step down (meter-lost alert) | generic default | 3.4 |
| Low-voltage threshold | 210 V | site (grid); configurable, default 210 V from the reference farm's 230 V supply | 3.3 |
| Voltage debounce window | 60 s | generic default | 3.3 |
| Minimum power step | one step (200 W on the reference farm) | derived: one configured step | 5.2 |
| Ramp timeout | 4 min (decided, round 7) | miner type; reference farm (S9, Braiins OS) | 5.2 |
| Minimum hold time per miner (temperature settling) | open, §9 | miner type and cooling | 5.2 |
| Tuning window (only for a step never run, `situation.tuning`) | 50 min | miner type; reference farm (S9, Braiins OS) | 5.1 |
| Input smoothing window (energy only) | 3 min (decided, round 7) | generic default | 5.2 |
| Cloud tolerance (deficit time before a step-down) | from the §5.4 study | site | 5.2, 5.4 |
| Probe step (step-up when surplus is hidden) | 400 W | derived from the power steps | 5.2 |
| Pause threshold | lowest power step (900 W on the reference farm, decided round 7) | derived: the lowest configured step | 5.3 |
| Efficient range, upper end | 2000 W | miner type; reference farm (S9) | 5.3 |
| Setup: has battery | yes / no | site | 6.1 |
| Setup: export policy | zero export / export allowed | site | 6.1 |
| Import target and band (Solar-follow) | tune from logs, band wider than one step | site; band derived: wider than one step | 6.2 |
| Schedule automation entity (handover warning) | — | site | 2.1 |
| Start / stop triggers for sunrise and sunset | from the §5.4 study | site | 5.4 |

---

## 9. Open questions (round 7)

Resolved: round 1 in §0, round 2 in §0.1, round 3 in §0.2, round 4 in §0.3, conflicts in §0.4, profiles in §0.5, the blockers for Solar-follow in §0.6.

**Needed for Solar-follow, but it can start with a default and be tuned:**
1. **Rise and fall speed study.** How fast does production rise in the morning and fall in the evening? It sets the start and stop triggers, the pace in each mode and the **cloud tolerance**. Watch for throttled history. (§5.4)
2. **Temperature settling time.** How long after a change until the temperature means something again? It sets the minimum hold time. Measure from the 2026-10-06 changes. (§3.2, §5.2)
3. Import target and band defaults for normal (non-transition) operation. 400 W is in the code. (§6.2)

**Not blocking Solar-follow:**
4. The cost of Braiins OS pause / resume. (§5.1)
5. Is efficiency worse above ~2000 W? No longer moot: the steps go up to 2,500 W since round 8. (§5.3)
6. Voltage sensor source and the hard-stop threshold. (§3.3)
7. How does the AI learn from the history: examples in the prompt, statistics, or offline review? (§7)
8. **When does applying start?** What evidence is enough to switch off the schedule and go live? (§2.1)

---

## 10. Out of scope for now

- Electricity tariffs and time-of-use pricing. Note that Solar-follow deliberately buys a small, steady amount of grid power, so tariffs will matter eventually.
- Pool or hashprice economics, and choosing which miner by profitability.
- All battery configuration and the battery profiles (§6.3). The farm has no battery.
- Actually *applying* proposals. This doc defines the decision; applying it is a separate step.

---

## 11. Code structure (proposal)

**Idea:** a common layer for everything that applies to every profile, plus one small strategy per profile.

```
custom_components/solar_smart_miner/decision/
  __init__.py            # build_decision(): runs the pipeline below
  common.py              # safety, dynamics gate, allocation, apply rules
  solar_follow.py        # Solar-follow (§6.2)
```

**Pipeline, run every decision cycle:**

1. **Safety (§3.3, §3.4).** Voltage, required-sensor health, battery floor.
   - It may change several miners at once and skips the queue.
   - Miners it touches are removed from the rest of the cycle.
   - Temperature is **not** here (§0, point 6).
2. **Dynamics gate (§5.2).** Ramp lock, minimum hold time, input smoothing. If a miner is ramping, the result is `hold` and the profile isn't asked.
3. **Profile strategy.** Each profile answers one question: *how many watts should the miners go up or down by?*
   - Solar-follow: from the import vs target band, with the sunrise and cloud tolerance (§5.4).
4. **Allocation and temperature (§3.2, §5.3).** Common to all profiles. Turn the watt delta into per-miner actions (pause / resume / increase / reduce), using the fewest restarts. Temperature blocks step-ups and forces step-downs here, at the normal pace.
5. **Apply rules.** Minimum step, clamp to the power range, round. The result is a `Decision` that uses the same vocabulary as the AI answer.

**Why split this way:**
- With the battery profiles open, one strategy is left, and it reduces to "watt delta". Allocation, which miner to change and when to pause, lives in common. That answers the earlier open question about where "which miner first" belongs.
- Battery profiles (§6.3) are left out until Setup B is picked up.
- The AI prompt can follow the same split: a common section (limits, dynamics, output format) plus one profile section.
