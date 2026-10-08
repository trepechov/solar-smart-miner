# Knowledge base

What we know about this site, the miners and the control rules, written down as small,
separate facts so that the AI can be given the right ones for the situation instead of one
ever-growing prompt. `kb.py` loads the fact files at startup and adds the chosen entries to
every AI request; editing an entry takes effect after Home Assistant reloads the integration.

## Entry format

Each file holds a list of `entries`. One entry is one fact:

| Field | Meaning |
|---|---|
| `id` | Stable name, `<area>.<slug>`. Other entries and alerts refer to it. Never reuse an id. |
| `title` | A few words. |
| `statement` | The fact itself, one to three sentences, written so the AI can read it as is. |
| `priority` | P0 to P3, below. How binding the entry is. |
| `status` | How sure we are, below. |
| `source` | Who or what says so: `owner`, `requirements doc §x`, `measured: <what, when>`, `observed`, `code`. |
| `date` | When it was last checked, `YYYY-MM-DD`. |
| `tags` | Situations where it matters: `sunset`, `sunrise`, `cloud`, `night`, `midday`, `startup`, `always`, plus topics. Used to pick entries for a prompt. |
| `situations` | (optional) situations this applies in, such as `situation.sunset` or `situation.tuning`. Rules, facts and alerts name the situations they depend on; this is the one place to edit. |
| `conflicts_with` | (optional) ids this entry disagrees with. Only on `status: conflict`. |
| `note` | (optional) caveat or what would settle it. |

## Priority: how binding

| | Name | Meaning | Who enforces it |
|---|---|---|---|
| **P0** | Hard limit | Never broken, whatever the profile or the AI says. | Code guard. The AI can't override it; its answer is filtered. |
| **P1** | Operating rule | Decided policy. Followed unless a P0 says otherwise. | Rules; also told to the AI, and its answer is checked against it. |
| **P2** | Guidance | A preference or trade-off. The AI may deviate when it has a reason, and the log says why. | The AI. |
| **P3** | Context | A fact about the site, the devices or what was seen. Nothing to obey; it is for reasoning. | Nobody. |

## Status: how sure

| Status | Meaning |
|---|---|
| `decided` | The owner agreed it. |
| `verified` | Measured against real data; the source says how. |
| `assumed` | Believed but not checked. Must not be used as a basis for P0/P1 behaviour until verified. |
| `open` | A question, not a fact. Lives in `open-questions.yaml`. |
| `conflict` | Two sources disagree. Needs a decision, and then one entry is retired. |
| `retired` | Kept for the record only. |

An `assumed` P1 is a smell: either verify it or lower it.

**Fewer rules.** Before adding a rule, try to change or retire one; two entries that say the same thing are
merged. Each active rule is either enforced by the code (with a test) or is advice for the AI only. See
"Fewer Rules" in the project's CLAUDE.md.

## Files

| File | Holds |
|---|---|
| `site.yaml` | The installation: inverters, roof, grid meter, load, forecast, schedule. |
| `miners.yaml` | How the miners behave: power range, tuning, resume/pause cost, noise. |
| `energy.yaml` | How the energy signals behave and what the sunset looked like. |
| `rules.yaml` | The control rules (P0 to P2) decided in the requirements doc and since. |
| `open-questions.yaml` | What is still undecided or unmeasured, and what would settle it. |
| `situations.yaml` | Named states that nothing measures directly (sunrise, sunset, night, cloud, miner tuning, inverters throttled, sensor dropout) and the evidence for each. |
| `alerts.yaml` | Edge cases that should not happen in normal operation, and what each one triggers. |

## How the AI uses it

1. **Always sent:** every P0 and P1 entry, kept short.
2. **Picked by situation:** P2 and P3 entries for the current situation, within a size budget
   (`KB_PROMPT_BUDGET_CHARS`). The situation comes from `sun.sun`: night, sunrise, midday or
   sunset by the sun's elevation and direction. An entry fits when it is tagged `always`, tagged
   with the situation (or `cloud` while the sun is up), or carries only topic tags and the sun
   is up. When the budget is tight, entries tagged for the moment go first and `always` ones last.
3. **Marked uncertain:** entries that are `assumed` are sent as "unverified".
4. **Never sent as fact:** `open`, `conflict` and `retired` entries.
5. Each line of `ai_log.jsonl` records the situation and the ids of the entries that were sent.
6. (Once decisions are applied) the AI's answer is checked against P0 and P1 first.

Autonomy grows in the stages of the requirements doc (§7): advisory, then the AI picks
which miners change, then it also picks the watt change. P0 and P1 stay a guard on every stage.

## Situations

A **situation** is a named state of the world that no sensor reports, so it is *inferred*:
sunrise, sunset, night, a cloud passing, a miner tuning, the inverters being throttled, a
sensor dropout. None of them is certain and none has a single test. Each is recognised when
several independent signals point the same way, and it can be wrong when something unusual
happens (a cloud at sunrise, fog all morning). The tags on facts, rules and alerts are the
situation names, so "what matters at sunset" is a lookup, not a guess.

Refer to one by its id, for example `situation.sunrise` or `situation.tuning`.

**Signals.** Each situation lists signals `for` it and signals `against` it. A signal has a
`kind` (`measured`, `forecast`, `time`, `derived`), a `weight` (`strong` 3, `medium` 2,
`weak` 1), the entities or facts it reads (`uses`), and whether that data is readable today
(`available`). The clock alone is `time`, and is always `weak`, because the season moves it.

**Scoring** (starting values, to be tuned from the logs): add the weight of every `for`
signal that holds, subtract the weight of every `against` signal that holds.

| Score | Level | Rule |
|---|---|---|
| 6 or more | confirmed | and at least two different kinds of signal agree |
| 4 to 5 | likely | and at least two different kinds of signal agree |
| 2 to 3 | possible | |
| under 2 | not active | |

Two kinds must agree for "likely" or better, so the time of day alone can never make it
sunrise. A signal that is not `available` yet is skipped, and the entry says what is missing.
`in_code` says how much of this exists in the integration today; most situations are only
described. For example `kb.py` currently picks night, sunrise, midday or sunset from the sun's
elevation alone (one signal) to choose which facts to send; the multi-signal versions below
are what would replace it, and tuning, cloud, curtailed and dropout are not used by it at all. The tuning flag, for one, currently uses a single signal (time since the limit
changed), not the several listed.

**Editing.**
- *Add:* a new entry in `situations.yaml` with a new unique `tag`, `status: proposed`, then
  put that tag on the facts and rules it affects. The test fails if the tag is unused.
- *Change:* edit the signals or weights, or `status` (`proposed`, `in_use`, `retired`).
- *Delete:* set `status: retired` first. Remove the entry only when nothing refers to it;
  the test names whatever still does.
- *Use in a decision:* give a rule, fact or alert a `situations: [situation.tuning]` field. Code
  that decides refers to the situation by id (`situation.tuning`), never by its signals.

A situation is either `scope: site` (one answer for the whole site: sunrise, night, cloud) or
`scope: miner` (one answer per miner: tuning). A miner situation is evaluated separately for
Brod1, Brod2 and Brod3, from that miner's own sensors.

## Alerts

`alerts.yaml` is separate from the facts on purpose. An alert is an edge case: it should
not occur in normal operation, so each one carries what *normal* looks like, what is
**not** an alert (so a quiet sunset does not page anyone), how it is handled and who acts.
Severity decides the route:

| Severity | Meaning | Route |
|---|---|---|
| `info` | Worth a record, nobody needs to act. | Log and dashboard only. |
| `warning` | Something is off; look at it today. | Plus one notification, then a cooldown. |
| `critical` | Money or hardware at risk, or the controller is blind. | Plus a repeating notification until acknowledged, and an automatic safe action. |

Safe actions are `hold` (change nothing), `freeze` (stop deciding for that miner or all),
`step_down` and `pause_all`. They only exist once decisions are applied; until then an
alert is a notification and a log entry.

## Editing rules

- One fact per entry. If it has "and", it is probably two entries.
- Put the evidence in `source` and `date`. A number without a source is `assumed`.
- Change `status` when you learn something; don't silently edit the statement.
- When two entries disagree, add a `conflict` entry that names both; don't pick one quietly.
- `tests/test_knowledge.py` checks the format, the ids and the cross references.
