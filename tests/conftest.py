from unittest.mock import AsyncMock, patch

import pytest
from homeassistant.helpers import device_registry as dr_module, entity_registry as er_module
from pytest_homeassistant_custom_component.common import MockConfigEntry

pytest_plugins = "pytest_homeassistant_custom_component"


@pytest.fixture(autouse=True)
def auto_enable_custom_integrations(enable_custom_integrations):
    """Enable custom integrations for all tests."""
    yield


@pytest.fixture(scope="session", autouse=True)
def _start_resolver_thread_once():
    """Start pycares' process-wide helper thread before any test snapshots threads.

    aiohttp's first real session (used with aioclient_mock) lazily starts it, which the
    harness' per-test leak check would otherwise blame on whichever test comes first.
    """
    try:
        import pycares

        pycares.Channel()
    except ImportError:
        pass


@pytest.fixture(autouse=True)
def mock_openrouter():
    """Never hit the network: the AI advisor gets a canned answer.

    Tests that care about the AI call assert on / reconfigure this mock.
    """
    from custom_components.solar_smart_miner.protocols import AiAdvice

    advice = AiAdvice(
        text="Looks fine.", model="test/model", requested_at="2026-01-01T00:00:00+00:00", latency_s=0.1
    )
    with (
        patch(
            "custom_components.solar_smart_miner.coordinator.async_ask",
            AsyncMock(return_value=advice),
        ) as mock,
        # A real session would start helper threads the test harness flags as leaks.
        patch("custom_components.solar_smart_miner.coordinator.async_get_clientsession"),
    ):
        yield mock


@pytest.fixture(autouse=True)
def mock_openrouter_models():
    """The options form lists OpenRouter's free models; serve a fixed list offline."""
    with (
        patch(
            "custom_components.solar_smart_miner.config_flow.async_free_models",
            AsyncMock(
                return_value=[
                    ("openrouter/free", "Free Models Router"),
                    ("test/free:free", "Test Free"),
                ]
            ),
        ),
        patch("custom_components.solar_smart_miner.config_flow.async_get_clientsession"),
    ):
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


@pytest.fixture(autouse=True)
def isolated_ai_log_dir(tmp_path, monkeypatch):
    """The AI log lives under the HA config dir; keep tests out of the shared one."""
    monkeypatch.setattr("custom_components.solar_smart_miner.ai_log.LOG_DIR", str(tmp_path / "ai"))
