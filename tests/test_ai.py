"""Tests for the OpenRouter client (prompt building, replies, error mapping, model list)."""
from __future__ import annotations

import aiohttp
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from custom_components.solar_smart_miner.ai import (
    FALLBACK_FREE_MODELS,
    SYSTEM_PROMPT,
    async_ask,
    async_free_models,
    build_messages,
    format_advice,
    parse_advice,
)
from custom_components.solar_smart_miner.const import OPENROUTER_API_URL
from custom_components.solar_smart_miner.protocols import (
    CoordinatorSnapshot,
    Decision,
    EnergySnapshot,
)

MODELS_URL = "https://openrouter.ai/api/v1/models"
MESSAGES = [{"role": "user", "content": "hi"}]


def _snapshot() -> CoordinatorSnapshot:
    return CoordinatorSnapshot(
        energy=EnergySnapshot(solar_production_w=1000.0),
        decision=Decision(
            summary="Solar-max: budget 500 W",
            trace=["READ", "Solar production: 1,000 W", "PROPOSE", "Brod1 → 500 W"],
        ),
    )


def _model(model_id: str, *, price: str = "0", output=("text",), inputs=("text",), ctx=1000, name=None):
    return {
        "id": model_id,
        "name": name or model_id,
        "context_length": ctx,
        "pricing": {"prompt": price, "completion": price},
        "architecture": {"input_modalities": list(inputs), "output_modalities": list(output)},
    }


# --- build_messages ---------------------------------------------------------


def test_build_messages_includes_trace_profile_and_limits() -> None:
    messages = build_messages(_snapshot(), profile="solar_follow", temp_target=65, temp_tolerance=10, battery_floor=20)

    assert messages[0] == {"role": "system", "content": SYSTEM_PROMPT}
    user = messages[1]["content"]
    assert "Profile: Solar-follow" in user
    assert "Temperature target: 65 °C, tolerance 10 °C" in user
    assert "Battery floor: 20 %" in user
    assert "Brod1 → 500 W" in user


def test_build_messages_adds_the_knowledge_base_to_the_system_prompt() -> None:
    messages = build_messages(
        _snapshot(), profile="solar_follow", temp_target=65, temp_tolerance=10, battery_floor=20,
        knowledge="KNOWLEDGE BASE (situation: midday)\n- [P0] Rule: text",
    )

    assert messages[0]["content"].startswith(SYSTEM_PROMPT)
    assert messages[0]["content"].endswith("- [P0] Rule: text")
    assert "KNOWLEDGE BASE" not in messages[1]["content"]


def test_build_messages_tells_the_model_it_cannot_change_anything() -> None:
    assert "cannot change anything" in SYSTEM_PROMPT


def test_build_messages_without_decision() -> None:
    snapshot = CoordinatorSnapshot(energy=EnergySnapshot(solar_production_w=None))

    messages = build_messages(snapshot, profile="solar_follow", temp_target=65, temp_tolerance=10, battery_floor=20)

    assert "No decision available." in messages[1]["content"]


# --- async_ask --------------------------------------------------------------


async def test_ask_returns_the_models_answer(hass, aioclient_mock) -> None:
    aioclient_mock.post(
        OPENROUTER_API_URL,
        json={"choices": [{"message": {"content": "  Looks sensible.  "}}]},
    )

    advice = await async_ask(
        async_get_clientsession(hass), api_key="sk-or-secret", model="x/y:free", messages=MESSAGES
    )

    assert advice.error is None
    assert advice.text == "Looks sensible."
    assert advice.model == "x/y:free"
    method, url, payload, headers = aioclient_mock.mock_calls[0]
    assert (method, str(url)) == ("POST", OPENROUTER_API_URL)
    assert payload["model"] == "x/y:free"
    assert payload["messages"] == MESSAGES
    assert headers["Authorization"] == "Bearer sk-or-secret"


async def test_ask_maps_rejected_key_without_leaking_it(hass, aioclient_mock) -> None:
    aioclient_mock.post(OPENROUTER_API_URL, status=401, json={"error": {"message": "bad"}})

    advice = await async_ask(
        async_get_clientsession(hass), api_key="sk-or-secret", model="m", messages=MESSAGES
    )

    assert advice.text == ""
    assert advice.error == "OpenRouter rejected the API key"
    assert "sk-or-secret" not in repr(advice)


async def test_ask_maps_rate_limit(hass, aioclient_mock) -> None:
    aioclient_mock.post(OPENROUTER_API_URL, status=429, json={})

    advice = await async_ask(
        async_get_clientsession(hass), api_key="k", model="m", messages=MESSAGES
    )

    assert "Rate limited" in advice.error


async def test_ask_includes_server_message_for_other_errors(hass, aioclient_mock) -> None:
    aioclient_mock.post(
        OPENROUTER_API_URL, status=400, json={"error": {"message": "model not found"}}
    )

    advice = await async_ask(
        async_get_clientsession(hass), api_key="k", model="m", messages=MESSAGES
    )

    assert advice.error == "OpenRouter error 400: model not found"


async def test_ask_reports_timeout(hass, aioclient_mock) -> None:
    aioclient_mock.post(OPENROUTER_API_URL, exc=TimeoutError)

    advice = await async_ask(
        async_get_clientsession(hass), api_key="k", model="m", messages=MESSAGES, timeout=5
    )

    assert advice.error == "No answer from OpenRouter within 5 s"


async def test_ask_reports_unreachable(hass, aioclient_mock) -> None:
    aioclient_mock.post(OPENROUTER_API_URL, exc=aiohttp.ClientConnectionError)

    advice = await async_ask(
        async_get_clientsession(hass), api_key="k", model="m", messages=MESSAGES
    )

    assert advice.error == "Could not reach OpenRouter: ClientConnectionError"


async def test_ask_reports_empty_answer(hass, aioclient_mock) -> None:
    aioclient_mock.post(OPENROUTER_API_URL, json={"choices": [{"message": {"content": None}}]})

    advice = await async_ask(
        async_get_clientsession(hass), api_key="k", model="m", messages=MESSAGES
    )

    assert advice.error == "The model returned an empty answer"


async def test_ask_reports_malformed_reply(hass, aioclient_mock) -> None:
    aioclient_mock.post(OPENROUTER_API_URL, json={"unexpected": True})

    advice = await async_ask(
        async_get_clientsession(hass), api_key="k", model="m", messages=MESSAGES
    )

    assert advice.error == "The model returned an empty answer"


# --- async_free_models ------------------------------------------------------


async def test_free_models_keeps_only_free_text_models_router_first(hass, aioclient_mock) -> None:
    aioclient_mock.get(
        MODELS_URL,
        json={
            "data": [
                _model("big/model:free", ctx=500_000, name="Big"),
                _model("paid/model", price="0.000001"),
                _model("audio/gen:free", output=("text", "audio")),
                _model("image/only:free", inputs=("image",)),
                _model("small/model:free", ctx=8_000, name="Small"),
                _model("openrouter/free", ctx=200_000, name="Free Models Router"),
            ]
        },
    )

    models = await async_free_models(async_get_clientsession(hass))

    assert models == [
        ("openrouter/free", "Free Models Router"),
        ("big/model:free", "Big"),
        ("small/model:free", "Small"),
    ]


async def test_free_models_falls_back_when_catalogue_unreachable(hass, aioclient_mock) -> None:
    aioclient_mock.get(MODELS_URL, exc=aiohttp.ClientConnectionError)

    models = await async_free_models(async_get_clientsession(hass))

    assert models == [(m, m) for m in FALLBACK_FREE_MODELS]


async def test_free_models_falls_back_on_http_error(hass, aioclient_mock) -> None:
    aioclient_mock.get(MODELS_URL, status=500)

    assert await async_free_models(async_get_clientsession(hass)) == [
        (m, m) for m in FALLBACK_FREE_MODELS
    ]


async def test_free_models_falls_back_when_nothing_is_free(hass, aioclient_mock) -> None:
    aioclient_mock.get(MODELS_URL, json={"data": [_model("paid/model", price="1")]})

    assert await async_free_models(async_get_clientsession(hass)) == [
        (m, m) for m in FALLBACK_FREE_MODELS
    ]


# --- request body ------------------------------------------------------------


async def test_ask_asks_for_low_reasoning_and_a_roomy_cap(hass, aioclient_mock) -> None:
    """Reasoning models (gpt-oss) otherwise burn the cap thinking and truncate the answer."""
    from custom_components.solar_smart_miner.ai import MAX_RESPONSE_TOKENS
    from custom_components.solar_smart_miner.const import DEFAULT_AI_TIMEOUT

    aioclient_mock.post(OPENROUTER_API_URL, json={"choices": [{"message": {"content": "ok"}}]})
    await async_ask(async_get_clientsession(hass), api_key="k", model="m", messages=MESSAGES)

    payload = aioclient_mock.mock_calls[0][2]
    assert payload["reasoning"] == {"effort": "low"}
    assert payload["max_tokens"] == MAX_RESPONSE_TOKENS >= 800
    assert DEFAULT_AI_TIMEOUT >= 40


# --- structured answers --------------------------------------------------------

STRUCTURED = (
    '{"summary": "Sun is setting", "actions": ['
    '{"miner": "Brod1", "action": "reduce", "reason": "not_enough_energy", "note": "dusk"},'
    '{"miner": "Brod2", "action": "increase", "reason": "excess_energy"}]}'
)


def test_parse_advice_reads_summary_and_actions() -> None:
    summary, actions = parse_advice(STRUCTURED)

    assert summary == "Sun is setting"
    assert actions == [
        {"miner": "Brod1", "action": "reduce", "target_w": None, "reason": "not_enough_energy", "note": "dusk"},
        {"miner": "Brod2", "action": "increase", "target_w": None, "reason": "excess_energy", "note": ""},
    ]


def test_parse_advice_tolerates_code_fences_and_prose() -> None:
    summary, actions = parse_advice(f"Sure!\n```json\n{STRUCTURED}\n```\nHope that helps.")

    assert summary == "Sun is setting"
    assert len(actions) == 2


def test_parse_advice_normalises_aliases_and_unknown_reasons() -> None:
    _, actions = parse_advice(
        '{"summary": "s", "actions": [{"miner": "A", "action": "Lower", "reason": "vibes"}]}'
    )

    assert actions[0]["action"] == "reduce"
    assert actions[0]["reason"] == "other"


def test_parse_advice_skips_malformed_actions() -> None:
    _, actions = parse_advice(
        '{"summary": "s", "actions": ["junk", {"action": "hold"}, {"miner": "A"}]}'
    )

    assert actions == [{"miner": "A", "action": "hold", "target_w": None, "reason": "other", "note": ""}]


def test_parse_advice_returns_nothing_for_plain_text_or_bad_json() -> None:
    assert parse_advice("Looks sensible.") == ("", [])
    assert parse_advice("{not json}") == ("", [])
    assert parse_advice("[1, 2]") == ("", [])


def test_format_advice_is_one_readable_line() -> None:
    summary, actions = parse_advice(STRUCTURED)

    assert format_advice(summary, actions) == (
        "Sun is setting | Brod1: reduce (not_enough_energy) | Brod2: increase (excess_energy)"
    )


async def test_ask_keeps_structured_actions_and_the_raw_reply(hass, aioclient_mock) -> None:
    aioclient_mock.post(OPENROUTER_API_URL, json={"choices": [{"message": {"content": STRUCTURED}}]})

    advice = await async_ask(async_get_clientsession(hass), api_key="k", model="m", messages=MESSAGES)

    assert advice.summary == "Sun is setting"
    assert [a["miner"] for a in advice.actions] == ["Brod1", "Brod2"]
    assert advice.raw == STRUCTURED
    assert "Brod1: reduce (not_enough_energy)" in advice.text


async def test_ask_falls_back_to_plain_text_when_reply_is_not_json(hass, aioclient_mock) -> None:
    aioclient_mock.post(OPENROUTER_API_URL, json={"choices": [{"message": {"content": "All good."}}]})

    advice = await async_ask(async_get_clientsession(hass), api_key="k", model="m", messages=MESSAGES)

    assert advice.text == "All good."
    assert advice.summary == "" and advice.actions == []


def test_system_prompt_defines_the_action_and_reason_vocabulary() -> None:
    from custom_components.solar_smart_miner.ai import ACTIONS, REASONS

    for word in (*ACTIONS, *REASONS):
        assert word in SYSTEM_PROMPT


def test_parse_advice_reads_the_0_7_answer_stop_and_start_as_pause_and_resume() -> None:
    _, actions = parse_advice(
        '{"summary": "s", "actions": ['
        '{"miner": "A", "action": "stop", "reason": "not_enough_energy"},'
        '{"miner": "B", "action": "Pause"},'
        '{"miner": "C", "action": "resume", "reason": "excess_energy"}]}'
    )

    assert [a["action"] for a in actions] == ["pause", "pause", "resume"]


def test_system_prompt_explains_steps_restarts_and_stopping() -> None:
    assert "pause" in SYSTEM_PROMPT and "resume" in SYSTEM_PROMPT and "target_w" in SYSTEM_PROMPT
    assert "restarts the miner" in SYSTEM_PROMPT
    assert "configured power steps" in SYSTEM_PROMPT
    # The reference farm's ladder is an example, not something every farm is told.
    assert "900" not in SYSTEM_PROMPT and "2,500" not in SYSTEM_PROMPT
    assert "live configuration" in SYSTEM_PROMPT


def test_system_prompt_asks_for_one_miner_per_answer() -> None:
    assert "change ONE miner per answer" in SYSTEM_PROMPT
    assert "while a miner is restarting, hold every miner" in SYSTEM_PROMPT


def test_parse_advice_ignores_a_stray_brace_or_prose_after_the_object() -> None:
    """Seen live from mistral-nemo: the reply ended with an extra closing brace."""
    live = (
        '{"summary": "Reduce power", "actions": [{"miner": "Brod1", "action": "reduce", '
        '"reason": "not_enough_energy", "note": "x"}]}}'
    )

    for text in (live, live + "\nHope that helps!", live + " {", "Sure: " + live):
        summary, actions = parse_advice(text)
        assert summary == "Reduce power", text
        assert [a["miner"] for a in actions] == ["Brod1"], text


def test_parse_advice_still_rejects_truncated_json() -> None:
    assert parse_advice('{"summary": "cut off", "actions": [{"miner": "Brod1", "act') == ("", [])


def test_the_farm_block_goes_into_the_system_prompt_before_the_knowledge() -> None:
    messages = build_messages(
        _snapshot(), profile="solar_follow", temp_target=65, temp_tolerance=10, battery_floor=20,
        knowledge="KNOWLEDGE BASE ...", farm="THIS FARM (the owner's description):\n- Battery: none",
    )

    system = messages[0]["content"]
    assert system.index("THIS FARM") < system.index("KNOWLEDGE BASE")
    assert "THIS FARM" not in build_messages(
        _snapshot(), profile="solar_follow", temp_target=65, temp_tolerance=10, battery_floor=20,
    )[0]["content"]


# --- the target step (U6; the decision is in rule.ai-is-advisor) -------------------------------------------

STEPS = [900.0, 1100.0, 1300.0, 1500.0]


def test_an_answer_names_a_target_step_per_miner() -> None:
    _, actions = parse_advice(
        '{"summary": "s", "actions": ['
        '{"miner": "A", "action": "increase", "target_w": 1300, "reason": "excess_energy"},'
        '{"miner": "B", "action": "hold", "target_w": null, "reason": "ramping"}]}',
        STEPS,
    )

    assert [(a["action"], a["target_w"], a["reason"]) for a in actions] == [
        ("increase", 1300.0, "excess_energy"), ("hold", None, "ramping"),
    ]
    assert "invalid" not in actions[0]
    assert format_advice("s", actions) == "s | A: increase to 1,300 W (excess_energy) | B: hold (ramping)"


def test_an_off_step_target_is_flagged_never_rounded() -> None:
    _, actions = parse_advice(
        '{"summary": "s", "actions": [{"miner": "A", "action": "reduce", "target_w": "1250"}]}', STEPS
    )

    assert actions[0]["target_w"] == 1250.0
    assert actions[0]["invalid"] == "1250 W is not a power step"
    assert "[1250 W is not a power step]" in format_advice("", actions)


def test_a_target_that_is_not_a_number_is_none() -> None:
    _, actions = parse_advice('{"summary": "s", "actions": [{"miner": "A", "target_w": "lots"}]}', STEPS)

    assert actions[0]["target_w"] is None and "invalid" not in actions[0]


def test_recent_changes_go_into_the_readings() -> None:
    messages = build_messages(
        _snapshot(), profile="solar_follow", temp_target=65, temp_tolerance=10, battery_floor=20,
        recent=["(now 12:05:00)", "12:01:30 Brod1: 1,500 W (ok)"],
    )

    user = messages[1]["content"]
    assert "RECENT CHANGES (newest first)\n(now 12:05:00)\n12:01:30 Brod1: 1,500 W (ok)" in user


async def test_ask_checks_the_targets_against_the_steps(hass, aioclient_mock) -> None:
    reply = '{"summary": "s", "actions": [{"miner": "A", "action": "increase", "target_w": 1400}]}'
    aioclient_mock.post(OPENROUTER_API_URL, json={"choices": [{"message": {"content": reply}}]})

    advice = await async_ask(
        async_get_clientsession(hass), api_key="k", model="m", messages=MESSAGES, steps=STEPS
    )

    assert advice.actions[0]["invalid"] == "1400 W is not a power step"
