"""Integration lifecycle tests using real Home Assistant config entries."""

from types import MappingProxyType, SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

pytest.importorskip("homeassistant")
pytest.importorskip("reolink_aio")

from homeassistant.config_entries import ConfigEntry
from homeassistant.exceptions import ConfigEntryAuthFailed, ConfigEntryNotReady

from custom_components.videolink_doorbell import async_unload_entry, backend
from custom_components.videolink_doorbell.api import (
    DeviceInfo,
    VideolinkAuthError,
    VideolinkConnectionError,
    VideolinkError,
)
from custom_components.videolink_doorbell.runtime import VideolinkRuntime


def make_entry():
    return ConfigEntry(
        domain="videolink_doorbell", title="Front", version=5, minor_version=1,
        source="user", unique_id="uid_channel_0", options={},
        data={"host": "camera.local", "port": 443, "username": "admin", "password": "test"},
        discovery_keys=MappingProxyType({}), subentries_data=(),
    )


@pytest.mark.asyncio
async def test_unload_continues_after_native_device_error():
    entry = make_entry()
    entry.runtime_data = VideolinkRuntime(
        SimpleNamespace(logout=AsyncMock(), native_talk_stop=AsyncMock(side_effect=TimeoutError)), None, None, entry
    )
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
    client = SimpleNamespace(host="camera.local", logout=AsyncMock(), device_info=AsyncMock(side_effect=error))
    monkeypatch.setattr(backend, "VideolinkClient", Mock(return_value=client))
    monkeypatch.setattr(backend, "async_get_clientsession", Mock())
    forward = AsyncMock()
    hass = SimpleNamespace(bus=SimpleNamespace(async_listen_once=Mock(return_value=Mock())),
                           config_entries=SimpleNamespace(async_forward_entry_setups=forward, async_entries=Mock(return_value=[]), async_update_entry=Mock()))
    entry = make_entry()
    with pytest.raises(expected):
        await backend.async_setup_entry(hass, entry)
    forward.assert_not_awaited()
    assert not hasattr(entry, "runtime_data")


@pytest.mark.asyncio
@pytest.mark.parametrize("serial", ["serial", "changed-serial", ""])
async def test_setup_stores_metadata_before_forwarding(monkeypatch, serial):
    info = DeviceInfo("Front", "Model", serial, "Firmware", uid="uid")
    client = SimpleNamespace(host="camera.local", logout=AsyncMock(), device_info=AsyncMock(return_value=info), device_uid=AsyncMock(return_value="uid"))
    monkeypatch.setattr(backend, "VideolinkClient", Mock(return_value=client))
    monkeypatch.setattr(backend, "async_get_clientsession", Mock())
    entry = make_entry()
    async def forward(actual_entry, platforms):
        assert actual_entry.runtime_data.client is client
        assert actual_entry.runtime_data.device_info == info
    hass = SimpleNamespace(bus=SimpleNamespace(async_listen_once=Mock(return_value=Mock())),
                           config_entries=SimpleNamespace(async_forward_entry_setups=forward, async_entries=Mock(return_value=[]), async_update_entry=Mock()))
    assert await backend.async_setup_entry(hass, entry)


@pytest.mark.asyncio
@pytest.mark.parametrize("uid", ["different-uid", ""])
async def test_setup_rejects_missing_or_changed_uid(monkeypatch, uid):
    client = SimpleNamespace(host="camera.local", logout=AsyncMock(),
        device_uid=AsyncMock(return_value=uid),
        device_info=AsyncMock(return_value=DeviceInfo("Front", "Model", "changed-serial", "FW")))
    monkeypatch.setattr(backend, "VideolinkClient", Mock(return_value=client))
    monkeypatch.setattr(backend, "async_get_clientsession", Mock())
    forward = AsyncMock()
    hass = SimpleNamespace(config_entries=SimpleNamespace(async_forward_entry_setups=forward))
    with pytest.raises(ConfigEntryNotReady, match="UID"):
        await backend.async_setup_entry(hass, make_entry())
    forward.assert_not_awaited()
    client.logout.assert_awaited_once()
