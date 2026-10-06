from __future__ import annotations

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant

from .const import DOMAIN  # noqa: F401
from .coordinator import SolarMinerCoordinator

# number/switch added in U7.
PLATFORMS: list[str] = ["button", "select", "sensor"]


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    coordinator = SolarMinerCoordinator(hass, entry)
    await coordinator.ai_log.async_load_history()
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
