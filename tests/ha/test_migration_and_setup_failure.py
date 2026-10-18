"""Validate UID identities and roll back interrupted platform forwarding."""

import asyncio

import pytest

pytest.importorskip("pytest_homeassistant_custom_component")

from homeassistant.components.camera.const import DATA_COMPONENT as CAMERA_COMPONENT
from homeassistant.config_entries import ConfigEntryState
from homeassistant.helpers import entity_registry as er


@pytest.mark.parametrize("cancel", [False, True])
async def test_failed_forwarding_unloads_started_entities_and_allows_retry(hass, entry_runtime, monkeypatch, cancel):
    entry, client, host = entry_runtime
    original_forward = hass.config_entries.async_forward_entry_setups
    started = asyncio.Event()
    runtimes = []

    async def fail_after_platforms_started(actual_entry, platforms):
        await original_forward(actual_entry, platforms)
        runtimes.append(actual_entry.runtime_data)
        started.set()
        if cancel:
            await asyncio.Event().wait()
        raise RuntimeError("Simulated platform forwarding failure")

    monkeypatch.setattr(hass.config_entries, "async_forward_entry_setups", fail_after_platforms_started)
    setup = asyncio.create_task(hass.config_entries.async_setup(entry.entry_id))
    try:
        await asyncio.wait_for(started.wait(), 2)
        if cancel:
            setup.cancel()
            with pytest.raises(asyncio.CancelledError):
                await setup
        else:
            assert not await setup
        await hass.async_block_till_done()
        assert entry.state is ConfigEntryState.SETUP_ERROR
        assert runtimes[0].closed
        assert list(hass.data[CAMERA_COMPONENT].entities) == []
        entities = er.async_entries_for_config_entry(er.async_get(hass), entry.entry_id)
        assert len(entities) == 2
        assert all(hass.states.get(entity.entity_id).state == "unavailable" for entity in entities)
        host.baichuan.unregister_callback.assert_called_once()
        host.logout.assert_awaited_once()
        client.native_talk_stop.assert_awaited_once()
        monkeypatch.setattr(hass.config_entries, "async_forward_entry_setups", original_forward)
        assert await hass.config_entries.async_reload(entry.entry_id)
        await hass.async_block_till_done()
        assert entry.runtime_data is not runtimes[0]
        assert len(er.async_entries_for_config_entry(er.async_get(hass), entry.entry_id)) == 2
        assert await hass.config_entries.async_unload(entry.entry_id)
    finally:
        setup.cancel()
        await asyncio.gather(setup, return_exceptions=True)


@pytest.mark.parametrize("serial", ["changed-serial", ""])
async def test_uid_entities_survive_serial_changes(hass, entry_runtime, serial):
    from custom_components.videolink_doorbell.api import DeviceInfo

    entry, client, _ = entry_runtime
    client.device_info.return_value = DeviceInfo("Front", "Model", serial, "FW")
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    registry = er.async_get(hass)
    entities = er.async_entries_for_config_entry(registry, entry.entry_id)
    assert {entity.unique_id for entity in entities} == {"uid_channel_0", "uid_channel_0_ring"}
    original_ids = {entity.entity_id for entity in entities}
    assert entry.unique_id == "uid_channel_0"
    client.device_info.return_value = DeviceInfo("Front", "Model", "another-serial", "FW")
    assert await hass.config_entries.async_reload(entry.entry_id)
    await hass.async_block_till_done()
    assert {entity.entity_id for entity in er.async_entries_for_config_entry(registry, entry.entry_id)} == original_ids
    assert await hass.config_entries.async_unload(entry.entry_id)


async def test_pinned_uid_mismatch_does_not_rebind_entry(hass, entry_runtime):
    entry, client, _ = entry_runtime
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    saved_data = dict(entry.data)
    client.device_uid.return_value = "replacement-uid"
    assert not await hass.config_entries.async_reload(entry.entry_id)
    assert entry.state is ConfigEntryState.SETUP_RETRY
    assert entry.unique_id == "uid_channel_0"
    assert dict(entry.data) == saved_data


async def test_serial_based_entry_requires_recreation(hass, entry_runtime, caplog):
    entry, client, _ = entry_runtime
    hass.config_entries.async_update_entry(entry, version=4, unique_id="serial_channel_0")
    assert not await hass.config_entries.async_setup(entry.entry_id)
    assert entry.state is ConfigEntryState.MIGRATION_ERROR
    assert "remove and re-add" in caplog.text
    client.device_info.assert_not_awaited()
