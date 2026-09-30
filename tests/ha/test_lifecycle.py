"""Integration lifecycle tests using real Home Assistant config entries."""

from types import MappingProxyType, SimpleNamespace
from unittest.mock import AsyncMock

import pytest

pytest.importorskip("homeassistant")
pytest.importorskip("reolink_aio")

from homeassistant.config_entries import ConfigEntry

from custom_components.videolink_doorbell import async_unload_entry


def make_entry():
    return ConfigEntry(
        domain="videolink_doorbell", title="Front", version=4, minor_version=1,
        source="user", unique_id="serial_channel_0", options={},
        data={"host": "camera.local", "port": 443, "username": "admin", "password": "test"},
        discovery_keys=MappingProxyType({}), subentries_data=(),
    )


@pytest.mark.asyncio
async def test_unload_continues_after_native_device_error():
    entry = make_entry()
    entry.runtime_data = SimpleNamespace(native_talk_stop=AsyncMock(side_effect=TimeoutError))
    unload = AsyncMock(return_value=True)
    hass = SimpleNamespace(config_entries=SimpleNamespace(async_unload_platforms=unload))
    assert await async_unload_entry(hass, entry)
    unload.assert_awaited_once()
