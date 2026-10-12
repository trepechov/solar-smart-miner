# Solar Smart Miner

```
           .   .   *   .   .
         .   *           *   .
       .      \  \ * /  /      .
      .    *---  ( ☀ )  ---*    .
       .      /  / * \  \      .
         .   *           *   .
           .   .   *   .   .

   ┌────────┐  ┌────────┐  ┌────────┐
   │▓▓│░░│▓▓│  │▓▓│░░│▓▓│  │▓▓│░░│▓▓│
   │░░│▓▓│░░│  │░░│▓▓│░░│  │░░│▓▓│░░│
   │▓▓│░░│▓▓│  │▓▓│░░│▓▓│  │▓▓│░░│▓▓│
   └────────┘  └────────┘  └────────┘
        \          |          /
         \         |         /
          `--------+--------'
                   |
    ╔══════════════╧═════════════╗
    ║  ╔──────────────────────╗  ║
    ║  │   ◉              ◉   │  ║
    ║  │       ╰──────╯       │  ║
    ║  ╚──────────────────────╝  ║
    ║    ♯ ♯ ♯  B T C  ♯ ♯ ♯     ║
    ║   █████████████████████    ║
    ╚════════════════════════════╝
           ⚡ clean energy ⚡
```

A Home Assistant custom integration that runs ASIC miners on solar power. Every cycle a rule-based
controller reads the solar, grid and miner state and proposes a power step, a stop or a start for each
miner. You apply the proposal with a button, or let it apply itself. An optional AI advisor comments on
every proposal and never acts.

The integration is for any farm: miner model, power steps, temperatures and timings are settings. The
defaults come from the reference farm it was built on.

## Features

- **Solar-follow:** follows the sun on a small steady grid import, for setups without a battery
  (battery profiles come later)
- **Safety first:** the temperature band, the battery floor, low voltage and a lost grid meter are handled
  before anything else and can't be overridden, not even by pressing Apply
- **Manual or Automatic:** in Manual (the default) nothing reaches a miner until you press
  **Apply proposal**; in Automatic the proposal is applied every cycle, one miner at a time
- **AI advisor** on any OpenRouter model, free ones included; advisory only
- **Logs you can replay:** every command, decision and AI request is written to JSONL files
- **Telegram:** a command that didn't take, or rules that failed, is also pushed to your Telegram chat
  (day reports are next: [plan](docs/plans/2026-10-09-002-feat-telegram-notifications-plan.md))

## Requirements

- Home Assistant (current stable release)
- [hass-miner](https://github.com/Schnitzel/hass-miner) with at least one miner whose power limit it can set
- A solar production sensor or a net grid meter from any HA integration
- Optional: an [OpenRouter](https://openrouter.ai) API key, a Telegram bot, a battery SOC sensor, a voltage
  sensor, solar forecast sensors

## Installation

**HACS (recommended):** add this repository as a custom repository, install **Solar Smart Miner**, restart
Home Assistant, then **Settings → Devices & Services → Add Integration → Solar Smart Miner**.

**Manual:** copy `custom_components/solar_smart_miner/` into `config/custom_components/` and restart.

## Configuration

Setup asks for the solar entity (a production sensor, or a net grid meter and its sign), an optional house
consumption sensor, an optional OpenRouter key and model, and an optional battery sensor. Miners aren't
configured here: every hass-miner miner is picked up.

Everything can be changed later under **Settings → Devices & services → Solar Smart Miner → Configure**.
Saving reloads the integration.

| Section | What you set |
|---|---|
| **Sensors** | Solar / net meter, house consumption, battery, voltage, and reference sensors for the AI log (actual PV, forecast) |
| **Miner stop method** | Per miner: the relay that cuts it off (empty = the miner's own pause switch) |
| **Farm** | Inverters, export to the grid, battery, PV array, cooling, miner model and firmware, the miners' own temperature cutoff, the house load besides the miners, schedule automations, notes |
| **AI (OpenRouter)** | On / off, API key, model, seconds between requests |
| **Telegram** | Bot token and chat ID; saving sends a test message (see below) |
| **Settings** | Power steps, import range, step-down delays, temperature target and tolerance, low-voltage limit, battery floor, polling interval, control mode |

### Your farm's data

The rules are the same on every farm. What is true of **your** farm lives in two places that survive
updates:

- **Configure → Farm:** the AI gets these fields with every request; the base load is also used to estimate
  the import when the grid meter is lost.
- **`<HA config>/solar_smart_miner/farm.yaml`:** your measurements and notes for the AI, in the knowledge
  base format ([knowledge/README.md](custom_components/solar_smart_miner/knowledge/README.md)), P3 only, ids
  starting with `farm.`. It is created with an example header. A broken entry is logged and skipped.

### Telegram

1. In Telegram, message **@BotFather**, send `/newbot`, and copy the token it gives you.
2. Open your new bot and press **Start** (a bot can't write to you before that).
3. Get your chat ID from **@userinfobot**. For a group, add the bot to it and use the group's ID (it starts
   with `-`).
4. **Configure → Telegram**: paste both and save. A test message arrives in the chat; if it doesn't, the
   form says why and nothing is saved. Clear both fields to turn Telegram off.

## How it decides

### Solar-follow

Without a battery the inverters hold their output to the load (zero export), so 0 W on the meter can mean
a little sun or a lot. A small steady **import** is the only proof all the solar is used, so Solar-follow
steers on it:

- **Below the import range:** add one increment: start a stopped miner at its lowest step, otherwise raise
  the weakest running miner one step. This is also how the miners start in the morning.
- **Inside the range:** hold. Keep the range at least one power step wide.
- **Above the range:** step down as far as brings the import back, once it has stayed high for the
  **step-down delay** (longer during sunrise, when the sun catches up).
- **Sunset and night:** nothing starts or steps up; miners only step down and stop, one at a time.

**Sunrise and sunset** are periods of changing production, not times of day; `sun.sun` only opens the
window. The solar forecast is logged for reference and never decides.

### Power steps

Every power-limit change restarts a miner, and a wattage it has never run takes much longer to tune. So
limits move only between the configured **power steps**, about 200 W apart (finer steps only add restarts)
(each miner uses the steps inside its own range).

- **One miner per proposal** (safety excepted): if several restarted together, the load would drop to
  almost zero and zero-export inverters would throttle down.
- **A cut may skip steps**; a step up is always one step.
- **Even load:** once every miner runs, their limits are kept within one step of each other.
- **Ramp lock:** after any change, even one made by hand, every miner holds until the changed miner has
  restarted and draws its new limit, or the ramp time runs out. It survives a restart of HA. Safety doesn't wait.
- **Stopping:** when no step cuts enough, one miner is stopped with its relay or its pause switch, and is
  started again later as the next increment.

### Safety

- **Temperature band:** a miner whose hottest chip is above the target plus the tolerance steps down one
  step; inside the band it doesn't step up. Too hot at the lowest step is left to the miner's own cutoff.
- **Battery floor:** below it, every miner stops.
- **Low voltage** (with a voltage sensor): below the limit for the set time, the hungriest miner stops,
  then the next only if the voltage stays low.
- **What safety brought down stays down** for the step-down delay.
- **Grid meter lost:** the miners hold through short gaps. After that the import is estimated from the
  miners' draw, the base load and the actual PV, and acted on only downwards, once per outage. A longer
  outage raises a notification.

## Control mode

| Mode | What happens |
|---|---|
| **Manual** (default) | The farm gets one proposal and one **Apply proposal** button; nothing is sent until you press it. |
| **Automatic** | The proposal is applied at the end of every cycle, through the same code as the button. |

- **What you saw is what runs:** on press everything is read again; if the proposal changed, nothing is
  sent and a notification says so.
- **Only the rule plan is applied**, never the AI answer.
- **Stops and step-downs go first**, then step-ups and starts.
- **Every command is checked:** the miner must restart and draw its new limit (or stop) in time, otherwise
  it is marked failed and you get a notification.
- **Turn off fixed-hour schedule automations** before choosing Automatic, or both act on the miners.

## Logs and entities

- **Decision log** sensor: the summary, trace and per-miner proposals. **Add to dashboard** builds a card with it,
  the AI advice, the activity feed and the Apply button.
- **`<config>/solar_smart_miner/actions.jsonl`:** every command sent, refused or finished
  (**Last action** sensor).
- **`decisions.jsonl`:** every input of the decision when the proposal changes;
  `scripts/replay.py` replays it through newer code and reports changes undone too soon.
- **`ai_log.jsonl`:** every AI request and answer (**AI advice** sensor, **Ask AI now** button). Free models
  have daily request limits, so a short interval may need a paid model.

The logs rotate at 5 MB and keep two older files.

## First run

1. Stay in **Manual**. At midday, press Apply on a proposed step-down: `actions.jsonl` shows `pending`, then
   `ok` once the miner has restarted, and the farm holds meanwhile.
2. Try a step-up, then a stop and a start with your stop method.
3. Change a setting between looking and pressing: you get the "proposal changed" notification.
4. Disconnect a miner and press: `failed` and a notification.
5. Turn off the schedule automations, switch to **Automatic** and watch one cycle apply.

## Development

See [docs/quick-start.md](docs/quick-start.md) for the dev container, and [CLAUDE.md](CLAUDE.md) for how the
project is organised. Tests need no HA instance and no miner:

```bash
pip install -r requirements.txt
python -m pytest tests/
```

## Roadmap

- Telegram day reports and alerts
- Battery profiles
- Event-triggered decisions instead of polling
- Grid electricity price

## License

MIT
