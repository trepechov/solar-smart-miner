"""Button platform for Solar Smart Miner."""
from __future__ import annotations

import logging

from homeassistant.components.button import ButtonEntity
from homeassistant.components.persistent_notification import async_create as pn_create
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers import device_registry, entity_registry
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import DOMAIN
from .coordinator import SolarMinerCoordinator

_LOGGER = logging.getLogger(__name__)


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
    async_add_entities([AddToDashboardButton(entry), AskAiButton(entry)])


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
            if e.device_id == hub_device.id
            and e.domain in ("sensor", "select")
            and e.platform == DOMAIN
        ]
        log_entity_id = next(
            (e.entity_id for e in hub_entities if e.unique_id.endswith("_decision_log")),
            None,
        )
        ai_entity_id = next(
            (e.entity_id for e in hub_entities if e.unique_id.endswith("_ai_advice")), None
        )
        entity_ids = sorted(
            e.entity_id for e in hub_entities if e.entity_id not in (log_entity_id, ai_entity_id)
        )

        if not entity_ids:
            pn_create(
                self.hass,
                "No sensor entities found yet. Wait for the first poll cycle and try again.",
                title="Solar Smart Miner — Add to Dashboard",
                notification_id=f"{DOMAIN}_add_to_dashboard",
            )
            return

        lines = ["type: entities", f"title: {self._entry.title}", "entities:"]
        for eid in entity_ids:
            lines.append(f"  - {eid}")
        if log_entity_id:
            lines = ["type: vertical-stack", "cards:"] + [
                ("  - " if i == 0 else "    ") + line for i, line in enumerate(lines)
            ]
            lines += _decision_log_card(log_entity_id, ai_entity_id)
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


def _decision_log_card(entity_id: str, ai_entity_id: str | None = None) -> list[str]:
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
        "    title: Decision log (preview — not applied)",
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
