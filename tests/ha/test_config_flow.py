"""Identity and validation regressions using Home Assistant's ConfigFlow."""

from types import MappingProxyType, SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

pytest.importorskip("homeassistant")
pytest.importorskip("reolink_aio")

from homeassistant.config_entries import ConfigEntry
from homeassistant.data_entry_flow import AbortFlow

from custom_components.videolink_doorbell.api import DeviceInfo
from custom_components.videolink_doorbell.config_flow import VideolinkWebConfigFlow


@pytest.fixture
def configured_flow():
    entry = ConfigEntry(
        domain="videolink_doorbell", title="Front", version=4, minor_version=1,
        source="user", unique_id="serial_channel_0", options={},
        data={"host": "camera.local", "port": 443, "username": "admin", "password": "test"},
        discovery_keys=MappingProxyType({}), subentries_data=(),
    )
    flow = VideolinkWebConfigFlow()
    flow.context = {"source": "reconfigure", "entry_id": entry.entry_id}
    manager = SimpleNamespace(
        async_get_entry=Mock(return_value=entry),
        async_get_known_entry=Mock(return_value=entry),
        async_entries=Mock(return_value=[entry]),
        async_entry_for_domain_unique_id=Mock(return_value=None),
        async_update_entry=Mock(),
    )
    flow.hass = SimpleNamespace(config_entries=manager)
    flow._async_in_progress = Mock(return_value=[])
    flow._async_validate = AsyncMock(return_value=DeviceInfo("Front", "Model", "serial", "FW"))
    flow.async_update_reload_and_abort = Mock(return_value={"type": "abort", "reason": "reconfigure_successful"})
    return flow, entry


@pytest.mark.asyncio
async def test_reconfigure_rejects_replacement_device(configured_flow):
    flow, entry = configured_flow
    flow._async_validate.return_value = DeviceInfo("Other", "Model", "other_serial", "FW")
    with pytest.raises(AbortFlow, match="wrong_device"):
        await flow.async_step_reconfigure({"host": "other.local"})
    flow.hass.config_entries.async_update_entry.assert_not_called()
    assert entry.unique_id == "serial_channel_0"


@pytest.mark.asyncio
async def test_reconfigure_same_camera_preserves_registry_identity(configured_flow):
    flow, entry = configured_flow
    await flow.async_step_reconfigure({"host": "new.local"})
    assert entry.unique_id == "serial_channel_0"
    flow.hass.config_entries.async_update_entry.assert_called_once_with(entry, title="Front")
    flow.async_update_reload_and_abort.assert_called_once()


@pytest.mark.asyncio
async def test_reconfigure_rejects_duplicate_endpoint(configured_flow):
    flow, entry = configured_flow
    other = SimpleNamespace(entry_id="other", unique_id="different_channel_0", data={"host": "new.local", "port": 443})
    flow.hass.config_entries.async_entries.return_value = [entry, other]
    result = await flow.async_step_reconfigure({"host": "new.local"})
    assert result["reason"] == "already_configured"
    flow.async_update_reload_and_abort.assert_not_called()


@pytest.mark.asyncio
async def test_legacy_entry_keeps_identity_during_reauthentication(configured_flow):
    flow, entry = configured_flow
    object.__setattr__(entry, "unique_id", "camera.local:443_channel_0")
    flow.context["source"] = "reauth"
    await flow.async_step_reauth_confirm({"password": "replacement"})
    assert entry.unique_id == "camera.local:443_channel_0"
    flow.async_update_reload_and_abort.assert_called_once()


@pytest.mark.asyncio
async def test_new_entries_require_stable_identity(configured_flow):
    flow, _ = configured_flow
    flow.context = {"source": "user"}
    flow._async_validate.return_value = DeviceInfo("Front", "Model", "", "FW")
    result = await flow.async_step_user({"host": "camera.local", "port": 443})
    assert result["errors"] == {"base": "missing_identity"}
