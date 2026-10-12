---
title: "feat: Telegram: the connection, four day messages, then alerts"
type: feat
date: 2026-10-09
updated: 2026-10-12  # owner's request: the four day messages (U4-U5) replace per-change messages and the 21:00 summary; owner's answers
status: U1-U5 done (0.8.3, 0.8.4, 2026-10-12); U6 open, U7 deferred
related_plans:
  - docs/plans/2026-10-06-001-feat-semi-automatic-apply-plan.md  # control.py, apply-failed notification
  - docs/plans/2026-10-09-001-refactor-decision-pipeline-plan.md  # alerts that will push once built
---

# feat: Telegram: the connection, four day messages, then alerts

## Summary

The owner wants the farm to **report to them on its own**, through Telegram, without anyone opening the
dashboard. Two steps:

1. **Finish the Telegram connection** (U1-U3). The bot token and chat ID go in their own Configure tab,
   are checked with a test message on save, and the integration gets one way to send (`notify.py`).
   Today the options flow asks for the token and chat ID (`CONF_TELEGRAM_TOKEN` / `CONF_TELEGRAM_CHAT_ID`
   in `config_flow.py`), but nothing reads them.
2. **Four messages that tell the story of the day** (U4-U5), each at most once a day:

| # | When | First sentence | Second sentence |
|---|---|---|---|
| 1 | Morning: the first miner starts mining | "☀️ Production is up: Miner 1 started at its lowest step, {limit} W ({time})." | The forecast for the rest of today (kWh) and the next hour (W). |
| 2 | Every miner runs at the top power step, settled and within the temperature band | "⚡ All {n} miners run at their top step, settled ({time})." | How long it took from the first start: "{duration} after the first start ({first_start})." |
| 3 | After message 2: the first time the farm steps a miner down | "🌥 Stepping down: Miner 2 {old} → {new} W ({reason})." | Forecast compared with the panels now: "Forecast {forecast} kW, panels give {pv} kW ({pct}%)." |
| 4 | Evening: the last miner stops | "🌙 Day done: the last miner stopped at {time}." | Summary: PV energy today, the miners' energy and how much of it came from the grid, mining hours, and the morning forecast against what came. |

Messages 1-4 only **observe** the farm. They change no plan and add **no decision rule** (the rule count
stays the same). They reuse the existing definitions instead of making their own: *settled* is
`control.Settling` / `draws_its_limit`, *sunset* is `transition.Transition`, *mining* is
`control.is_mining`, *the temperature band* is the setting the `limits` group uses.

The alerts in `knowledge/alerts.yaml` marked `push` come after (U6). Acting from Telegram stays deferred (U7).

---

## Progress

- [ ] **U1**: `notify.py`: send to Telegram through the Bot API, never raise, never log the token
- [ ] **U2**: One `Notifier` for all notifications; existing ones routed through it, `apply-failed` pushed
- [ ] **U3**: A Telegram tab in Configure; the token and chat ID checked on save (test message)
- [ ] **U4**: `day_report.py`: follows the day (first start, all at max, first step down, last stop) and
      counts the energy; kept across HA restarts
- [ ] **U5**: The four day messages, on/off on the Telegram tab
- [ ] **U6**: Alerts with `push` go to Telegram; critical ones repeat until acknowledged
- [ ] **U7** (deferred): Inline buttons (Acknowledge, Pause all, Resume) answered from Telegram

Retired from the 2026-10-09 version: *per-change decision messages* (one message per cycle with changes; too
chatty for the owner, and message 3 covers the change they care about) and the *21:00 daily summary*
(it is now message 4, sent when the day actually ends).

---

## Owner's answers (2026-10-12)

| Question | Answer |
|---|---|
| Where does the day's PV energy come from? | From sensors already configured, no new setting. The **forecast** sensors (power now, next hour, energy still to come today) give the forecast; the **actual PV output** sensor, added up every poll, gives what was produced. |
| Forecast vs actual on a zero-export site, where the panels are held to the load? | **Always show both**, without a note or suppression. The forecast is a reference; with no or little import, inverter throttling is the obvious reason the actual is lower, and the reader knows it. Throttling is the reference farm's setup, not the general case. |
| Does a step-down for temperature or voltage count for message 3? | **Yes**: the first step-down of any kind after message 2, quoting the plan's reason. |
| A midday cloud stops every miner. | **Not reporting's job.** The day messages ignore it (message 4 needs sunset, message 1 is once a day). Unexpected events like this belong to **Alerts** (U6). |
| Language and units | English, W and kWh, the HA time zone, 24 h clock (as the dashboard). |
| Names and numbers | Miner names come from hass-miner, steps and limits from Configure. The plan, the code and the tests use no model, firmware or farm figure: the top step is the highest configured step inside the miner's range. |
| Manual mode | The messages follow what the miners **do**, not who ordered it, so they work in Manual and Automatic alike. |

---

## Decisions

| Question | Decision | Why |
|---|---|---|
| Call the Bot API directly, or go through HA's `telegram_bot` / `notify` services? | **Directly**, with HA's shared `aiohttp` session (`async_get_clientsession`, as `ai.py` does) | The token and chat ID are in our options; the owner doesn't set up another integration. A `notify.*` target can come later if someone asks. |
| Which messages go to Telegram? | Each event has a `push` flag; only pushed events are sent. The persistent notification stays for failures and alerts; **day messages are Telegram only** | Day messages are a report, not something to act on; they would only clutter the HA notification list. |
| Message format | Plain text, `parse_mode` unset; first character an emoji marker | No escaping bugs with miner names or reasons that contain `_`, `*`, `<`. |
| Day messages from events or from state? | **From state**: `day_report` looks at each coordinator snapshot, not at commands | Works in Manual and Automatic, catches a miner started by a schedule automation, and survives a lost command event. |
| Once a day | `day_report` keeps the day's date and which messages went out, in an HA `Store` (`.storage/solar_smart_miner.day_report`), saved when it changes | An HA restart at noon must not send "production is up" again or lose the first-start time message 2 needs. |
| When does a day begin? | At the first poll on a new date. A day picked up while miners mine skips only the greeting | Review, 2026-10-12: tying it to the sun rising lost the whole day when HA came back in the afternoon. |
| Repeats of failures and alerts | Per-key cooldown in memory (`cooldown_min` from the alert, 15 min default); an HA restart resets it | A duplicate after a restart is acceptable. |
| A failed send | Logged once per failure streak at `warning`, never raised, never retried in a loop; a day message that fails is **not** re-sent | A Telegram outage must not break a cycle or a command; a morning message at noon is worse than none. |
| Token in logs | Never: errors are logged with the status and Telegram's `description`, not the URL | The token gives full control of the bot. |
| Where the settings live | Their own **Telegram** item in the options menu (`edit_telegram`), next to *AI (OpenRouter)* | Settings for one channel stay together. Same pattern as `edit_ai`. |
| Telegram not configured | Everything works as today; `day_report` still follows the day (cheap, and testable) but sends nothing | Telegram stays optional. |

---

## Units

### U1: `notify.py`: Telegram sender

**What:** `async_send_telegram(session, token, chat_id, text) -> str | None` posts to
`https://api.telegram.org/bot<token>/sendMessage` (`chat_id`, `text`, `disable_web_page_preview`), with a
10 s timeout. It returns `None` on success or a short reason ("HTTP 401: Unauthorized", "timeout"). It never
raises. Text is cut to Telegram's 4096-character limit.

**Tests** (`tests/test_notify.py`, `aioclient_mock`, no real network):
- a successful send posts the chat ID and text, and returns `None`
- 401, 400 (wrong chat ID), a timeout and a connection error each return a reason and don't raise
- the token appears neither in the returned reason nor in the log (`caplog`)
- an over-long text is cut

An autouse fixture in `tests/conftest.py` fails any unmocked call to `api.telegram.org`, as for OpenRouter.

### U2: one `Notifier` for every notification

**What:** a `Notifier` owned by the coordinator, with
`notify(key, title, message, *, severity="info", push=False, persistent=True, cooldown_min=None)`:
- if `persistent`, creates or replaces the persistent notification `f"{DOMAIN}_{key}"` (today's behaviour)
- if `push` and Telegram is configured and the key is not in its cooldown, schedules the send as a
  background task (`hass.async_create_background_task`), so callers never wait on the network
- `dismiss(key)` removes the persistent notification; the cooldown stays, so an event that comes and goes
  is pushed once per cooldown (review, 2026-10-12). A push that didn't go out doesn't use the cooldown

Route the existing calls through it:

| Today | Key | Push |
|---|---|---|
| `control.py` `_notify_failure` (`control.apply-failed`, critical) | `apply_failed_<miner>` | **yes**, cooldown 15 min |
| `coordinator.py` `_notify_changed` (proposal changed after Apply) | `apply_changed[_<miner>]` | no: the owner is at the dashboard |
| `coordinator.py` `_notify_rules_failed` | `rules_failed` | **yes**, cooldown 60 min |
| `coordinator.py` Apply all skipped | `apply_all_skipped` | no |
| `button.py` Add to Dashboard | unchanged | no |

`control.py` gets the notifier passed in (instead of calling `pn_create` itself), so it stays testable.

**Tests:** the existing persistent-notification tests pass unchanged. A failed command with Telegram
configured sends one message; a second failure inside the cooldown sends none. Without Telegram nothing is
sent. A send that fails doesn't fail the command or the cycle.

**KB:** `alerts.yaml`, `control.apply-failed.note`: pushed to Telegram when configured.

### U3: a Telegram tab, checked on save

**What:** the options menu gets **Telegram**
(`menu_options: [edit_sensors, edit_miners, edit_ai, edit_telegram, edit_settings]`), a step
`async_step_edit_telegram` built like `async_step_edit_ai` with its own `_telegram_schema(options)`:
- bot token (password field, suggested value, blank = off) and chat ID, **moved** out of `_options_schema`
  with their handling in `async_step_edit_settings` (`config_flow.py:645`)
- U5's *Day messages* switch joins this tab
- `strings.json` and `translations/en.json`: menu label, step title, and a description of how to get a
  token (@BotFather → `/newbot`) and the chat ID (send the bot a message, then open
  `https://api.telegram.org/bot<token>/getUpdates`, or use @userinfobot); field labels moved from `edit_settings`

The option keys (`telegram_bot_token`, `telegram_chat_id`) stay the same, so saved values carry over without
a migration. Saving *Settings* keeps the stored Telegram values (today it rewrites them from the form).

Saving the tab with a token and chat ID that changed sends "✅ Solar Smart Miner is connected to this chat."
If that fails, the form comes back with `telegram_failed` (the reason as a placeholder) and nothing is saved.
Blank token **and** chat ID turns Telegram off; only one of them filled is an error.

**Tests** (`tests/test_config_flow.py`): the menu lists `edit_telegram`; the step shows and saves the values;
*Settings* has no Telegram fields and keeps the stored ones (update the existing Settings tests that set
them); a valid pair saves and sends one message; 401 or a wrong chat ID shows the error and keeps the old
options; unchanged fields send nothing; one field filled gives an error.

**Release:** U1-U3 together are the first release (0.8.3): the keys can be installed and checked.

### U4: `day_report.py`: following the day

**What:** a `DayReport` the coordinator calls once per cycle, after the decision, with the snapshot, the
controller's settling records, the transition and the options:
`events = day_report.update(now, snapshot, settling, transition, temp_band) -> list[DayEvent]`.
It holds the day's state, saved in a `Store` whenever it changes:

```
date, first_start_at, first_miner, all_max_at, first_step_down_at, last_stop_at,
sent: {morning, all_max, step_down, evening},
forecast_at_start_kwh,                    # forecast_remaining_kwh when the first miner started
pv_kwh, miners_kwh, import_kwh,           # integrated per poll (trapezoid over the real interval)
mining_minutes: {miner_id: minutes}
```

The events, each **once a day**, in this order:

| Event | Condition |
|---|---|
| `morning` | No miner was mining this day yet, and one now is (`is_mining`). |
| `all_max` | Every available miner mines at the **top configured power step**, none is settling (`Settling` absent or `done`), and each hottest chip is within the temperature band (target + tolerance). Seen on two polls in a row, so a passing reading doesn't count. |
| `step_down` | After `all_max`: a miner's limit is lower than when last read, or it stopped: not mining for two polls and not restarting after a limit change (a restart reads as a stop for a minute or two). |
| `evening` | After `morning`: no miner mines, and it is sunset (`transition.sunset`) or the sun is below the horizon. |

Energy, each poll, over the time since the last poll (gaps over 5 min are not filled in):
- `pv_kwh`: the actual PV output (`pv_power_w`, else `solar_production_w`) integrated
- `miners_kwh`: `miner_consumption_sum_w` integrated
- `import_kwh`: the grid import (`-grid_net_w` when below zero) integrated, only while a miner mines

A new day (see *When does a day begin?* under Decisions) resets the state. If the integration loads in the middle of a day with a
miner already mining and no state for today, `morning` is marked sent without sending (no "production is up"
at noon after a fresh install).

**Tests** (`tests/test_day_report.py`, plain snapshots and a fixed clock; no HA needed for most):
- a day from the farm model (`test_farm_sim.py`'s farm) gives exactly morning → all_max → step_down →
  evening, once each
- a cloud that stops every miner at noon gives no `evening` and no second `morning`
- `all_max` waits while a miner is settling, while one is above the band, and for the second poll
- `step_down` before `all_max` gives nothing
- a restart in the middle of the day (state loaded from the `Store`) sends nothing again and keeps
  `first_start_at`
- loading mid-day with miners mining marks `morning` without an event
- the energy integration over known polls; a 10-minute gap is not filled
- one replay day from `tests/replay/` gives the events at the times the farm saw them

**KB:** not a rule, so nothing in `rules.yaml`. Add `report.day-messages` to `situations.yaml` (P3, decided, 2026-10-12): what the four messages mean and that they only observe. It tells
the AI that a sent message is not a decision.

### U5: the four day messages

**What:** the coordinator turns each `DayEvent` into a message and calls
`Notifier.notify(f"day_{event}", ..., push=True, persistent=False)`. A new switch on the Telegram tab,
**Day messages** (`CONF_TELEGRAM_DAY`, default **on**: someone who configured Telegram wants to hear from the
farm). The texts, built by small pure functions in `day_report.py` (`describe_morning(...)` and so on):

1. **Morning**
   `☀️ Production is up: {miner} started at {limit} W ({time}).`
   `Forecast for the rest of today: {remaining} kWh, next hour {next_hour} kW.`
   Without forecast entities, the second sentence is left out.
2. **All at max**
   `⚡ All {n} miners run at their top step, settled ({time}, PV {pv} kW).`
   `It took {duration} from the first start ({first_start}).`
3. **First step down**
   `🌥 Stepping down: {miner} {old} → {new} W ({reason}).` (or `stopped`), with the reason from the miner's plan
   `Forecast {forecast_now} kW, panels give {pv} kW ({pct}%).` Left out without a forecast.
4. **Evening**
   `🌙 Day done: the last miner stopped at {time}.`
   `Today: PV {pv_kwh} kWh, miners {miners_kwh} kWh ({import_kwh} kWh from the grid), mining {first_start}-{last_stop}, {hours} miner-hours.`
   `Forecast this morning {forecast_at_start} kWh.` (when known)
   When message 2 never came: `Peak: {k} of {n} miners at their top step.`

A message for a reading that is missing leaves out that part rather than writing "None".

**Tests:** each describe function with full and missing readings (forecast missing, a single
miner, a stop instead of a step); the switch off sends nothing while `day_report` still follows the day;
Telegram not configured sends nothing; a farm-model day with Telegram mocked posts exactly four messages in order.

**Release:** U4+U5 together (0.8.4).

### U6: alerts with `push`

**What:** when the alert engine raises an alert (alerts are still `proposed`, see `status_note` in
`alerts.yaml`; built in the decision pipeline plan and later), it calls `Notifier.notify(alert.id, ...)`
with `push` from the alert's `notify` field and `cooldown_min` from the alert.
- `warning` + push: one message, then the cooldown
- `critical` + push: repeats every `cooldown_min` until it resolves (`resolves_when`) or is acknowledged
- a resolved pushed alert sends one "✅ resolved" message
- acknowledgement for now: an **Acknowledge alerts** button on the hub device (`button.py`); U7 adds it to Telegram

Each alert lands with its own unit in the decision plan; its test checks that it pushes.

Events the day messages leave out belong here, for example **all miners stopped in daylight** (a cloud,
a tripped breaker, the grid meter lost). That one is a candidate for `alerts.yaml`, `warning` + push,
raised when no miner mines while the sun is up and it isn't sunset.

**Tests:** a critical alert repeats after its cooldown and stops after Acknowledge; a resolved alert sends
the resolved message once; a warning doesn't repeat.

### U7 (deferred): act from Telegram

Inline buttons (Acknowledge, Pause all, Resume) need the integration to **receive** Telegram updates
(long-polling `getUpdates` in a background task, or a webhook through HA). Only the configured chat ID may act;
every action goes through the dashboard buttons' guards and lands in the action log. Deferred until U6 is in
use and the dashboard Acknowledge proves not to be enough.

---

## Releases

| Release | Units | What the owner gets |
|---|---|---|
| 0.8.3 | U1-U3 | Telegram tab, test message on save, failed commands pushed |
| 0.8.4 | U4-U5 | The four day messages |
| next | U6 | Pushed alerts, as each alert is built |

Patch numbers only (CLAUDE.md). No entity is removed, so `RETIRED_ENTITIES` doesn't change. New options get
defaults, so an existing entry keeps working without being saved again.

## Risks

- **A wrong chat ID or revoked token** sends nothing: U3 catches it on save, and U1 logs a warning once per
  failure streak.
- **A message at the wrong moment** (a cloud read as the evening, a restart read as the morning): the
  once-a-day latch, the sunset condition and the `Store` cover the known cases; the replay test checks a real day.
- **`all_max` never comes** on a short or cloudy day: then messages 2 and 3 don't come, and message 4 says how
  far it got ("peak: {k} of {n} miners at their top step").
- **Telegram blocked or slow:** sends run in the background with a timeout and never hold up a cycle.
- **Rule creep:** `day_report` must stay an observer. It reads the snapshot and settling records and never
  writes to the decision inputs; a test asserts the decision is identical with the reports on and off.
