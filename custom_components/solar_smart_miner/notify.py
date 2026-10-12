"""Notifications: HA persistent notifications, and Telegram when it is configured.

Every notification goes through one `Notifier`. A pushed one is also sent to the configured
Telegram chat, in the background, so no cycle or command ever waits on the network. A send
never raises and never logs the bot token: the token is in the URL, so errors are reported by
status and Telegram's own description only.
"""
from __future__ import annotations

from collections.abc import Callable, Mapping
import logging
import time
from typing import Any

import aiohttp
from homeassistant.components.persistent_notification import async_create as pn_create
from homeassistant.components.persistent_notification import async_dismiss as pn_dismiss
from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .const import DOMAIN

_LOGGER = logging.getLogger(__name__)

CONF_TELEGRAM_TOKEN = "telegram_bot_token"
CONF_TELEGRAM_CHAT_ID = "telegram_chat_id"
CONF_TELEGRAM_DAY = "telegram_day_messages"  # the four day messages (day_report.py), on by default

TELEGRAM_API = "https://api.telegram.org/bot{token}/sendMessage"
TELEGRAM_TIMEOUT_S = 10
TELEGRAM_MAX_LEN = 4096  # Telegram's limit for one message
DEFAULT_COOLDOWN_MIN = 15
CONNECTED_TEXT = "✅ Solar Smart Miner is connected to this chat."

SEVERITY_MARK = {"critical": "🔴", "warning": "⚠️", "info": ""}


async def async_send_telegram(
    session: aiohttp.ClientSession, token: str, chat_id: str, text: str
) -> str | None:
    """Send one plain-text message; None when it went out, else a short reason. Never raises."""
    try:
        async with session.post(
            TELEGRAM_API.format(token=token),
            json={
                "chat_id": chat_id,
                "text": text[:TELEGRAM_MAX_LEN],
                "disable_web_page_preview": True,
            },
            timeout=aiohttp.ClientTimeout(total=TELEGRAM_TIMEOUT_S),
        ) as resp:
            if resp.status == 200:
                return None
            try:
                body = await resp.json(content_type=None)
            except (aiohttp.ClientError, ValueError):
                body = None
            detail = body.get("description") if isinstance(body, dict) else None
            return f"HTTP {resp.status}" + (f": {str(detail)[:200]}" if detail else "")
    except TimeoutError:
        return f"no answer from Telegram within {TELEGRAM_TIMEOUT_S} s"
    except aiohttp.ClientError as err:
        return f"could not reach Telegram: {type(err).__name__}"
    except Exception as err:  # noqa: BLE001 - its text could hold the URL, and with it the token
        return f"unexpected error: {type(err).__name__}"


def telegram_target(options: Mapping[str, Any]) -> tuple[str, str] | None:
    """(token, chat id) when both are set, else None (Telegram off)."""
    token = (options.get(CONF_TELEGRAM_TOKEN) or "").strip()
    chat_id = (options.get(CONF_TELEGRAM_CHAT_ID) or "").strip()
    return (token, chat_id) if token and chat_id else None


class Notifier:
    """One way to notify: a persistent notification, and a Telegram push for pushed events."""

    def __init__(
        self, hass: HomeAssistant, get_options: Callable[[], Mapping[str, Any]] = dict
    ) -> None:
        self._hass = hass
        self._get_options = get_options
        self._last_push: dict[str, float] = {}  # key -> time.monotonic() of its last push
        self._failing = False  # the last send failed (warned once per failure streak)

    def notify(
        self,
        key: str,
        title: str,
        message: str,
        *,
        severity: str = "info",
        push: bool = False,
        persistent: bool = True,
        cooldown_min: float | None = None,
    ) -> None:
        """Show `message` (replacing the one with the same key) and push it if asked.

        A push within `cooldown_min` (DEFAULT_COOLDOWN_MIN) of the last one that went out for
        the key is skipped; the persistent notification is still replaced.
        """
        if persistent:
            pn_create(self._hass, message, title=title, notification_id=f"{DOMAIN}_{key}")
        if not push:
            return
        target = telegram_target(self._get_options())
        if target is None:
            return
        now = time.monotonic()
        cooldown = DEFAULT_COOLDOWN_MIN if cooldown_min is None else cooldown_min
        last = self._last_push.get(key)
        if last is not None and now - last < cooldown * 60:
            return
        self._last_push[key] = now
        mark = SEVERITY_MARK.get(severity, "")
        heading = f"{mark} {title}".strip() if title else ""
        text = f"{heading}\n{message}" if heading else message
        self._hass.async_create_background_task(
            self._async_push(key, *target, text), f"{DOMAIN} telegram {key}"
        )

    def dismiss(self, key: str) -> None:
        """Remove the notification. The push cooldown stays, so an event that keeps coming
        and going is pushed at most once per cooldown."""
        pn_dismiss(self._hass, f"{DOMAIN}_{key}")

    async def _async_push(self, key: str, token: str, chat_id: str, text: str) -> None:
        reason = await async_send_telegram(async_get_clientsession(self._hass), token, chat_id, text)
        if reason is None:
            if self._failing:
                _LOGGER.info("Telegram messages are going out again")
            self._failing = False
            return
        self._last_push.pop(key, None)  # it didn't go out: the next one for the key may
        if not self._failing:
            _LOGGER.warning("A Telegram message didn't go out: %s", reason)
        self._failing = True
