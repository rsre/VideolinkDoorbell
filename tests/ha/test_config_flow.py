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


@pytest.mark.asyncio
async def test_malformed_url_shows_actionable_flow_error(configured_flow, monkeypatch):
    from custom_components.videolink_doorbell import config_flow

    flow, _ = configured_flow
    flow.context = {"source": "user"}
    flow._async_validate = VideolinkWebConfigFlow._async_validate.__get__(flow)
    monkeypatch.setattr(config_flow, "async_get_clientsession", Mock(return_value=object()))
    result = await flow.async_step_user({
        "host": "http://[", "port": 443, "username": "admin", "password": "test", "verify_ssl": True,
    })
    assert result["errors"] == {"base": "invalid_host"}

@pytest.mark.parametrize("failure,error", [
    ("VideolinkInvalidHostError", "invalid_host"),
    ("VideolinkAuthError", "invalid_auth"),
    ("VideolinkConnectionError", "cannot_connect"),
    ("VideolinkError", "unknown"),
])
@pytest.mark.parametrize("step", ["user", "reconfigure", "reauth"])
async def test_real_flow_manager_validation_errors(hass, entry_runtime, monkeypatch, failure, error, step):
    from custom_components.videolink_doorbell import api, config_flow
    entry, client, _ = entry_runtime
    factory = Mock(return_value=client)
    factory._normalize_host = api.VideolinkClient._normalize_host
    monkeypatch.setattr(config_flow, "VideolinkClient", factory)
    client.device_info.side_effect = getattr(api, failure)("dummy error")
    context = {"source": step}
    if step != "user":
        context["entry_id"] = entry.entry_id
    result = await hass.config_entries.flow.async_init("videolink_doorbell", context=context,
        data=dict(entry.data) if step == "reauth" else None)
    values = {"host": "changed.local", "username": "admin", "password": "dummy", "stream": "main",
        "advanced": {"port": 443, "rtsp_port": 554, "video_source": "flv", "verify_ssl": True}}
    if step == "reauth":
        values = {"username": "admin", "password": "dummy"}
    result = await hass.config_entries.flow.async_configure(result["flow_id"], user_input=values)
    assert result["errors"] == {"base": error}
    client.logout.assert_awaited_once()


async def test_real_flow_manager_initial_creation(hass, entry_runtime, monkeypatch):
    from custom_components.videolink_doorbell import api, config_flow
    _, client, _ = entry_runtime
    client.device_info.return_value = DeviceInfo("New camera", "Model", "new-serial", "FW")
    factory = Mock(return_value=client)
    factory._normalize_host = api.VideolinkClient._normalize_host
    monkeypatch.setattr(config_flow, "VideolinkClient", factory)
    monkeypatch.setattr(hass.config_entries, "async_setup", AsyncMock(return_value=True))
    result = await hass.config_entries.flow.async_init("videolink_doorbell", context={"source": "user"})
    result = await hass.config_entries.flow.async_configure(result["flow_id"], user_input={
        "host": "new.local", "username": "admin", "password": "dummy", "stream": "main",
        "advanced": {"port": 443, "rtsp_port": 554, "video_source": "flv", "verify_ssl": True}})
    assert result["type"] == "create_entry"
    assert result["result"].unique_id == "new-serial_channel_0"
    client.logout.assert_awaited_once()

@pytest.fixture
async def manager_client(entry_runtime, monkeypatch):
    from custom_components.videolink_doorbell import api, config_flow
    entry, client, _ = entry_runtime
    factory = Mock(return_value=client)
    factory._normalize_host = api.VideolinkClient._normalize_host
    monkeypatch.setattr(config_flow, "VideolinkClient", factory)
    return entry, client


def form_values(host="camera.local", source="flv"):
    return {"host": host, "username": "admin", "password": "test", "stream": "main",
        "advanced": {"port": 443, "rtsp_port": 554, "video_source": source, "verify_ssl": True}}


@pytest.mark.parametrize("serial,host,reason", [
    ("serial", "new.local", "already_configured"),
    ("different", "camera.local", "already_configured"),
    ("", "new.local", "missing_identity"),
])
async def test_manager_initial_identity_and_duplicates(hass, manager_client, serial, host, reason):
    _, client = manager_client
    client.device_info.return_value = DeviceInfo("Front", "Model", serial, "FW")
    result = await hass.config_entries.flow.async_init("videolink_doorbell", context={"source": "user"})
    result = await hass.config_entries.flow.async_configure(result["flow_id"], user_input=form_values(host))
    if reason == "missing_identity":
        assert result["errors"] == {"base": reason}
    else:
        assert result["reason"] == reason


async def test_manager_source_only_reconfigure_avoids_login(hass, manager_client):
    entry, client = manager_client
    hass.config_entries.async_update_entry(entry, data={**entry.data,
        "stream": "main", "rtsp_port": 554, "video_source": "flv", "channel": 0})
    result = await hass.config_entries.flow.async_init("videolink_doorbell",
        context={"source": "reconfigure", "entry_id": entry.entry_id})
    values = form_values(source="rtsp")
    values["password"] = ""
    result = await hass.config_entries.flow.async_configure(result["flow_id"], user_input=values)
    assert result["reason"] == "reconfigure_successful"
    client.device_info.assert_not_awaited()
    assert entry.data["video_source"] == "rtsp"


@pytest.mark.parametrize("step", ["reconfigure", "reauth"])
@pytest.mark.parametrize("serial", ["", "different"])
async def test_manager_existing_identity_failures(hass, manager_client, step, serial):
    entry, client = manager_client
    client.device_info.return_value = DeviceInfo("Front", "Model", serial, "FW")
    result = await hass.config_entries.flow.async_init("videolink_doorbell",
        context={"source": step, "entry_id": entry.entry_id},
        data=dict(entry.data) if step == "reauth" else None)
    result = await hass.config_entries.flow.async_configure(result["flow_id"],
        user_input=form_values("new.local") if step == "reconfigure" else {"username": "admin", "password": "new"})
    if not serial:
        assert result["errors"] == {"base": "missing_identity"}
    else:
        assert result["reason"] == "wrong_device"


@pytest.mark.parametrize("step", ["reconfigure", "reauth"])
async def test_manager_successful_identity_preserving_update(hass, manager_client, monkeypatch, step):
    entry, client = manager_client
    monkeypatch.setattr(hass.config_entries, "async_reload", AsyncMock(return_value=True))
    result = await hass.config_entries.flow.async_init("videolink_doorbell",
        context={"source": step, "entry_id": entry.entry_id},
        data=dict(entry.data) if step == "reauth" else None)
    result = await hass.config_entries.flow.async_configure(result["flow_id"],
        user_input=form_values("new.local") if step == "reconfigure" else {"username": "admin", "password": "new"})
    assert result["reason"] == f"{step}_successful"
    assert entry.unique_id == "serial_channel_0"
    client.logout.assert_awaited_once()

async def test_manager_legacy_reconfigure_cannot_change_address(hass, manager_client):
    entry, _ = manager_client
    hass.config_entries.async_update_entry(entry, unique_id="camera.local:443_channel_0")
    result = await hass.config_entries.flow.async_init("videolink_doorbell",
        context={"source": "reconfigure", "entry_id": entry.entry_id})
    result = await hass.config_entries.flow.async_configure(result["flow_id"], user_input=form_values("new.local"))
    assert result["reason"] == "wrong_device"
