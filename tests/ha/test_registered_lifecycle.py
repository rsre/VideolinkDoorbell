"""Setup, reload, recovery and cleanup through Home Assistant's real managers."""

import asyncio
from unittest.mock import Mock

import pytest

pytest.importorskip("pytest_homeassistant_custom_component")

from homeassistant.config_entries import SOURCE_REAUTH, ConfigEntryState
from homeassistant.data_entry_flow import FlowResultType
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers import translation
from homeassistant.util import dt as dt_util
from pytest_homeassistant_custom_component.common import (
    async_fire_time_changed,
)

from custom_components.videolink_doorbell import config_flow, subscription
from custom_components.videolink_doorbell.api import (
    DeviceInfo,
    VideolinkAuthError,
    VideolinkClient,
    VideolinkConnectionError,
)
from custom_components.videolink_doorbell.const import DOMAIN


async def test_repeated_setup_reload_and_unload_preserves_entities(hass, entry_runtime):
    entry, client, host = entry_runtime
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


async def test_doorbell_name_comes_from_platform_translation(hass, entry_runtime, monkeypatch):
    entry, _, _ = entry_runtime
    get_translations = translation.async_get_translations

    async def translated_name(*args, **kwargs):
        translations = await get_translations(*args, **kwargs)
        key = "component.videolink_doorbell.entity.event.doorbell.name"
        if args[2] == "entity" and DOMAIN in args[3]:
            assert translations[key] == "Doorbell"
            translations = {
                **translations,
                key: "Translated doorbell",
            }
        return translations

    monkeypatch.setattr(translation, "async_get_translations", translated_name)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    entities = er.async_entries_for_config_entry(er.async_get(hass), entry.entry_id)
    doorbell = next(entity for entity in entities if entity.domain == "event")
    assert doorbell.translation_key == "doorbell"
    assert doorbell.original_name == "Translated doorbell"
    assert hass.states.get(doorbell.entity_id).attributes["friendly_name"] == "Front Translated doorbell"
    assert await hass.config_entries.async_unload(entry.entry_id)


async def test_reauth_rejects_different_camera_with_translated_abort(hass, entry_runtime, monkeypatch):
    entry, client, _ = entry_runtime
    original_data = dict(entry.data)
    client.device_info.return_value = DeviceInfo("Other", "Model", "other_serial", "FW")
    factory = Mock(return_value=client)
    factory._normalize_host = VideolinkClient._normalize_host
    monkeypatch.setattr(config_flow, "VideolinkClient", factory)
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": SOURCE_REAUTH, "entry_id": entry.entry_id}, data=entry.data,
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"username": "admin", "password": "replacement"},
    )
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "wrong_device"
    translations = await translation.async_get_translations(hass, "en", "config", {DOMAIN})
    assert translations[f"component.{DOMAIN}.config.abort.wrong_device"] == (
        "The connected camera does not match the configured device. Add it as a new integration."
    )
    assert entry.data == original_data
    assert entry.unique_id == "serial_channel_0"
    assert entry.state is ConfigEntryState.NOT_LOADED


async def test_metadata_connection_failure_recovers_without_partial_entities(hass, entry_runtime, freezer):
    entry, client, _ = entry_runtime
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


async def test_metadata_auth_failure_completes_real_reauthentication_and_reload(hass, entry_runtime, monkeypatch):
    entry, client, _ = entry_runtime
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


async def test_doorbell_network_loss_recovers_and_resumes_ring_events(hass, entry_runtime, monkeypatch, caplog):
    entry, client, host = entry_runtime
    monkeypatch.setattr(subscription, "_CHECK_SECONDS", 0)
    monkeypatch.setattr(subscription, "_RETRY_SECONDS", 0)
    lose_network, recovered, finish_check = asyncio.Event(), asyncio.Event(), asyncio.Event()

    async def check():
        await lose_network.wait()
        host.baichuan.events_active = False
        raise OSError("Camera disconnected")

    async def resubscribe():
        if host.baichuan.subscribe_events.await_count > 1:
            recovered.set()
            await finish_check.wait()
        host.baichuan.events_active = True

    host.baichuan.check_subscribe_events.side_effect = check
    host.baichuan.subscribe_events.side_effect = resubscribe
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    push = host.baichuan.register_callback.call_args.args[1]
    entity_id = next(entity.entity_id for entity in er.async_entries_for_config_entry(
        er.async_get(hass), entry.entry_id,
    ) if entity.domain == "event")
    host.visitor_detected.return_value = True
    push()
    first_ring = hass.states.get(entity_id).state
    assert hass.states.get(entity_id).attributes["event_type"] == "ring"
    lose_network.set()
    await asyncio.wait_for(recovered.wait(), 1)
    assert hass.states.get(entity_id).state == "unavailable"
    # Release the successful subscription, then park subsequent health checks.
    host.baichuan.check_subscribe_events.side_effect = asyncio.Event().wait
    finish_check.set()
    await hass.async_block_till_done()
    assert entry.runtime_data.doorbell.available
    # The outage resets the edge detector even when the last received state was
    # pressed; the first ring after reconnection must still be delivered.
    push()
    assert hass.states.get(entity_id).state not in {"unavailable", first_ring}
    assert hass.states.get(entity_id).attributes["event_type"] == "ring"
    warnings = [record for record in caplog.records if "subscription unavailable" in record.message]
    assert len(warnings) == 1
    client.validate_credentials.assert_not_awaited()
    assert await hass.config_entries.async_unload(entry.entry_id)
    host.baichuan.unregister_callback.assert_called_once()
    host.logout.assert_awaited_once()
