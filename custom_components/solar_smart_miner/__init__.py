from __future__ import annotations

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry

from .const import DOMAIN  # noqa: F401
from .coordinator import SolarMinerCoordinator

# number/switch added in U7.
PLATFORMS: list[str] = ["button", "select", "sensor"]

# (domain, unique-id suffix) of entities an older version created and this one no longer does.
# v0.6.0 had a proposal sensor and an Apply button per miner; the proposal is farm-level now.
RETIRED_ENTITIES: tuple[tuple[str, str], ...] = (
    ("button", "_apply"),
    ("sensor", "_proposed_action"),
)


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    _remove_retired_entities(hass, entry)
    coordinator = SolarMinerCoordinator(hass, entry)
    await coordinator.ai_log.async_load_history()
    await coordinator.action_log.async_load_history()
    coordinator.seed_activity()
    await coordinator.async_load_knowledge()
    await coordinator.async_config_entry_first_refresh()
    entry.runtime_data = coordinator
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    entry.async_on_unload(entry.add_update_listener(_async_reload_on_change))
    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)


async def _async_reload_on_change(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Apply any settings change (entities, polling interval, profile) by reloading."""
    await hass.config_entries.async_reload(entry.entry_id)


def _remove_retired_entities(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Drop registry entries an update left behind, so they don't linger as unavailable."""
    er = entity_registry.async_get(hass)
    for entity in entity_registry.async_entries_for_config_entry(er, entry.entry_id):
        if any(
            entity.domain == domain and entity.unique_id.endswith(suffix)
            for domain, suffix in RETIRED_ENTITIES
        ):
            er.async_remove(entity.entity_id)
