"""Integration lifecycle tests using real Home Assistant config entries."""

from types import MappingProxyType, SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

pytest.importorskip("homeassistant")
pytest.importorskip("reolink_aio")

from homeassistant.config_entries import ConfigEntry
from homeassistant.exceptions import ConfigEntryAuthFailed, ConfigEntryNotReady

from custom_components import videolink_doorbell as integration
from custom_components.videolink_doorbell import async_unload_entry
from custom_components.videolink_doorbell.api import (
    DeviceInfo,
    VideolinkAuthError,
    VideolinkConnectionError,
    VideolinkError,
)


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
    entry.runtime_data = SimpleNamespace(client=SimpleNamespace(native_talk_stop=AsyncMock(side_effect=TimeoutError)))
    unload = AsyncMock(return_value=True)
    hass = SimpleNamespace(config_entries=SimpleNamespace(async_unload_platforms=unload))
    assert await async_unload_entry(hass, entry)
    unload.assert_awaited_once()


@pytest.mark.asyncio
@pytest.mark.parametrize("error,expected", [
    (VideolinkAuthError(), ConfigEntryAuthFailed),
    (VideolinkConnectionError(), ConfigEntryNotReady),
    (VideolinkError(), ConfigEntryNotReady),
])
async def test_metadata_failure_prevents_platform_forwarding(monkeypatch, error, expected):
    client = SimpleNamespace(device_info=AsyncMock(side_effect=error))
    monkeypatch.setattr(integration, "VideolinkClient", Mock(return_value=client))
    monkeypatch.setattr(integration, "async_get_clientsession", Mock())
    forward = AsyncMock()
    hass = SimpleNamespace(config_entries=SimpleNamespace(async_forward_entry_setups=forward))
    entry = make_entry()
    with pytest.raises(expected):
        await integration.async_setup_entry(hass, entry)
    forward.assert_not_awaited()
    assert not hasattr(entry, "runtime_data")


@pytest.mark.asyncio
async def test_setup_stores_metadata_before_forwarding(monkeypatch):
    info = DeviceInfo("Front", "Model", "serial", "Firmware")
    client = SimpleNamespace(device_info=AsyncMock(return_value=info))
    monkeypatch.setattr(integration, "VideolinkClient", Mock(return_value=client))
    monkeypatch.setattr(integration, "async_get_clientsession", Mock())
    entry = make_entry()
    async def forward(actual_entry, platforms):
        assert actual_entry.runtime_data.client is client
        assert actual_entry.runtime_data.device_info is info
    hass = SimpleNamespace(config_entries=SimpleNamespace(async_forward_entry_setups=forward))
    assert await integration.async_setup_entry(hass, entry)
