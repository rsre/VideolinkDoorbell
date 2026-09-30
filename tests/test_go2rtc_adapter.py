"""Compatibility tests for the isolated go2rtc provider boundary."""

from __future__ import annotations

import importlib.util
import sys
from enum import Enum
from pathlib import Path
from types import ModuleType, SimpleNamespace

import pytest


class ConfigEntryState(Enum):
    LOADED = "loaded"
    NOT_LOADED = "not_loaded"


@pytest.fixture
def adapter(monkeypatch):
    """Restore every boundary double after the test, including on real HA."""
    homeassistant = ModuleType("homeassistant")
    config_entries = ModuleType("homeassistant.config_entries")
    config_entries.ConfigEntryState = ConfigEntryState
    core = ModuleType("homeassistant.core")
    core.HomeAssistant = object
    monkeypatch.setitem(sys.modules, "homeassistant", homeassistant)
    monkeypatch.setitem(sys.modules, "homeassistant.config_entries", config_entries)
    monkeypatch.setitem(sys.modules, "homeassistant.core", core)
    path = Path(__file__).parents[1] / "custom_components/videolink_doorbell/go2rtc.py"
    spec = importlib.util.spec_from_file_location("go2rtc_adapter_under_test", path)
    module = importlib.util.module_from_spec(spec)
    monkeypatch.setitem(sys.modules, spec.name, module)
    spec.loader.exec_module(module)
    return module


class ConfigEntries:
    def __init__(self, entries) -> None:
        self.entries = entries

    def async_entries(self, domain: str):
        assert domain == "go2rtc"
        return self.entries


class Streams:
    async def list(self):
        return {}

    async def add(self, name, sources):
        return None


def test_returns_compatible_loaded_streams_api(adapter) -> None:
    streams = Streams()
    entry = SimpleNamespace(
        state=ConfigEntryState.LOADED,
        runtime_data=SimpleNamespace(_rest_client=SimpleNamespace(streams=streams)),
    )
    hass = SimpleNamespace(config_entries=ConfigEntries([entry]))
    assert adapter.get_streams_api(hass) is streams


def test_rejects_missing_or_incompatible_private_api(adapter) -> None:
    entries = [
        SimpleNamespace(state=ConfigEntryState.NOT_LOADED, runtime_data=None),
        SimpleNamespace(
            state=ConfigEntryState.LOADED,
            runtime_data=SimpleNamespace(
                _rest_client=SimpleNamespace(streams=object())
            ),
        ),
    ]
    hass = SimpleNamespace(config_entries=ConfigEntries(entries))
    assert adapter.get_streams_api(hass) is None
