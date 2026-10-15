---
title: "refactor: the morning is the integration's first start; a Manual-mode reminder; the forecast is a rough figure the report checks"
type: refactor
date: 2026-10-15
origin: docs/brainstorms/2026-10-15-sun-driven-day-messages-requirements.md
builds_on: docs/plans/2026-10-15-002-feat-sun-driven-day-messages-plan.md (shipped as 0.8.7 and 0.8.8)
status: planned
release: 0.8.9
---

# refactor: the morning is the integration's first start; a Manual-mode reminder; the forecast is a rough figure the report checks

## Summary

The day's first message comes when **the integration starts a miner**, not at "the first usable
production". A miner started by hand opens nothing. In **Manual mode**, a reminder is sent once a day when
the integration first proposes starting a miner while the sun is up, since nothing will start on its own.
The forecast in the morning message is a rough figure, labelled as such, and the midnight report (0.8.8)
compares that same figure with the solar that came after it. The forecast accuracy sensor stays. The
morning loses its "PV at the lowest power step" trigger; message rules go from five to six (the
reminder); no decision rule changes.

## Where we are (0.8.8)

- **Morning** (`messages/morning.py`, decided in `messages/__init__.py` `_note_first_production`): the
  day's first usable production, while the sun is up. That is the integration's own first start, or the
  PV at the lowest power step on two polls in a row (`CONFIRM_POLLS`, `_usable_polls`), or a pickup with
  a miner mining or the PV at that step. Text: "☀️ Production is up (08:17)." and "Forecast: … kWh for
  the rest of today, … next hour."
- **The morning's forecast** (`forecast_at_start_kwh`): the last `forecast_remaining_kwh` read *before*
  first production. On a pickup it is `None`.
- **Day report** (`messages/day_report.py`): "🔮 Forecast this morning: X kWh; the panels gave Y kWh (Z%)",
  where Y is the **whole day's** solar, and `forecast_ratio()` feeds the forecast accuracy sensor. The
  solar before the morning is in Y but not in X, so the ratio leans high.
- **Peak** and **going down** come only after the morning (`test_no_peak_without_the_morning`).
- **Evening** uses `first_production_at`: "No usable production today." when it never came.
- **Manual mode** (`CONTROL_MODE_MANUAL`, the default): the decision still runs every cycle and its
  plans wait as a proposal for the Apply button; the coordinator applies them only in Automatic. The
  messages name the mode in the status lines, nothing more.

## Decisions (owner, 2026-10-15)

- **The first message of the day is the integration's first start.** A start command the integration
  sent today that the miner carried out (the command record, `kind == "start"`). Pressing Apply in Manual
  mode sends the integration's command, so it counts. A miner started by hand, at night or in daylight,
  opens nothing; who started a miner is otherwise not reported (R16 dropped).
- **A pickup still says so.** When HA starts mid-day with miners mining, "Picked up the day: N miners
  already mining." is sent once, whoever started them: it tells the owner the integration is back and
  what it found. A pickup with no miner mining waits for the integration's first start.
- **A day the integration never starts sends no peak and no going down.** They follow the morning, as
  now; on a day of hand starts only the evening and the report come. Hand starts are the exception.
- **Manual mode gets a reminder.** Once a day, in Manual mode, the first poll while the sun is up where
  the decision's proposal starts a miner. That is the moment Automatic would have started one, so it
  needs no threshold of its own.
- **The forecast is a rough indication, not something to rely on.** The morning message says so.
- **"The morning forecast" is the figure the morning message showed.** No separate measuring moment. The
  report compares it with the solar from the first start on, the same span the forecast covers ("the
  rest of today"), so the ratio doesn't lean high.
- **The forecast accuracy sensor stays**, fed by that comparison, so HA keeps its history.
- **The forecast comparison (`2026-10-15-001`) keeps its own fixed-time scoring.** It is the tool for
  choosing a forecast service, not part of the messages. Its "morning energy" metric is renamed "07:00
  day forecast" so the two aren't confused.

## Implementation Units

### U1. The day opens at the integration's first start

**Files:** `messages/__init__.py`, `messages/morning.py`, `messages/evening.py`, `messages/going_down.py`,
`messages/peak.py`, `messages/context.py` (only where they read the field), `tests/test_messages_rules.py`

**Approach:**
- Replace `_note_first_production` with `_note_first_start`. While the sun is up (or, without
  `sun.sun`, once the sunrise transition has started), the first start in today's command record sets
  `first_start_at` to the time the start was sent. The command record is the one source; mining seen on
  a miner the integration didn't start sets nothing.
- Pickup: the first readable poll since HA started (`ctx.picking_up`) with a miner mining sets
  `first_start_at` at once, marked `picked_up`. The "or the PV at the lowest step" part goes.
- Remove `CONFIRM_POLLS`, `_usable_polls` and the "PV at the lowest step" branch.
- Rename `first_production_at` to `first_start_at` everywhere. The saved-day loader (`OLD_NAMES` and the
  key mapping next to it) maps the 0.8.7 and 0.8.8 key, so a day saved before the update keeps its
  morning and isn't sent twice.
- Evening: "No usable production today." becomes "No miner mined today." (and the mode, from the status
  lines, when it isn't Automatic).

**Tests** (update the old ones, don't delete them):
- A start by hand at night sends nothing; the morning comes at the integration's first start (AE1, kept).
- A start by hand in daylight sends nothing (`test_a_start_by_hand_in_daylight_doesnt_open_the_morning`,
  kept); the integration's start later that day opens the morning.
- PV at the lowest step for an hour with no start from the integration: no morning (replaces
  `test_an_own_start_on_an_overcast_day_is_usable_production`'s PV half and the "house load at sunrise"
  case; an own start on an overcast day still opens the morning).
- The morning shows the time the start was sent (kept).
- In Manual mode, pressing Apply on a proposed start opens the morning.
- Pickup with miners mining: the morning at once, "picked up" (AE2, kept). Pickup with no miner mining:
  nothing until the integration starts one.
- Miners started by hand all day and never touched by the integration: no morning, no peak, no going
  down; the evening and the report come.
- A day saved by 0.8.8 with `first_production_at` loads with its morning sent.
- Evening on a day without a start: "No miner mined today."

### U2. The morning message: the first start, and a rough forecast

**Files:** `messages/morning.py`, `messages/__init__.py`, `tests/test_messages_rules.py`

**Approach:**
- Text: "⛏️ First miner started at 08:17 (Brod1, 900 W)." from the command record (miner and limit). On a
  pickup: "⛏️ Picked up the day at 10:08: 3 miners already mining." The farm status lines follow, as now.
- Forecast line: "Forecast, rough: about 17 kWh for the rest of today, 2.1 kW next hour." Missing parts
  are left out; no forecast sensors, no line.
- When the morning is sent, store what it showed: `forecast_at_start_kwh` = that poll's
  `forecast_remaining_kwh`, and `solar_at_start_kwh` = the day totals' `solar_kwh` at that poll. A pickup
  stores them too: the forecast shown covers the rest of the day from that poll, the span the report
  compares, so a pickup day is compared as well.
- Remove the "last forecast before production" bookkeeping.

**Tests:** the message with full, partial and missing forecast; the stored forecast equals the one in the
message; a pickup stores its forecast and the solar already counted.

### U3. The Manual-mode reminder (a new message rule)

**Files:** `messages/manual_reminder.py` (new), `messages/__init__.py` (the policy's order),
`messages/context.py` (the proposal's first start, if not already at hand), `knowledge/messages.yaml`,
`tests/test_messages_rules.py`, `tests/test_knowledge.py` (the rule ↔ entry check picks it up)

**Approach:**
- Fires once a day when all hold: the mode is Manual; the sun is up (or, without `sun.sun`, the sunrise
  transition has started); the decision's plans (`snapshot.decision.plans`) include `ACTION_START` for a
  stopped miner; the morning hasn't been sent today.
- Text: "🖐 Manual mode: the sun could run a miner now (solar 1.2 kW). Proposed: start Brod1 at 900 W.
  Nothing starts until you press Apply or switch to Automatic." Solar left out when unknown. The status
  lines follow.
- Order in the policy: before the morning, so on a poll where both could fire, the reminder comes first
  and the morning follows once Apply is pressed.
- It only reads the decision; it never changes a plan or applies one.

**Tests:**
- Manual, sun up, a proposed start: one reminder; the next polls with the same proposal send nothing.
- Automatic with the same proposal: no reminder (the integration starts the miner; the morning follows).
- Manual, sun down (a proposal at night): nothing.
- Manual, the owner presses Apply: the morning follows; no second reminder that day.
- Manual all day with no proposed start (overcast): no reminder; the evening says "No miner mined today."
- A switch from Automatic to Manual after the morning: no reminder (the day is already open).
- A restart after the reminder: not sent again (the day's state is saved, as for the other rules).

### U4. The report compares the forecast with the solar after the first start

**Files:** `messages/day_report.py`, `sensor.py` (only if the ratio's source changes shape),
`tests/test_messages_day_report.py`, `tests/test_sensor.py`

**Approach:**
- Line: "🔮 Forecast this morning (rough): about X kWh after the first start at 08:17; the panels gave
  Y kWh (Z%)." Y = day solar − `solar_at_start_kwh`.
- `forecast_ratio()` uses the same Y, so the sensor and the line can't disagree. The sensor keeps its
  name, unit and history; its meaning is documented as "after the first start".
- No forecast, no first start (hand starts only, or Manual without Apply), or the day's solar unknown:
  no line, and the sensor stays unknown for that day.

**Tests:** AE7's day with a first start at 08:17 gives the line and the ratio from the solar after 08:17;
a day with no start from the integration has no forecast line; the sensor and the line agree.

### U5. Knowledge base, requirements, plans

**Files:** `knowledge/messages.yaml`, `docs/brainstorms/2026-10-15-sun-driven-day-messages-requirements.md`,
`docs/plans/2026-10-15-002-feat-sun-driven-day-messages-plan.md`

- `message.morning`: the integration's first start while the sun is up; a pickup with miners mining says
  so; the forecast is rough. `message.manual-reminder` (new, P3, decided): once a day in Manual mode at
  the first proposed start while the sun is up. `message.day-report`: the forecast line compares from the
  first start. `date` 2026-10-15, `source` this plan; no miner, model or entity names (the shipped base).
- Requirements: R7 (morning = the integration's first start), R8 (kept), R9 (pickup with a miner
  mining), R14 ("no miner mined today"), R26 (the morning forecast is the one the message showed,
  compared from the first start), and a new requirement for the Manual-mode reminder. Under Key
  Decisions, note that the morning trigger changed on 2026-10-15 and why.
- Plan 002: mark U1–U4 shipped in 0.8.7 and U5–U7 in 0.8.8, and point here for the morning.
- Plan 001 is being written in another session (untracked): don't edit it from here. Leave that session a
  note to rename "Morning energy (today's total at 07:00)" to "07:00 day forecast" and to say it is
  separate from the messages' morning forecast.

### U6. Replays, farm model, release

**Files:** `tests/test_farm_sim.py`, `tests/test_coordinator.py`, `manifest.json` (0.8.9, last commit)

- Farm-model sunny day in Automatic: morning → peak → going down → evening → report, each once, in
  order; the morning at the first start.
- Farm-model sunny day in Manual, Apply never pressed: reminder → evening ("No miner mined today") →
  report; no morning, peak or going down.
- The 2026-10-14 night start and the 2026-10-15 pickup through a real coordinator cycle (kept).
- Full suite and the validate workflow green; release 0.8.9.

## Rule count

Message rules: five → six (the Manual-mode reminder, with its test group and knowledge-base entry). The
morning loses a trigger condition (the PV at the lowest step, with its two-poll confirmation), and the
pickup loses its PV branch. Decision rules unchanged.

## Risks

- **Upgrade mid-day.** A day saved by 0.8.8 loads under the new key (U1 test); otherwise the morning
  would be sent again after the update.
- **A day of hand starts is quiet.** No morning, peak or going down; only the evening and the report.
  Intended (owner): the integration normally starts the miners.
- **The reminder depends on the decision proposing a start.** If the decision holds back (a safety rule,
  a missing reading), no reminder comes even on a sunny morning. That is consistent: it is the moment
  Automatic would start, and Automatic wouldn't start then either.
- **Mode switches during the day.** Manual → Automatic after the reminder: the integration's start then
  opens the morning as usual. Automatic → Manual after the morning: no reminder.

## Next questions (not in this plan)

Discuss these with the owner next, one at a time:

1. **A forecast that stops updating silences "going down".** Forecast.Solar stayed at 19.6 kWh all day on
   2026-10-13 and 10-14. `going_down._declining` needs the next-hour forecast below the forecast now, so
   with a flat forecast every afternoon step down counts as a cloud and the message never comes.
2. **Forecast changes and the accuracy history.** The forecast comparison will correct Forecast.Solar's
   settings and may switch services; the accuracy sensor's history would then mix days before and
   after. Options: note the change date in the knowledge base, or reset nothing and read the history
   with it.
3. **The decision during the inverters' one-minute gaps** (`decision/safety.py` skips without PV):
   already listed as follow-up work in plan 002.
