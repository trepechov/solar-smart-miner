---
name: farm-day-report
description: Build the farm's day report (infographic PNG card + long Telegram text) for one day from the farm's Home Assistant history, and optionally send it to the Telegram group. Use when the owner asks for a day report, a day summary, "how did the farm do on <date>", or to send the report to Telegram.
argument-hint: "[YYYY-MM-DD] [send] [test]"
---

# Farm day report

Builds one day of the farm as an infographic card (1080×1350 PNG) and a long plain-text message,
and can send both to the Telegram group. It is the prototype of the integration's day report
(see `docs/brainstorms/2026-10-15-sun-driven-day-messages-requirements.md`, the day report section);
the numbers and words come from `scripts/farm_day.py`, the card from `scripts/farm_day_card.html`.

## Arguments

- a date `YYYY-MM-DD` (local farm day); none means **yesterday**. The farm's recorder keeps about
  10 days, so older days can't be built. A day in the future can't be built: say so and ask.
- `send`: also send to Telegram. Sending posts to a group other people read, so do it only when
  the owner asked for it in this request.
- `test`: mark the card and the text as a test message. Use it for every send that is not the
  real daily report.

## Steps

1. Check the inputs exist (names only, never print values):
   - `secrets/ha-agent.env` (HA_URL, HA_TOKEN) for the farm's Home Assistant (over Tailscale);
   - `secrets/farm-day.json`, the farm's entity map (format: `farm-day.example.json` next to this
     file); if it's missing, build it from the farm's `/api/states` with the owner;
   - for `send`: `secrets/telegram.env` with `TELEGRAM_BOT_TOKEN` and `TELEGRAM_CHAT_ID`. If it's
     missing, ask the owner to create it. Don't read the token out of the integration's options.
2. Build (and send, when asked), from the repo root:
   ```bash
   .venv/bin/python scripts/farm_day.py build 2026-10-12 --test     # card + text + data
   .venv/bin/python scripts/farm_day.py send 2026-10-12 --test      # the same, then photo + text to Telegram
   ```
   Output goes to `config/farm-history/reports/farm-day-<date>.{png,txt,json}` (git-ignored).
3. Look at the PNG once (Read it) for clipped labels or overlaps before sending.
4. Reply with the text, the PNG path (the full path; the owner opens it from Finder or the IDE)
   and, after a send, the Telegram status lines the script printed.

## What the figures mean

- Solar: the inverters' daily yield counters. Grid in/out: the grid meter's counters. Miners:
  each miner's energy counter (it integrates the power the miner reports).
- House & other = solar + grid in − grid out − miners: a remainder, so it carries the other
  meters' errors. Hourly values below zero are drawn as zero; the day total is not clamped.
- Miners from the sun: per 15-minute slice, the smaller of the miners' energy and the solar
  energy not exported. The rest of the miners' energy came from the grid.
- Commands: the finished commands (`(ok)`) on the integration's last-action sensor; the state
  carried over from the evening before is not counted. A flip-flop warning is 3 or more starts
  of one miner within an hour.
- The inverters' power readings are used only for the first and last production times: on the
  reference farm they have gaps, the energy counters don't.

When a figure looks wrong, check the recorder history first (`config/farm-history/`), then the
script; fix the script with a test in `tests/test_farm_day.py`.
