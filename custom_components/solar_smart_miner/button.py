"""Button platform for Solar Smart Miner."""
from __future__ import annotations

import json
import logging

from homeassistant.components.button import ButtonEntity
from homeassistant.components.persistent_notification import async_create as pn_create
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import device_registry, entity_registry
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import (
    CONF_CONTROL_MODE,
    CONTROL_MODE_MANUAL,
    CONTROL_MODE_PREVIEW,
    DEFAULT_CONTROL_MODE,
    DOMAIN,
)
from .control import RESULT_FAILED, RESULT_REFUSED, CommandResult
from .coordinator import SolarMinerCoordinator

_LOGGER = logging.getLogger(__name__)

_MODE_TITLES = {
    CONTROL_MODE_PREVIEW: "preview, not applied",
    CONTROL_MODE_MANUAL: "manual apply",
}


def _hub_device_info(entry: ConfigEntry) -> DeviceInfo:
    return DeviceInfo(
        identifiers={(DOMAIN, entry.entry_id)},
        name=entry.title,
    )


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    coordinator: SolarMinerCoordinator = entry.runtime_data
    async_add_entities([AddToDashboardButton(entry), AskAiButton(entry), ApplyAllButton(coordinator, entry)])



class AddToDashboardButton(ButtonEntity):
    _attr_has_entity_name = True
    _attr_name = "Add to dashboard"
    _attr_icon = "mdi:view-dashboard-variant"

    def __init__(self, entry: ConfigEntry) -> None:
        self._entry = entry
        self._attr_unique_id = f"{entry.entry_id}_add_to_dashboard"
        self._attr_device_info = _hub_device_info(entry)

    async def async_press(self) -> None:
        dr = device_registry.async_get(self.hass)
        er = entity_registry.async_get(self.hass)

        hub_device = dr.async_get_device(identifiers={(DOMAIN, self._entry.entry_id)})
        if hub_device is None:
            _LOGGER.warning("Hub device not found; cannot generate dashboard card")
            return

        hub_entities = [
            e
            for e in er.entities.values()
            if e.device_id == hub_device.id and e.platform == DOMAIN
        ]

        def ids_ending(domain: str, suffix: str) -> list:
            return [e for e in hub_entities if e.domain == domain and e.unique_id.endswith(suffix)]

        log_entity_id = next((e.entity_id for e in ids_ending("sensor", "_decision_log")), None)
        ai_entity_id = next((e.entity_id for e in ids_ending("sensor", "_ai_advice")), None)
        old_proposed = ids_ending("sensor", "_proposed_action")  # per-miner sensors of v0.6.0
        last_action_id = next((e.entity_id for e in ids_ending("sensor", "_last_action")), None)
        activity_id = next((e.entity_id for e in ids_ending("sensor", "_activity")), None)
        apply_all_id = next((e.entity_id for e in ids_ending("button", "_apply_all")), None)
        # Shown in the activity log (or the markdown card) instead of the list.
        elsewhere = {log_entity_id, ai_entity_id, last_action_id, activity_id} | {
            e.entity_id for e in old_proposed
        }
        entity_ids = sorted(
            e.entity_id
            for e in hub_entities
            if e.domain in ("sensor", "select") and e.entity_id not in elsewhere
        )

        if not entity_ids:
            pn_create(
                self.hass,
                "No sensor entities found yet. Wait for the first poll cycle and try again.",
                title="Solar Smart Miner — Add to Dashboard",
                notification_id=f"{DOMAIN}_add_to_dashboard",
            )
            return

        cards = [
            _child_card(
                ["type: entities", f"title: {self._entry.title}", "entities:"]
                + [f"  - {eid}" for eid in entity_ids]
            )
        ]
        if activity_id:
            cards.append(_activity_card(activity_id))
        if apply_all_id:
            cards.append(_apply_button_card(apply_all_id))
        if log_entity_id:
            mode = self._entry.options.get(CONF_CONTROL_MODE, DEFAULT_CONTROL_MODE)
            cards.append(_decision_log_card(log_entity_id, ai_entity_id, mode))
        if len(cards) == 1:
            lines = [line[4:] for line in cards[0]]  # a lone card needs no stack around it
        else:
            lines = ["type: vertical-stack", "cards:"] + [line for card in cards for line in card]
        yaml_card = "\n".join(lines)

        pn_create(
            self.hass,
            (
                "Copy the YAML below and paste it into a new dashboard card "
                "(Edit dashboard → Add card → Manual):\n\n"
                f"```yaml\n{yaml_card}\n```"
            ),
            title="Solar Smart Miner — Add to Dashboard",
            notification_id=f"{DOMAIN}_add_to_dashboard",
        )


class AskAiButton(ButtonEntity):
    """Ask the AI advisor about the current readings right now."""

    _attr_has_entity_name = True
    _attr_name = "Ask AI now"
    _attr_icon = "mdi:robot-happy-outline"

    def __init__(self, entry: ConfigEntry) -> None:
        self._entry = entry
        self._attr_unique_id = f"{entry.entry_id}_ask_ai"
        self._attr_device_info = _hub_device_info(entry)

    async def async_press(self) -> None:
        coordinator: SolarMinerCoordinator = self._entry.runtime_data
        coordinator.async_ask_ai_now()


def _raise_if_turned_away(name: str, result: CommandResult) -> None:
    """Show a refused or failed command to whoever pressed the button."""
    if result.status in (RESULT_REFUSED, RESULT_FAILED) and not result.notified:
        raise HomeAssistantError(f"{name}: {result.reason}")


class ApplyAllButton(CoordinatorEntity[SolarMinerCoordinator], ButtonEntity):
    """Carry out the farm's proposal as shown: stops and step-downs first, then step-ups and starts.

    The proposal is one bundle worked out from one power budget, so it is applied whole or not at all.
    """

    _attr_has_entity_name = True
    _attr_name = "Apply proposal"
    _attr_icon = "mdi:check-all"

    def __init__(self, coordinator: SolarMinerCoordinator, entry: ConfigEntry) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = f"{entry.entry_id}_apply_all"
        self._attr_device_info = _hub_device_info(entry)

    @property
    def available(self) -> bool:
        return super().available and self.coordinator.can_apply_any()

    async def async_press(self) -> None:
        results = await self.coordinator.async_apply_all()
        if not results:
            raise HomeAssistantError("There is nothing to apply")
        for result in results.values():
            _raise_if_turned_away("Apply all", result)


def _child_card(lines: list[str]) -> list[str]:
    """Card lines as a child of a vertical-stack: first line gets the list dash."""
    return [("  - " if i == 0 else "    ") + line for i, line in enumerate(lines)]


def _quoted(text: str) -> str:
    return json.dumps(text, ensure_ascii=False)  # a double-quoted YAML string


def _apply_button_card(button_id: str) -> list[str]:
    """The Apply button, under the activity log. Its press asks for confirmation first."""
    return [
        "  - type: button",
        f"    entity: {button_id}",
        '    name: "Apply proposal"',
        "    icon: mdi:check-circle-outline",
        "    show_state: false",
        "    tap_action:",
        "      action: perform-action",
        "      perform_action: button.press",
        "      target:",
        f"        entity_id: {button_id}",
        "      confirmation:",
        '        text: "Apply the proposal shown above?"',
    ]


def _activity_card(activity_id: str) -> list[str]:
    """The activity log: the farm's proposal now, then proposals and applied actions."""
    return [
        "  - type: markdown",
        "    title: Activity log",
        "    content: |",
        f"      **Proposal now:** {{{{ state_attr('{activity_id}', 'proposal') }}}}",
        "",
        "      **Latest first**",
        f"      {{% for e in (state_attr('{activity_id}', 'feed') or [])[:15] %}}",
        "      - `{{ e.time }}` "
        "{% if e.kind == 'applied' %}**{{ e.miner }}** applied {{ e.plan }}: **{{ e.result }}**"
        "{% if e.reason and e.result != 'ok' %} ({{ e.reason }}){% endif %}"
        "{% else %}Proposal: {{ e.plan }}"
        "{% if e.current %} **◀ current**{% endif %}{% endif %}",
        "      {% endfor %}",
    ]


def _decision_log_card(
    entity_id: str, ai_entity_id: str | None = None, control_mode: str = CONTROL_MODE_PREVIEW
) -> list[str]:
    """Markdown card (as vertical-stack child lines) rendering the decision trace."""
    ai_lines = (
        [
            "",
            "      **AI advice**",
            f"      {{{{ state_attr('{ai_entity_id}', 'response') or states('{ai_entity_id}') }}}}",
            "",
            "      **AI log (latest first)**",
            f"      {{% for h in (state_attr('{ai_entity_id}', 'history') or [])[:8] %}}",
            "      - `{{ h.time }}` {{ h.error or h.summary }}"
            "{% for a in h.actions %} · {{ a.miner }} {{ a.action }} ({{ a.reason }}){% endfor %}",
            "      {% endfor %}",
        ]
        if ai_entity_id
        else []
    )
    return [
        "  - type: markdown",
        f"    title: Decision log ({_MODE_TITLES.get(control_mode, 'preview, not applied')})",
        "    content: |",
        f"      **{{{{ states('{entity_id}') }}}}**",
        "",
        f"      {{% for line in state_attr('{entity_id}', 'trace') or [] %}}",
        "      {{ '**' ~ line ~ '**' if line.isupper() else '- ' ~ line }}",
        "      {% endfor %}",
        "",
        "      **Recent changes**",
        f"      {{% for h in state_attr('{entity_id}', 'history') or [] %}}",
        "      - `{{ h.time }}` {{ h.summary }}",
        "      {% endfor %}",
        *ai_lines,
    ]
