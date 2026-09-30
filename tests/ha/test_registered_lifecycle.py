"""Setup, reload, recovery and cleanup through Home Assistant's real managers."""

from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

pytest.importorskip("pytest_homeassistant_custom_component")

from homeassistant.config_entries import ConfigEntryState
from homeassistant.helpers import entity_registry as er
from homeassistant.loader import async_get_integration
from homeassistant.util import dt as dt_util
from pytest_homeassistant_custom_component.common import (
    MockConfigEntry,
    async_fire_time_changed,
)

from custom_components import videolink_doorbell as integration
from custom_components.videolink_doorbell import config_flow, subscription
from custom_components.videolink_doorbell.api import (
    DeviceInfo,
    VideolinkAuthError,
    VideolinkClient,
    VideolinkConnectionError,
)
from custom_components.videolink_doorbell.const import DOMAIN


@pytest.fixture
async def runtime(hass, enable_custom_integrations, monkeypatch):
    loaded = await async_get_integration(hass, DOMAIN)
    # Frontend/go2rtc services are separate integrations; preserve actual platform
    # forwarding, registries, entry states and entity teardown in these tests.
    monkeypatch.setattr(loaded, "dependencies", [])
    monkeypatch.setattr(integration, "async_setup", AsyncMock(return_value=True))
    client = AsyncMock(spec=VideolinkClient)
    client.host = "camera.local"
    client.port = 443
    client.base_url = "https://camera.local"
    client.device_info.return_value = DeviceInfo("Front", "Model", "serial", "FW")
    monkeypatch.setattr(integration, "VideolinkClient", Mock(return_value=client))
    host = SimpleNamespace(
        get_host_data=AsyncMock(), logout=AsyncMock(), visitor_detected=Mock(return_value=False),
        baichuan=SimpleNamespace(events_active=True, register_callback=Mock(), unregister_callback=Mock(),
                                subscribe_events=AsyncMock(), check_subscribe_events=AsyncMock(), unsubscribe_events=AsyncMock()),
    )
    monkeypatch.setattr(subscription, "Host", Mock(return_value=host))
    entry = MockConfigEntry(
        domain=DOMAIN, title="Front", version=4, unique_id="serial_channel_0",
        data={"host": "camera.local", "port": 443, "username": "admin", "password": "test", "verify_ssl": True},
    )
    entry.add_to_hass(hass)
    return entry, client, host


async def test_repeated_setup_reload_and_unload_preserves_entities(hass, runtime):
    entry, client, host = runtime
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    assert entry.state is ConfigEntryState.LOADED
    registry = er.async_get(hass)
    original = {item.entity_id: item.unique_id for item in er.async_entries_for_config_entry(registry, entry.entry_id)}
    assert len(original) == 2
    assert {entity_id.split(".")[0] for entity_id in original} == {"camera", "event"}
    for _ in range(2):
        assert await hass.config_entries.async_reload(entry.entry_id)
        await hass.async_block_till_done()
        assert {item.entity_id: item.unique_id for item in er.async_entries_for_config_entry(registry, entry.entry_id)} == original
    assert await hass.config_entries.async_unload(entry.entry_id)
    await hass.async_block_till_done()
    assert entry.state is ConfigEntryState.NOT_LOADED
    assert {hass.states.get(entity_id).state for entity_id in original} == {"unavailable"}
    assert host.baichuan.register_callback.call_count == host.baichuan.unregister_callback.call_count == 3
    assert host.logout.await_count == client.native_talk_stop.await_count == 3


async def test_metadata_connection_failure_recovers_without_partial_entities(hass, runtime, freezer):
    entry, client, _ = runtime
    client.device_info.side_effect = VideolinkConnectionError("Offline")
    assert not await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    assert entry.state is ConfigEntryState.SETUP_RETRY
    assert not er.async_entries_for_config_entry(er.async_get(hass), entry.entry_id)
    client.device_info.side_effect = None
    freezer.tick(10)
    async_fire_time_changed(hass, dt_util.utcnow())
    await hass.async_block_till_done()
    # Retry setup runs as a background task; wait for its entry lock rather
    # than waiting for the long-lived doorbell listener.
    async with entry.setup_lock:
        pass
    await hass.async_block_till_done()
    assert entry.state is ConfigEntryState.LOADED
    assert len(er.async_entries_for_config_entry(er.async_get(hass), entry.entry_id)) == 2
    assert await hass.config_entries.async_unload(entry.entry_id)


async def test_metadata_auth_failure_completes_real_reauthentication_and_reload(hass, runtime, monkeypatch):
    entry, client, _ = runtime
    client.device_info.side_effect = VideolinkAuthError("Invalid credentials")
    assert not await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    assert entry.state is ConfigEntryState.SETUP_ERROR
    progress = hass.config_entries.flow.async_progress_by_handler(DOMAIN)
    assert len(progress) == 1
    assert progress[0]["context"]["source"] == "reauth"
    assert not er.async_entries_for_config_entry(er.async_get(hass), entry.entry_id)
    client.device_info.side_effect = None
    factory = Mock(return_value=client)
    factory._normalize_host = VideolinkClient._normalize_host
    monkeypatch.setattr(config_flow, "VideolinkClient", factory)
    result = await hass.config_entries.flow.async_configure(
        progress[0]["flow_id"], {"username": "admin", "password": "replacement"}
    )
    await hass.async_block_till_done()
    assert result["reason"] == "reauth_successful"
    assert entry.data["password"] == "replacement"
    assert entry.unique_id == "serial_channel_0"
    assert entry.state is ConfigEntryState.LOADED
    assert len(er.async_entries_for_config_entry(er.async_get(hass), entry.entry_id)) == 2
    assert await hass.config_entries.async_unload(entry.entry_id)
