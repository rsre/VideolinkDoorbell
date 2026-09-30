"""Migrate legacy entries and roll back interrupted platform forwarding."""

import asyncio

import pytest

pytest.importorskip("pytest_homeassistant_custom_component")

from homeassistant.components.camera.const import DATA_COMPONENT as CAMERA_COMPONENT
from homeassistant.config_entries import ConfigEntryState
from homeassistant.helpers import entity_registry as er

from custom_components.videolink_doorbell.const import DOMAIN


@pytest.mark.parametrize("version,old_id,new_id", [
    (1, "serial", "serial_channel_0"),
    (1, "serial_channel_0", "serial_channel_0"),
    (1, "camera.local:443", "camera.local:443_channel_0"),
    (2, "camera.local:443_channel_0", "camera.local:443_channel_0"),
    (3, "serial_channel_0", "serial_channel_0"),
])
async def test_legacy_migration_preserves_existing_registry_entities(hass, entry_runtime, version, old_id, new_id):
    entry, _, _ = entry_runtime
    hass.config_entries.async_update_entry(entry, version=version, unique_id=old_id, title="Front (camera.local)")
    registry = er.async_get(hass)
    original = registry.async_get_or_create(
        "camera", DOMAIN, new_id, config_entry=entry, suggested_object_id="custom_front",
    )
    registry.async_update_entity(original.entity_id, name="My front camera")
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    assert entry.version == 4
    assert entry.unique_id == new_id
    assert entry.title == "Front"
    assert registry.async_get(original.entity_id).name == "My front camera"
    assert hass.states.get(original.entity_id).attributes["friendly_name"] == "My front camera"
    assert len(er.async_entries_for_config_entry(registry, entry.entry_id)) == 2
    assert await hass.config_entries.async_reload(entry.entry_id)
    await hass.async_block_till_done()
    assert entry.unique_id == new_id
    assert len(er.async_entries_for_config_entry(registry, entry.entry_id)) == 2
    assert await hass.config_entries.async_unload(entry.entry_id)


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
