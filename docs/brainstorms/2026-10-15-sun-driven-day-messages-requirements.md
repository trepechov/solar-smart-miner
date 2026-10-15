---
date: 2026-10-15
topic: sun-driven-day-messages
origin_plan: docs/plans/2026-10-09-002-feat-telegram-notifications-plan.md
---

# Day messages follow the solar day, as a message policy per farm profile

## Summary

The Telegram day messages follow the solar day instead of reacting to any miner seen mining. Morning comes at the first usable production. Peak comes when the miners reach full load. "Going down" is a step down that matches the sun's decline. Evening comes when the sun is below the horizon. Each message reports the farm's status. The messages are defined as an ordered **message policy**: small message rules kept apart from the code, like the decision rules. Each farm profile has its own policy, so other farms (batteries, more miners, other loads) can get their own day story later.

At midnight a **day report** closes the story: one text message that covers the whole calendar day, night included, computed by the integration and sent automatically, or on request for a chosen day. It also tracks how close the morning's solar forecast came to what the panels gave. A prototype builds the same text (and an infographic card, deferred) from the farm's history: `/farm-day-report`, `scripts/farm_day.py`.

---

## Problem Frame

The day report (0.8.4) opens the day when any miner is first seen mining, and closes it when nothing mines and the sun is down. On the reference farm this went wrong twice in two days:

- **2026-10-14, 01:28 local:** the owner started a miner by hand at night. It mined for about 40 s. The day report sent "☀️ Production is up" and, one poll later, very likely "🌙 Day done", both at night. The real start by the integration at 08:17 then sent nothing, because the morning counted as already sent.
- **2026-10-15, 10:08 local:** HA came back after the Pi had been off overnight, and the three miners were already mining (started by hand). A day picked up with miners mining marks the morning as sent without sending it, so nothing told the owner the integration was back or what it found. The integration then raised the miners to the top step; "all at max" could only fire at about 10:52, after the last restart settled.

The integration exists to use the solar production. Miners, however, can be started by hand, in Manual mode, or before HA comes back. So the miners don't mark the solar day reliably. The sun and the production do. The four messages also live in one module, as hard-coded conditions. A farm with a battery, more miners or other loads would need a different day story, and there is no place to put one.

---

## Key Decisions

- **The sun and the production drive the messages; the miners are reported, not followed.** Every milestone is sent on time and carries the farm's status. A milestone isn't sent just because a miner started.
- **Morning is the integration's first start, not the sun's horizon.** At sunrise the panels can't run a miner. A message then says nothing useful. (Changed 2026-10-15, plan 2026-10-15-003: 0.8.7 and 0.8.8 also opened the day at the PV reaching the lowest power step. The owner wants the first message at the first start; a start by hand opens nothing.)
- **Manual mode gets a reminder.** In Manual mode nothing starts on its own, so once a day, when the decision first proposes a start while the sun is up, a reminder says so (owner, 2026-10-15).
- **Peak is the miners at full load.** It keeps today's "all at max" meaning: every miner at its top step, settled, below the temperature band, seen on two polls in a row. The owner wants to know when the farm uses all it can, not when the sun is highest.
- **"Going down" pairs a step down with the sun's decline.** A step down at noon is a cloud; the same step down in the late afternoon, with the forecast falling, is the evening trend. Only the second one is a milestone.
- **The day messages are a message policy: ordered message rules, one policy per farm profile.** The policy mirrors the decision pipeline's discipline. Each message rule has one place in the code, one test and one knowledge-base entry. It is chosen by the farm's profile, and it is changed by editing and releasing, never by a runtime setting. Today there is one profile (Solar-follow), so there is one policy.
- **The day report still only observes.** No message rule feeds the decision. The policy reads the snapshot and the decision's own definitions (mining, settled, sunset, the ladder, the band). It never redefines them.
- **The day's summary is the midnight report; the evening message only closes the solar day.** Only at midnight is the whole day known: on the reference farm most of the grid import is the house at night. So there is one day summary, one definition of the day's totals, and the evening message no longer carries a summary of its own (owner, 2026-10-15).
- **The report is computed, never written by the AI.** Every figure and every line comes from the integration's code and a fixed template, so it is right every time and goes out when OpenRouter is down. AI commentary is left for later (owner, 2026-10-15).
- **The report is text only for now.** The infographic card stays in the prototype; drawing it inside Home Assistant is deferred.
- **The day's energy is the configured power, integrated, and a short gap holds the last reading.** No new setting. On the reference farm the inverters go unavailable for about a minute every 5 minutes (133 times between sunrise and sunset on 2026-10-12, about 160 minutes in all). Counted as zero, those gaps made the day's solar 29.6 kWh; holding the last reading through them gives 42.15 kWh, where the inverters' own yield counters say 42.10 (owner's reading confirmed, 2026-10-15). The miners' energy meters are accurate: near all of a mining miner's draw, zero when paused (owner). The grid meter's power integrates close to its counter (12.8 against 13.0 kWh). A gap longer than a few polls (HA down) is not filled, and the report says the figures are incomplete. The house load is a remainder: solar plus grid import, minus export, minus the miners; on the reference farm it is usually about 11 kWh a day (owner).
- **The forecast is measured against what came, every day.** The owner wants to know how far to trust the forecast, so the report states the morning's forecast for the day against the actual solar, and the integration keeps that ratio where Home Assistant records its history.
- **A battery farm's day story waits for the battery profiles.** Until then, a farm configured with a battery gets the same policy, except that the evening message doesn't claim the day is done.

---

## Requirements

**The message policy**

- R1. The day messages are produced by a message policy: an ordered list of message rules, each with a name, a trigger condition and the text it sends.
- R2. Each farm profile names its message policy; Solar-follow names the sun-driven policy described below.
- R3. Each message rule has one place in the code, one test and one knowledge-base entry, like a decision rule.
- R4. Message rules only observe: they read the snapshot and the decision's existing definitions and never change a decision.
- R5. Each message rule fires at most once per day, and the day's sent messages survive an HA restart (as today).
- R6. The existing Telegram switch for the day messages turns the whole policy on or off.

**The sun-driven policy (Solar-follow)**

- R7. Morning: sent once at the integration's first start of the day (a Manual-mode Apply counts; a start by hand doesn't), with a rough forecast for the rest of the day and the farm's status. A day the integration never starts gets no morning, peak or going down.
- R8. Morning is never sent while the sun is below the horizon, whatever the miners do.
- R9. When HA starts during the day with miners mining, the morning message is sent at once and says the integration picked up the day, and what it found.
- R10. Peak: sent once when every miner is at its top step, settled and below the temperature band, seen on two polls in a row, with the time it took from the morning.
- R11. Going down: sent once at the first step down or stop that comes after solar noon while the forecast for the next hour is below the forecast now.
- R12. A step down or stop that doesn't meet R11 sends nothing; the evening summary counts it as a cloud step-down.
- R13. Evening: sent once when the sun goes below the horizon. It says when the last miner stopped and how many cloud step-downs the day had, and points to the report at midnight; the day's totals are in the report (R20), not here.
- R14. Evening is sent even on a day the integration started no miner, and then says so.

**The farm's status in every message**

- R15. Each message states each miner's state: mining at which limit, stopped, or unavailable.
- R16. (Dropped 2026-10-15: who started each miner. The integration starts them; anything else is the exception.)
- R17. Each message names the control mode when it isn't Automatic.
- R18. The evening message says when miners are still mining after sunset.

**A farm with a battery**

- R19. On a farm configured with a battery, the evening message gives the sunset summary but doesn't say the day is done; miners may mine on from the battery.

**The day report**

- R20. A day report covers one calendar day (local midnight to midnight). It holds the solar energy, the miners' energy and the share of it that came from the sun, the house and other loads, the grid import and export, and the share of the solar energy used on site.
- R21. It is one Telegram text message, sent automatically just after local midnight for the day that ended. The existing Day messages switch turns it on and off with the other day messages.
- R22. It can also be sent on request, for today so far or any earlier day whose readings the integration still has, from a button and from an action that takes the day and a test flag. A report sent as a test says so in its first line.
- R23. Per miner it gives the energy, mining hours, average hashrate and J/TH. It also gives the first and last production, the best 15 minutes of solar, and the commands by kind (starts, stops, limit changes).
- R24. It names a start/stop flip-flop (3 or more starts of one miner within an hour) as a warning line. It reports the flip-flop and changes nothing.
- R25. The house load is shown as a remainder and says so.
- R26. Forecast against actual: the report gives the rough forecast the morning message showed, the solar that came from the first start on (the span that forecast covers), and the ratio between them. The integration keeps the ratio for each day where Home Assistant records its history (a sensor), so its trend over weeks can be watched. Without forecast sensors the line is left out.
- R27. The latest report's text is also shown in Home Assistant (a sensor or its attribute), so it can be read without Telegram.
- R28. A reading that is missing leaves its line out. A reading missing for a short gap holds its last value; when the integration wasn't running for part of the day, the report says its figures are incomplete.
- R29. The report only observes, like the day messages: it reads energy, the miners' hashrate and the integration's own command record, and never changes a decision.

**Manual mode**

- R30. In Manual mode, a reminder is sent once a day, the first time the decision proposes starting a miner while the sun is up and before the morning: what it proposes, and that nothing starts until the owner applies it or switches to Automatic. It only reads the proposal.

---

## Acceptance Examples

- AE1. A manual start at night
  - **Covers:** R7, R8
  - **Given:** 01:28 local, the sun below the horizon, all miners stopped.
  - **When:** the owner starts a miner by hand and stops it 40 s later.
  - **Then:** no message. At the day's first usable production the morning message is sent as usual.
- AE2. HA picked up with the miners already mining
  - **Covers:** R9, R10
  - **Given:** HA starts at 10:08, after first production; three miners mine at 2,000 W, started by hand.
  - **When:** the first poll runs.
  - **Then:** the morning message is sent at once. It says the integration picked up the day and lists the three miners mining at 2,000 W. The peak follows once all three are at the top step and settled (about 10:52 on 2026-10-15).
- AE3. A cloud at noon, the evening at five
  - **Covers:** R11, R12, R13
  - **Given:** the peak was sent at 11:30.
  - **When:** a miner steps down at 12:10 (before solar noon), and another at 16:40 with the next-hour forecast below the forecast now.
  - **Then:** 12:10 sends nothing; 16:40 sends "going down"; the evening summary counts one cloud step-down.
- AE4. An overcast day
  - **Covers:** R13, R14
  - **Given:** no usable production all day; no miner mined.
  - **When:** the sun goes below the horizon.
  - **Then:** the evening message is sent and says there was no usable production today.
- AE5. Manual mode
  - **Covers:** R17
  - **Given:** the control mode is Manual and the owner runs the miners by hand.
  - **When:** any milestone comes.
  - **Then:** the message is sent on time and names Manual mode.
- AE6. A farm with a battery
  - **Covers:** R19
  - **Given:** Configure → Farm says a battery is present; two miners still mine at sunset.
  - **When:** the sun goes below the horizon.
  - **Then:** the evening message gives the summary and lists the two miners as still mining, without "Day done".
- AE7. The day report for 2026-10-12 (the reference day for the prototype)
  - **Covers:** R20, R23, R24, R25
  - **Given:** the farm's history for 2026-10-12.
  - **When:** the day report is built (by the integration, or by the prototype from the recorder).
  - **Then:** solar 42.1 kWh (07:26–18:10, best 15 min 8.2 kW at 11:00); miners 45.8 kWh, 91% from the sun (41.5 solar + 4.2 grid); house and other 9.3 kWh (8.7 from the grid); grid 13.0 kWh in, 0.0 out; 100% of the solar used on site; 70 commands (11 starts, 10 stops, 49 limit changes); one warning: Brod1 started 4× between 07:47 and 08:29.

---

## Scope Boundaries

**Deferred for later**

- The battery profiles' own message policies (when to call the day done, what the night looks like).
- Message policies for farms with more miners or other loads; the policy structure makes room for them, but none is designed now.
- Alerts for unexpected events: a miner mining at night, a cloud stopping every miner at noon. They belong to the separate Alerts work (U6 of the Telegram plan), not to the day story.

**Day report: nice to have, later** (owner, 2026-10-15)

- The infographic card as a photo with the report; the prototype draws it with headless Chrome, which Home Assistant doesn't have, so the integration would draw it itself (Pillow ships with Home Assistant).
- What the day's grid energy cost, from the farm's tariffs.
- An estimate of the bitcoin the day's hashes earned.
- A few lines of AI commentary, from the computed figures only, with every number in it checked against them, and left out when the model fails.

**Dropped from the report**

- Comparison with yesterday or a 7-day average: not of interest.
- Who started the miners, in the report and in the day messages: the integration starts them; anything else is the exception (owner, 2026-10-15).

**Outside this work**

- Any change to the decision rules: the policy only observes.
- A runtime setting to reorder or edit message rules: a policy changes by release, like the decision rules.

---

## Dependencies / Assumptions

- Solar noon and the sunset time come from `sun.sun`, which the coordinator already reads (it reads `next_setting` and `rising`).
- The forecast sensors are optional. Without a next-hour forecast, R11 falls back to "after solar noon" alone (assumption, to confirm when planning).

---

## Outstanding Questions

**Deferred to Planning**

- How "first usable production" is detected on a zero-export site: the existing sunrise transition (production rising), the integration's first start, or whichever comes first.
- Where the message rules' knowledge-base entries live (a new file next to `rules.yaml`, or `situations.yaml`), and how `tests/test_knowledge.py` checks that each rule has its entry.
- How the evening summary words a day with several cloud step-downs, and whether the peak line ("N of M at the top step") stays when the peak never came.

**Day report: to settle**

- [Planning] A bug this replaces: `day_report.py` adds a missing PV reading as 0 W (`actual_pv_w(energy) or 0.0`), so 0.8.4's evening summary undercounts the reference farm's solar by about 30%; it also counts the grid import only while a miner mines. The report's one integration rule (hold the last reading through a short gap, whole-day import) fixes both. Check what the decision itself does with the PV reading during these one-minute gaps.
- [Planning] Where the day's readings live: the integration's own per-poll totals (`day_report.py`, kept across restarts) or Home Assistant's long-term statistics for the counters, which also cover hours when the integration wasn't running. A report on request for an earlier day needs the second. Either way the figures must match AE7.
- [Planning] Which forecast value counts as "the morning's forecast": the forecast for today at the day's first poll, or the last one before the first production; and where the integration reads the forecast for the whole day (today it reads the forecast for the rest of the day).

---

## Sources

- `custom_components/solar_smart_miner/day_report.py`: today's four messages and the pickup rule (a new day with miners mining marks the morning as sent).
- `custom_components/solar_smart_miner/transition.py`: the one definition of sunrise and sunset as production changes.
- `custom_components/solar_smart_miner/decision/`: the ordered rule groups the message policy mirrors.
- `custom_components/solar_smart_miner/const.py`: `PROFILES` (one profile, Solar-follow) and `CONF_FARM_BATTERY`.
- `docs/plans/2026-10-09-002-feat-telegram-notifications-plan.md`: the original decisions this revises ("day messages from state", "a day picked up while miners mine skips only the greeting").
- `scripts/farm_day.py`, `scripts/farm_day_card.html` and `.claude/skills/farm-day-report/`: the day report prototype (the totals, the text, the deferred card, sending to Telegram), tested by `tests/test_farm_day.py`. The integration's report reuses its computation and template.
- Farm history 2026-10-13 to 10-15 (`config/farm-history/telegram-*.json`, git-ignored): the 01:28 manual start and the 10:08 pickup.
