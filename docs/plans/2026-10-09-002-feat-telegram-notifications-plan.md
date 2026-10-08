---
title: "feat: Telegram notifications for alerts, decisions and a daily summary"
type: feat
date: 2026-10-09
status: planned, not started
related_plans:
  - docs/plans/2026-10-06-001-feat-semi-automatic-apply-plan.md  # control.py, apply-failed notification
  - docs/plans/2026-10-09-001-refactor-decision-pipeline-plan.md  # alerts that will push once built
---

# feat: Telegram notifications

## Summary

The options flow already asks for a **Telegram bot token** and **chat ID**
(`config_flow.py`, `CONF_TELEGRAM_TOKEN` / `CONF_TELEGRAM_CHAT_ID`), but nothing reads them. The alert
catalogue (`knowledge/alerts.yaml`) marks 17 alerts `notify: [..., push]` ("push repeats for critical until
acknowledged"), and none of them has a channel to push to.

This plan gives the integration one way to notify (`notify.py`). It sends every message as an HA persistent
notification as today and, when Telegram is configured and the event is worth a push, also as a Telegram
message. It then adds the events in order of value: the existing failure notifications first, then the
decisions taken in Automatic mode, then the alerts as they are built, then a daily summary. Inline buttons
that act from Telegram come last and are deferred.

Nothing in this plan changes what the miners do. Every unit only adds messages.

---

## Progress

- [ ] **U1**: `notify.py`: send to Telegram through the Bot API, never raise, never log the token
- [ ] **U2**: One `Notifier` for all notifications; existing ones routed through it, `apply-failed` pushed
- [ ] **U3**: Telegram gets its own options tab; the token and chat ID are checked on save (test message)
- [ ] **U4**: Decision messages in Automatic mode (one message per cycle that changed something)
- [ ] **U5**: Alerts with `push` go to Telegram; critical ones repeat until acknowledged
- [ ] **U6**: Daily summary
- [ ] **U7** (deferred): Inline buttons (Acknowledge, Pause all, Resume) answered from Telegram

---

## Decisions

| Question | Decision | Why |
|---|---|---|
| Call the Bot API directly, or go through HA's `telegram_bot` / `notify` services? | **Directly**, with HA's shared `aiohttp` session (`async_get_clientsession`, as `ai.py` does) | The token and chat ID are already in our options; the owner doesn't have to set up another integration. A `notify.*` entity as an alternative target can come later if someone asks. |
| Which messages go to Telegram? | Each event has a `push` flag; only pushed events are sent. The persistent notification stays for all of them | Telegram is for "look now" events; the dashboard keeps the full picture. Matches the `notify` field in `alerts.yaml`. |
| Message format | Plain text, `parse_mode` unset; first line is a severity marker and the title | No escaping bugs with miner names or reasons that contain `_`, `*`, `<`. |
| Repeats | Per-key cooldown in memory (`cooldown_min` from the alert, 15 min default); a restart of HA resets it | Simple; a duplicate after a restart is acceptable. `system.restarted` will tell the owner anyway. |
| A failed send | Logged once per failure streak at `warning`, never raised, never retried in a loop | A Telegram outage must not break a coordinator cycle or a command. |
| Token in logs | Never: the URL is built at the call site and errors are logged with the status and Telegram's `description`, not the URL | The token gives full control of the bot. |
| Where the settings live | Their own **Telegram** item in the options menu (`edit_telegram`), next to *AI (OpenRouter)*; out of the general *Settings* form | Settings for one channel stay together, and the general form gets shorter. Same pattern as `edit_ai`. |
| Telegram not configured | Everything works as today, with only persistent notifications | Telegram stays optional. |

---

## Units

### U1: `notify.py`: Telegram sender

**What:** `async_send_telegram(session, token, chat_id, text) -> str | None` posts to
`https://api.telegram.org/bot<token>/sendMessage` (`chat_id`, `text`, `disable_web_page_preview`), with a
10 s timeout. It returns `None` on success or a short reason ("HTTP 401: Unauthorized", "timeout"). It never
raises. Text is cut to Telegram's 4096-character limit.

**Tests** (`tests/test_notify.py`, using the `aioclient_mock` fixture; no real network):
- a successful send posts the chat ID and text, and returns `None`
- 401, 400 (wrong chat ID), a timeout and a connection error each return a reason and don't raise
- the token appears neither in the returned reason nor in the log (`caplog`)
- an over-long text is cut

Add an autouse fixture in `tests/conftest.py` that fails any unmocked call to `api.telegram.org`, as is
done for OpenRouter.

### U2: one `Notifier` for every notification

**What:** a `Notifier` owned by the coordinator, with
`notify(key, title, message, *, severity="info", push=False, cooldown_min=None)`:
- always creates or replaces the persistent notification `f"{DOMAIN}_{key}"` (today's behaviour)
- if `push` and Telegram is configured and the key is not in its cooldown, schedules the Telegram send as a
  background task (`hass.async_create_background_task`), so callers never wait on the network
- `dismiss(key)` removes the persistent notification and clears the cooldown

Route the existing calls through it:

| Today | Key | Push |
|---|---|---|
| `control.py` `_notify_failure` (`control.apply-failed`, critical) | `apply_failed_<miner>` | **yes**, cooldown 15 min |
| `coordinator.py` `_notify_changed` (proposal changed after Apply) | `apply_changed[_<miner>]` | no: the owner is at the dashboard, having just pressed Apply |
| `coordinator.py` Apply all skipped | `apply_all_skipped` | no, for the same reason |
| `button.py` Add to Dashboard | unchanged | no |

`control.py` gets the notifier passed in (instead of calling `pn_create` itself) so it stays testable.

**Tests:** the existing persistent-notification tests still pass unchanged; a failed command with Telegram
configured sends one message and a second failure inside the cooldown sends none; without Telegram nothing is
sent; a send that fails doesn't fail the command or the cycle.

**KB:** in `alerts.yaml`, `control.apply-failed.note`: the push goes to Telegram when it is configured.

### U3: a Telegram tab, checked on save

**What:** the options menu (`async_step_init`) gets a fourth item, **Telegram**
(`menu_options: [edit_sensors, edit_miners, edit_ai, edit_telegram, edit_settings]`). It is a new step
`async_step_edit_telegram`, built like `async_step_edit_ai`, with its own `_telegram_schema(options)`:
- bot token (password field, suggested value, blank = off) and chat ID, **moved** out of `_options_schema`
  together with their handling in `async_step_edit_settings`
- the later Telegram options join this tab, not the general form: *decision messages* (U4) and
  *daily summary* and its time (U6)
- `strings.json` and `translations/en.json`: the menu label "Telegram", the step title and description
  (how to get a token from @BotFather and the chat ID), and the field labels moved from `edit_settings`

The option keys (`telegram_bot_token`, `telegram_chat_id`) stay the same, so values already saved carry over
and nothing has to be migrated. Saving *Settings* keeps the stored Telegram values untouched (today it rewrites
them from the form; once the fields are gone it must not blank them).

When the Telegram form is saved with a token and chat ID that changed, send
"Solar Smart Miner is connected." If it fails, show the form again with an error (`telegram_failed`, the
reason in the description placeholder) and don't save. A blank token or chat ID disables Telegram, as today.
Saving the other options doesn't send anything when the Telegram fields didn't change.

**Tests:** in `tests/test_config_flow.py`: the menu lists `edit_telegram`; the Telegram step shows the saved
values and saves them; the *Settings* form no longer has the Telegram fields, and saving it keeps the stored
token and chat ID (update the existing Settings tests that set them); a valid pair saves and sends one message; a 401 or a wrong chat ID
returns the error and keeps the old options; unchanged fields send nothing; only one of the two fields filled
gives an error.

### U4: decision messages in Automatic mode

**What:** a new option on the Telegram tab, **Decision messages** (`off` / `changes`, default `off`). With `changes`, each
coordinator cycle in Automatic mode that applied at least one command sends **one** message listing them,
for example:

```
⚙️ Solar Smart Miner: 2 changes
- S9-1: 1300 → 1500 W (export 640 W for 3 min)
- S9-3: started at 900 W (sunrise, export 1100 W)
```

It is built from the controller's command events (`on_event`, the same events as the action log) collected
during the cycle, using each plan's reason. A command that then fails is reported by U2's `apply-failed`, not
here. Manual mode sends nothing: the owner pressed the button.

**Tests:** two commands in one cycle give one message; a cycle with only holds gives none; `off` gives none;
Manual mode gives none; the text uses the plan's reason.

### U5: alerts with `push`

**What:** when the alert engine raises an alert (the alerts are still `proposed`, see `status_note` in
`alerts.yaml`; they are built in the decision pipeline plan and later), it calls
`Notifier.notify(alert.id, ...)` with `push` taken from the alert's `notify` field and `cooldown_min` from the
alert.
- `warning` + push: one message, then the cooldown
- `critical` + push: repeats every `cooldown_min` until it resolves (`resolves_when`) or is acknowledged
- when a pushed alert resolves, one "✅ resolved" message
- acknowledgement for now: an **Acknowledge alerts** button entity on the hub device (`button.py`) that stops
  the repeats of every active alert; U7 adds it to Telegram

This unit is only the wiring and the acknowledge button; each alert lands with its own unit in the decision
plan, and its test checks that it pushes.

**Tests:** a critical alert repeats after its cooldown and stops after Acknowledge; a resolved alert sends
the resolved message once; a warning doesn't repeat.

### U6: daily summary

**What:** a new option on the Telegram tab, **Daily summary** (off by default, a time, default 21:00). One message:
energy used by the miners and how much of it came from the grid, the hours each miner ran, the number of
changes applied, and the alerts raised that day. Read from the action log and the AI log the integration
already writes, and from the coordinator's counters. A day with nothing to report (no miner ran) sends one
line.

**Open:** where the daily energy totals come from (a `utility_meter` the owner sets up, or integrated by the
coordinator). Decide before building; it may be enough to start with run hours and changes only.

**Tests:** with a fixed clock (`freezer`), the message is sent once at the set time with the expected numbers
from a prepared log; off sends nothing.

### U7 (deferred): act from Telegram

Inline keyboard buttons (Acknowledge, Pause all, Resume) need the integration to **receive** Telegram
updates: either long-polling `getUpdates` in a background task or a webhook exposed through HA. Only the
configured chat ID may act, and every action goes through the same guards as the dashboard buttons and lands
in the action log. Deferred until U5 is in use and the dashboard Acknowledge proves not to be enough.

---

## Releases

Each of U1+U2, U3, U4, U5 and U6 is its own patch release (the next one after `0.7.7`). U1 and U2 go
together, because U1 alone has no caller. No entity is removed, so `RETIRED_ENTITIES` doesn't change. New
options get defaults, so an existing entry keeps working without being saved again.

## Risks

- **A wrong chat ID or revoked token** sends nothing silently: U3 catches it on save, and U1 logs a warning
  once per failure streak.
- **Too many messages:** cooldowns per key, one decision message per cycle, decisions `off` by default.
- **Telegram blocked or slow:** sends run in the background with a timeout and never hold up a cycle.
