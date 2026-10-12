"""Telegram sender and the Notifier (plan 2026-10-09-002, U1 and U2)."""
from __future__ import annotations

import logging

import aiohttp
from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from custom_components.solar_smart_miner import notify
from custom_components.solar_smart_miner.notify import (
    TELEGRAM_MAX_LEN,
    Notifier,
    async_send_telegram,
    telegram_target,
)

TOKEN = "123456:SECRET-token"
CHAT = "-100777"
URL = f"https://api.telegram.org/bot{TOKEN}/sendMessage"
OPTIONS = {"telegram_bot_token": TOKEN, "telegram_chat_id": CHAT}


# --- U1: the sender -----------------------------------------------------------


async def test_send_posts_the_chat_and_text(hass: HomeAssistant, aioclient_mock) -> None:
    aioclient_mock.post(URL, json={"ok": True})

    assert await async_send_telegram(async_get_clientsession(hass), TOKEN, CHAT, "hello") is None
    _, _, payload, _ = aioclient_mock.mock_calls[0]
    assert payload == {"chat_id": CHAT, "text": "hello", "disable_web_page_preview": True}


async def test_send_reports_a_rejected_token_without_it(hass: HomeAssistant, aioclient_mock, caplog) -> None:
    aioclient_mock.post(URL, status=401, json={"ok": False, "description": "Unauthorized"})

    reason = await async_send_telegram(async_get_clientsession(hass), TOKEN, CHAT, "x")

    assert reason == "HTTP 401: Unauthorized"
    assert TOKEN not in reason and TOKEN not in caplog.text


async def test_send_reports_a_wrong_chat(hass: HomeAssistant, aioclient_mock) -> None:
    aioclient_mock.post(URL, status=400, json={"ok": False, "description": "Bad Request: chat not found"})

    reason = await async_send_telegram(async_get_clientsession(hass), TOKEN, CHAT, "x")

    assert reason == "HTTP 400: Bad Request: chat not found"


async def test_send_reports_a_timeout_and_a_connection_error(hass: HomeAssistant, aioclient_mock) -> None:
    session = async_get_clientsession(hass)
    aioclient_mock.post(URL, exc=TimeoutError)
    assert "no answer" in await async_send_telegram(session, TOKEN, CHAT, "x")

    aioclient_mock.clear_requests()
    aioclient_mock.post(URL, exc=aiohttp.ClientConnectionError)
    reason = await async_send_telegram(session, TOKEN, CHAT, "x")
    assert reason == "could not reach Telegram: ClientConnectionError"
    assert TOKEN not in reason


async def test_send_never_raises_and_names_only_the_error_type(hass: HomeAssistant, aioclient_mock) -> None:
    aioclient_mock.post(URL, exc=RuntimeError(f"failed for {URL}"))

    reason = await async_send_telegram(async_get_clientsession(hass), TOKEN, CHAT, "x")

    assert reason == "unexpected error: RuntimeError"


async def test_send_cuts_an_over_long_text(hass: HomeAssistant, aioclient_mock) -> None:
    aioclient_mock.post(URL, json={"ok": True})

    await async_send_telegram(async_get_clientsession(hass), TOKEN, CHAT, "x" * (TELEGRAM_MAX_LEN + 50))

    assert len(aioclient_mock.mock_calls[0][2]["text"]) == TELEGRAM_MAX_LEN


def test_telegram_is_on_only_with_both_token_and_chat() -> None:
    assert telegram_target(OPTIONS) == (TOKEN, CHAT)
    assert telegram_target({"telegram_bot_token": TOKEN}) is None
    assert telegram_target({"telegram_bot_token": " ", "telegram_chat_id": CHAT}) is None
    assert telegram_target({}) is None


# --- U2: the Notifier ---------------------------------------------------------


async def test_notify_shows_a_persistent_notification(hass: HomeAssistant, mock_telegram) -> None:
    Notifier(hass, lambda: OPTIONS).notify("k", "Title", "Body")
    await hass.async_block_till_done(wait_background_tasks=True)

    note = hass.data["persistent_notification"]["solar_smart_miner_k"]
    assert (note["title"], note["message"]) == ("Title", "Body")
    mock_telegram.assert_not_awaited()  # not a pushed event


async def test_a_push_goes_to_telegram_with_its_marker(hass: HomeAssistant, mock_telegram) -> None:
    Notifier(hass, lambda: OPTIONS).notify("k", "Title", "Body", severity="critical", push=True)
    await hass.async_block_till_done(wait_background_tasks=True)

    mock_telegram.assert_awaited_once()
    _, token, chat, text = mock_telegram.await_args.args
    assert (token, chat, text) == (TOKEN, CHAT, "🔴 Title\nBody")


async def test_a_push_without_telegram_only_shows_the_notification(hass: HomeAssistant, mock_telegram) -> None:
    Notifier(hass, dict).notify("k", "Title", "Body", push=True)
    await hass.async_block_till_done(wait_background_tasks=True)

    assert "solar_smart_miner_k" in hass.data["persistent_notification"]
    mock_telegram.assert_not_awaited()


async def test_a_telegram_only_message_leaves_no_notification(hass: HomeAssistant, mock_telegram) -> None:
    Notifier(hass, lambda: OPTIONS).notify("day", "", "☀️ Up", push=True, persistent=False)
    await hass.async_block_till_done(wait_background_tasks=True)

    assert "solar_smart_miner_day" not in hass.data.get("persistent_notification", {})
    assert mock_telegram.await_args.args[3] == "☀️ Up"


async def test_a_key_is_pushed_once_per_cooldown(hass: HomeAssistant, mock_telegram, monkeypatch) -> None:
    clock = [1000.0]
    monkeypatch.setattr(notify.time, "monotonic", lambda: clock[0])
    notifier = Notifier(hass, lambda: OPTIONS)

    notifier.notify("k", "T", "first", push=True, cooldown_min=15)
    clock[0] += 14 * 60
    notifier.notify("k", "T", "second", push=True, cooldown_min=15)  # inside: not pushed
    notifier.notify("other", "T", "else", push=True, cooldown_min=15)  # another key is
    clock[0] += 2 * 60
    notifier.notify("k", "T", "third", push=True, cooldown_min=15)
    await hass.async_block_till_done(wait_background_tasks=True)

    assert [c.args[3] for c in mock_telegram.await_args_list] == ["T\nfirst", "T\nelse", "T\nthird"]
    # The notification itself is still replaced every time.
    assert hass.data["persistent_notification"]["solar_smart_miner_k"]["message"] == "third"


async def test_dismiss_keeps_the_cooldown(hass: HomeAssistant, mock_telegram) -> None:
    """An event that comes and goes (fail, recover, fail) is pushed once per cooldown."""
    notifier = Notifier(hass, lambda: OPTIONS)
    notifier.notify("k", "T", "one", push=True)
    notifier.dismiss("k")
    notifier.notify("k", "T", "two", push=True)
    await hass.async_block_till_done(wait_background_tasks=True)

    assert "solar_smart_miner_k" in hass.data["persistent_notification"]  # shown again
    assert mock_telegram.await_count == 1


async def test_a_push_that_didnt_go_out_doesnt_use_the_cooldown(hass: HomeAssistant, mock_telegram) -> None:
    notifier = Notifier(hass, lambda: OPTIONS)
    mock_telegram.return_value = "timeout"
    notifier.notify("k", "T", "one", push=True)
    await hass.async_block_till_done(wait_background_tasks=True)

    mock_telegram.return_value = None
    notifier.notify("k", "T", "two", push=True)
    notifier.notify("k", "T", "three", push=True)  # "two" went out: this one waits
    await hass.async_block_till_done(wait_background_tasks=True)

    assert [c.args[3] for c in mock_telegram.await_args_list] == ["T\none", "T\ntwo"]


async def test_a_failing_send_is_warned_once_per_streak(hass: HomeAssistant, mock_telegram, caplog) -> None:
    mock_telegram.return_value = "HTTP 401: Unauthorized"
    notifier = Notifier(hass, lambda: OPTIONS)
    caplog.set_level(logging.WARNING)

    for key in ("a", "b"):
        notifier.notify(key, "T", "x", push=True)
        await hass.async_block_till_done(wait_background_tasks=True)
    assert caplog.text.count("didn't go out") == 1

    mock_telegram.return_value = None
    notifier.notify("c", "T", "x", push=True)
    await hass.async_block_till_done(wait_background_tasks=True)
    mock_telegram.return_value = "timeout"
    notifier.notify("d", "T", "x", push=True)
    await hass.async_block_till_done(wait_background_tasks=True)
    assert caplog.text.count("didn't go out") == 2  # a new streak warns again
