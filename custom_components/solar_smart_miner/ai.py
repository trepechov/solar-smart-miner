"""OpenRouter client for the AI advisor.

For now the AI only *comments* on the rule-based decision (see decision.py): it
is sent the readings and the proposal, and its answer is shown on the "AI advice"
sensor. Nothing it says is applied to the miners.
"""
from __future__ import annotations

import json
import logging
import time
from datetime import UTC, datetime

import aiohttp

from .const import DEFAULT_AI_TIMEOUT, OPENROUTER_API_URL, PROFILES_BY_NAME
from .protocols import AiAdvice, CoordinatorSnapshot

_LOGGER = logging.getLogger(__name__)

SYSTEM_PROMPT = (
    "You are the advisor for Solar Smart Miner, a Home Assistant controller that sets "
    "power limits on ASIC bitcoin miners so they run on surplus solar power. You are "
    "shown the current readings and the rule-based controller's proposal. You cannot "
    "change anything: you only say what you WOULD change.\n"
    "Reply with JSON only, no markdown, in exactly this shape:\n"
    '{"summary": "<one short sentence>", "actions": [{"miner": "<name>", '
    '"action": "increase|reduce|hold|stop|start", '
    '"reason": "excess_energy|not_enough_energy|voltage_limit|temperature_limit|battery_low|no_change|other", '
    '"note": "<few words>"}]}\n'
    "Give one action per miner. increase = raise its power limit because there is surplus "
    "energy. reduce = lower it because the available power is below what it draws, or "
    "because of a limit (voltage, temperature, battery). hold = leave it. stop = switch it "
    "off because even the lowest power step is more than the available power (e.g. after "
    "sunset). start = switch a stopped miner back on because there is room for it.\n"
    "Power limits only ever move between fixed steps (900 to 2,500 W in 200 W steps by default); "
    "never suggest other wattages. Every change restarts the miner: it draws almost nothing "
    "for 2 to 4 minutes. So change ONE miner per answer (a change may skip steps) and hold "
    "the others, unless safety needs more; while a miner is restarting, hold every miner. "
    "Several miners restarting together drop the farm's load to almost 0 W.\n"
    "The inverters may be power-limited (zero export), so actual PV can be far below the "
    "forecast: the forecast is what the panels could give, not power that is available."
)

ACTIONS = ("increase", "reduce", "hold", "stop", "start")
REASONS = (
    "excess_energy",
    "not_enough_energy",
    "voltage_limit",
    "temperature_limit",
    "battery_low",
    "no_change",
    "other",
)
_ACTION_ALIASES = {
    "raise": "increase",
    "lower": "reduce",
    "decrease": "reduce",
    "keep": "hold",
    "pause": "stop",
    "shutdown": "stop",
    "off": "stop",
    "resume": "start",
    "on": "start",
}

MAX_RESPONSE_TOKENS = 800
_MODELS_TIMEOUT = 5  # seconds; the model list is only a convenience for the options form

# Offered when the OpenRouter model list can't be fetched. "openrouter/free" routes
# to whichever free model is available, so it keeps working as free models rotate.
FALLBACK_FREE_MODELS = ["openrouter/free"]


def build_messages(
    snapshot: CoordinatorSnapshot,
    *,
    profile: str,
    temp_target: float,
    temp_tolerance: float,
    battery_floor: float,
    knowledge: str = "",
) -> list[dict[str, str]]:
    """System prompt (plus the knowledge base section, if any) and the readings."""
    profile_def = PROFILES_BY_NAME.get(profile)
    label = profile_def["display_name"] if profile_def else profile
    trace = snapshot.decision.trace if snapshot.decision else ["No decision available."]
    user = (
        f"Profile: {label}\n"
        f"Temperature target: {temp_target:.0f} °C, tolerance {temp_tolerance:.0f} °C\n"
        f"Battery floor: {battery_floor:.0f} %\n\n" + "\n".join(trace)
    )
    return [
        {"role": "system", "content": f"{SYSTEM_PROMPT}\n\n{knowledge}" if knowledge else SYSTEM_PROMPT},
        {"role": "user", "content": user},
    ]


def parse_advice(text: str) -> tuple[str, list[dict[str, str]]]:
    """Split the model's reply into (summary, actions).

    Models sometimes wrap the JSON in a code fence or add prose (or a stray brace)
    around it, so the first complete {...} object is used. A reply that isn't the requested JSON gives
    ("", []): the caller then shows the raw text instead.
    """
    start = text.find("{")
    if start < 0:
        return "", []
    try:
        # Only the first complete object: models add stray braces or prose after it.
        data, _ = json.JSONDecoder().raw_decode(text[start:])
    except ValueError:
        return "", []
    if not isinstance(data, dict):
        return "", []
    actions: list[dict[str, str]] = []
    for item in data.get("actions") or []:
        if not isinstance(item, dict) or not item.get("miner"):
            continue
        action = str(item.get("action") or "hold").strip().lower()
        reason = str(item.get("reason") or "other").strip().lower()
        actions.append(
            {
                "miner": str(item["miner"]),
                "action": _ACTION_ALIASES.get(action, action),
                "reason": reason if reason in REASONS else "other",
                "note": str(item.get("note") or "")[:120],
            }
        )
    return str(data.get("summary") or "").strip(), actions


def format_advice(summary: str, actions: list[dict[str, str]]) -> str:
    """One readable text for the sensor and dashboard."""
    parts = [summary] if summary else []
    parts += [f"{a['miner']}: {a['action']} ({a['reason']})" for a in actions]
    return " | ".join(parts)


def _error_message(status: int, body: object) -> str:
    """Human-readable reason for a non-200 reply. Never includes request headers."""
    if status in (401, 403):
        return "OpenRouter rejected the API key"
    if status == 402:
        return "OpenRouter account has no credit for this model"
    if status == 429:
        return "Rate limited by OpenRouter (free models allow only a few requests)"
    detail = ""
    if isinstance(body, dict) and isinstance(body.get("error"), dict):
        detail = str(body["error"].get("message") or "")
    return f"OpenRouter error {status}" + (f": {detail[:200]}" if detail else "")


async def async_ask(
    session: aiohttp.ClientSession,
    *,
    api_key: str,
    model: str,
    messages: list[dict[str, str]],
    timeout: float = DEFAULT_AI_TIMEOUT,
) -> AiAdvice:
    """Send one chat completion; always returns an AiAdvice (errors go in .error)."""
    started = time.monotonic()
    now = datetime.now(UTC).isoformat(timespec="seconds")

    def _result(text: str = "", error: str | None = None) -> AiAdvice:
        return AiAdvice(
            text=text,
            model=model,
            requested_at=now,
            latency_s=round(time.monotonic() - started, 1),
            error=error,
        )

    try:
        async with session.post(
            OPENROUTER_API_URL,
            headers={
                "Authorization": f"Bearer {api_key}",
                "HTTP-Referer": "https://github.com/trepechov/solar-smart-miner",
                "X-Title": "Solar Smart Miner",
            },
            json={
                "model": model,
                "messages": messages,
                "max_tokens": MAX_RESPONSE_TOKENS,
                # Reasoning models would otherwise spend the cap thinking and
                # return a truncated answer; other models ignore this.
                "reasoning": {"effort": "low"},
                "temperature": 0.2,
            },
            timeout=aiohttp.ClientTimeout(total=timeout),
        ) as resp:
            try:
                body = await resp.json(content_type=None)
            except (aiohttp.ClientError, ValueError):
                body = None
            if resp.status != 200:
                return _result(error=_error_message(resp.status, body))
    except TimeoutError:
        return _result(error=f"No answer from OpenRouter within {timeout:.0f} s")
    except aiohttp.ClientError as err:
        return _result(error=f"Could not reach OpenRouter: {type(err).__name__}")

    try:
        text = (body["choices"][0]["message"]["content"] or "").strip()
    except (KeyError, IndexError, TypeError):
        text = ""
    if not text:
        return _result(error="The model returned an empty answer")
    summary, actions = parse_advice(text)
    advice = _result(text=format_advice(summary, actions) if (summary or actions) else text)
    advice.summary, advice.actions, advice.raw = summary, actions, text
    return advice


async def async_free_models(session: aiohttp.ClientSession) -> list[tuple[str, str]]:
    """(model id, label) of the free text models OpenRouter currently offers.

    Falls back to a short built-in list when the (public) catalogue is unreachable.
    """
    fallback = [(m, m) for m in FALLBACK_FREE_MODELS]
    try:
        async with session.get(
            OPENROUTER_API_URL.replace("/chat/completions", "/models"),
            timeout=aiohttp.ClientTimeout(total=_MODELS_TIMEOUT),
        ) as resp:
            if resp.status != 200:
                return fallback
            catalogue = (await resp.json(content_type=None))["data"]
    except (TimeoutError, aiohttp.ClientError, ValueError, KeyError, TypeError):
        return fallback

    free: list[tuple[str, str, int]] = []
    for model in catalogue:
        try:
            pricing = model["pricing"]
            arch = model.get("architecture") or {}
            if pricing.get("prompt") != "0" or pricing.get("completion") != "0":
                continue
            if arch.get("output_modalities") != ["text"] or "text" not in arch.get(
                "input_modalities", []
            ):
                continue
            free.append((model["id"], model.get("name") or model["id"], model.get("context_length") or 0))
        except (KeyError, TypeError, AttributeError):
            continue
    if not free:
        return fallback
    free.sort(key=lambda m: (m[0] != "openrouter/free", -m[2]))
    return [(model_id, name) for model_id, name, _ in free]
