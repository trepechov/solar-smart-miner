"""Button platform for Solar Smart Miner."""
from __future__ import annotations

import logging

from homeassistant.components.button import ButtonEntity
from homeassistant.components.persistent_notification import async_create as pn_create
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import device_registry, entity_registry
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN
from .control import RESULT_FAILED, RESULT_REFUSED, CommandResult
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
    coordinator: SolarMinerCoordinator = entry.runtime_data
    async_add_entities([AddToDashboardButton(entry), AskAiButton(entry), ApplyAllButton(coordinator, entry)])

    # Miners come from hass-miner and can appear after startup: give each one its Apply button.
    known_miner_ids: set[str] = set()

    @callback
    def _add_new_miners() -> None:
        if coordinator.data is None:
            return
        new = [m for m in coordinator.data.miners if m.miner_id not in known_miner_ids]
        known_miner_ids.update(m.miner_id for m in new)
        if new:
            async_add_entities(ApplyButton(coordinator, entry, m.miner_id, m.name) for m in new)

    _add_new_miners()
    entry.async_on_unload(coordinator.async_add_listener(_add_new_miners))


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


def _raise_if_turned_away(name: str, result: CommandResult) -> None:
    """Show a refused or failed command to whoever pressed the button."""
    if result.status in (RESULT_REFUSED, RESULT_FAILED) and not result.notified:
        raise HomeAssistantError(f"{name}: {result.reason}")


class ApplyButton(CoordinatorEntity[SolarMinerCoordinator], ButtonEntity):
    """Carry out the action proposed for one miner, as it was shown."""

    _attr_has_entity_name = True
    _attr_icon = "mdi:check-circle-outline"

    def __init__(
        self,
        coordinator: SolarMinerCoordinator,
        entry: ConfigEntry,
        miner_id: str,
        miner_name: str,
    ) -> None:
        super().__init__(coordinator)
        self._miner_id = miner_id
        self._miner_name = miner_name
        self._attr_unique_id = f"{entry.entry_id}_{miner_id.replace('.', '_')}_apply"
        self._attr_name = f"{miner_name} apply"
        self._attr_device_info = _hub_device_info(entry)

    @property
    def available(self) -> bool:
        return super().available and self.coordinator.can_apply(self._miner_id)

    async def async_press(self) -> None:
        fingerprint = self.coordinator.shown_fingerprint(self._miner_id)
        if fingerprint is None:
            raise HomeAssistantError(f"{self._miner_name}: there is no plan to apply")
        result = await self.coordinator.async_apply_shown(self._miner_id, fingerprint)
        _raise_if_turned_away(self._miner_name, result)


class ApplyAllButton(CoordinatorEntity[SolarMinerCoordinator], ButtonEntity):
    """Carry out every proposed action: stops and step-downs first, then step-ups and starts."""

    _attr_has_entity_name = True
    _attr_name = "Apply all proposals"
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
