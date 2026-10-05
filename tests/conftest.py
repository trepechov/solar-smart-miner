import pytest
from homeassistant.helpers import device_registry as dr_module, entity_registry as er_module
from pytest_homeassistant_custom_component.common import MockConfigEntry

pytest_plugins = "pytest_homeassistant_custom_component"


@pytest.fixture(autouse=True)
def auto_enable_custom_integrations(enable_custom_integrations):
    """Enable custom integrations for all tests."""
    yield


@pytest.fixture
def add_hass_miner(hass):
    """Factory that registers a miner the way hass-miner does.

    One config entry per miner (data["ip"]), one device, and entities whose
    unique_id is "<mac>-<key>". Board-level and "ideal" entities are included
    as decoys so tests prove the coordinator picks the miner-level ones.
    Returns {"entry", "device", "<key>": registry entry, ...}.
    """
    counter = iter(range(1, 1000))

    def _add(
        ip: str,
        *,
        name: str | None = None,
        power: str = "600",
        temperature: str = "65",
        limit: str = "800",
        limit_attrs: dict | None = None,
        hashrate: str | None = None,
        efficiency: str | None = None,
        entry_data: dict | None = None,
    ) -> dict:
        mac = f"00:00:00:00:00:{next(counter):02x}"
        title = name or f"Miner {ip}"
        entry = MockConfigEntry(
            domain="miner", title=title, data={"ip": ip, **(entry_data or {})}
        )
        entry.add_to_hass(hass)
        device = dr_module.async_get(hass).async_get_or_create(
            config_entry_id=entry.entry_id,
            identifiers={("miner", mac)},
            connections={("ip", ip)},
            name=title,
        )
        er = er_module.async_get(hass)
        created = {"entry": entry, "device": device}

        def reg(domain, key, state, attrs=None, device_class=None):
            reg_entry = er.async_get_or_create(
                domain,
                "miner",
                f"{mac}-{key}",
                config_entry=entry,
                device_id=device.id,
                original_device_class=device_class,
            )
            if state is not None:
                hass.states.async_set(reg_entry.entity_id, state, attrs or {})
            created[key] = reg_entry

        # Decoys first, so naive "first match" logic would pick them.
        reg("sensor", "power_limit", "1234", device_class="power")
        reg("sensor", "0-board_temperature", "99", device_class="temperature")
        reg("sensor", "ideal_hashrate", "999")
        reg("sensor", "0-board_hashrate", "888")

        reg("sensor", "miner_consumption", power, device_class="power")
        reg("sensor", "temperature", temperature, device_class="temperature")
        reg("number", "power_limit", limit,
            limit_attrs if limit_attrs is not None else {"min": 200.0, "max": 1500.0})
        if hashrate is not None:
            reg("sensor", "hashrate", hashrate)
        if efficiency is not None:
            reg("sensor", "efficiency", efficiency)
        return created

    return _add
