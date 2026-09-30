"""Compatibility tests for the isolated go2rtc provider boundary."""

from __future__ import annotations

import importlib.util
import sys
from enum import Enum
from pathlib import Path
from types import ModuleType, SimpleNamespace


class ConfigEntryState(Enum):
    LOADED = "loaded"
    NOT_LOADED = "not_loaded"


homeassistant = ModuleType("homeassistant")
config_entries = ModuleType("homeassistant.config_entries")
config_entries.ConfigEntryState = ConfigEntryState
core = ModuleType("homeassistant.core")
core.HomeAssistant = object
sys.modules.setdefault("homeassistant", homeassistant)
sys.modules["homeassistant.config_entries"] = config_entries
sys.modules["homeassistant.core"] = core

MODULE_PATH = (
    Path(__file__).parents[1] / "custom_components/videolink_doorbell/go2rtc.py"
)
SPEC = importlib.util.spec_from_file_location("go2rtc_adapter_under_test", MODULE_PATH)
assert SPEC is not None and SPEC.loader is not None
adapter = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = adapter
SPEC.loader.exec_module(adapter)


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


def test_returns_compatible_loaded_streams_api() -> None:
    streams = Streams()
    entry = SimpleNamespace(
        state=ConfigEntryState.LOADED,
        runtime_data=SimpleNamespace(_rest_client=SimpleNamespace(streams=streams)),
    )
    hass = SimpleNamespace(config_entries=ConfigEntries([entry]))
    assert adapter.get_streams_api(hass) is streams


def test_rejects_missing_or_incompatible_private_api() -> None:
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
