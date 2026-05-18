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
    async_add_entities([AddToDashboardButton(entry)])


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

        entity_ids = sorted(
            e.entity_id
            for e in er.entities.values()
            if e.device_id == hub_device.id
            and e.domain == "sensor"
            and e.platform == DOMAIN
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
