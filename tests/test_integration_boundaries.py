"""Exercise Home Assistant entry points with small API boundary doubles."""

from __future__ import annotations

import asyncio
import base64
import importlib.util
import sys
from pathlib import Path
from types import ModuleType, SimpleNamespace
from unittest.mock import Mock

import pytest

ROOT = Path(__file__).parents[1] / "custom_components/videolink_doorbell"
PACKAGE = "videolink_boundary_test"


def _module(monkeypatch: pytest.MonkeyPatch, name: str, **attributes) -> ModuleType:
    module = ModuleType(name)
    module.__path__ = []
    module.__dict__.update(attributes)
    monkeypatch.setitem(sys.modules, name, module)
    return module


def _load(monkeypatch: pytest.MonkeyPatch, name: str) -> ModuleType:
    spec = importlib.util.spec_from_file_location(
        f"{PACKAGE}.{name}", ROOT / f"{name}.py"
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    monkeypatch.setitem(sys.modules, spec.name, module)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def boundaries(monkeypatch: pytest.MonkeyPatch):
    """Provide only the Home Assistant interfaces used by these modules."""
    _module(monkeypatch, PACKAGE)

    class Client:
        @staticmethod
        def _normalize_host(host):
            return host.removeprefix("https://").rstrip("/")

    class ConfigFlow:
        def __init_subclass__(cls, **kwargs):
            return super().__init_subclass__()

    def identity(value, **kwargs):
        return value

    _module(
        monkeypatch,
        "voluptuous",
        Required=identity,
        Optional=identity,
        Schema=identity,
        In=identity,
        All=lambda *args: args[0],
        Coerce=identity,
        Range=lambda **kwargs: identity,
    )
    _module(monkeypatch, "homeassistant")
    _module(
        monkeypatch,
        "homeassistant.config_entries",
        ConfigFlow=ConfigFlow,
        ConfigEntry=object,
        ConfigEntryState=SimpleNamespace(LOADED="loaded"),
    )
    _module(
        monkeypatch,
        "homeassistant.data_entry_flow",
        FlowResult=dict,
        section=lambda schema, options: schema,
    )
    _module(
        monkeypatch,
        "homeassistant.const",
        CONF_HOST="host",
        CONF_PASSWORD="password",
        CONF_PORT="port",
        CONF_USERNAME="username",
    )
    _module(monkeypatch, "homeassistant.helpers")
    _module(
        monkeypatch,
        "homeassistant.helpers.aiohttp_client",
        async_get_clientsession=lambda *args, **kwargs: object(),
    )
    _module(
        monkeypatch,
        "homeassistant.helpers.selector",
        SelectSelector=lambda config: identity,
        SelectSelectorConfig=lambda **kwargs: kwargs,
    )
    _module(monkeypatch, "homeassistant.helpers.device_registry", DeviceInfo=dict)
    _module(
        monkeypatch,
        "homeassistant.helpers.entity_platform",
        AddConfigEntryEntitiesCallback=object,
    )
    _module(monkeypatch, "homeassistant.helpers.config_validation", entity_id=identity)
    registry = SimpleNamespace(async_get=Mock())
    _module(
        monkeypatch,
        "homeassistant.helpers.entity_registry",
        async_get=lambda hass: registry,
    )
    _module(monkeypatch, "homeassistant.core", HomeAssistant=object, callback=identity)
    _module(monkeypatch, "homeassistant.components")

    class EventEntity:
        def __init__(self):
            pass

        def _trigger_event(self, event_type):
            pass

        def async_write_ha_state(self):
            pass

    _module(
        monkeypatch,
        "homeassistant.components.event",
        EventEntity=EventEntity,
        DoorbellEventType=SimpleNamespace(RING="ring"),
        EventDeviceClass=SimpleNamespace(DOORBELL="doorbell"),
    )
    websocket_api = _module(
        monkeypatch,
        "homeassistant.components.websocket_api",
        websocket_command=lambda schema: identity,
        async_response=identity,
        async_register_command=Mock(),
    )
    _module(monkeypatch, "homeassistant.auth")
    _module(monkeypatch, "homeassistant.auth.permissions")
    _module(
        monkeypatch, "homeassistant.auth.permissions.const", POLICY_CONTROL="control"
    )
    _module(monkeypatch, "reolink_aio")
    _module(monkeypatch, "reolink_aio.api", Host=object)
    reolink_error = type("ReolinkError", (Exception,), {})
    _module(monkeypatch, "reolink_aio.exceptions", ReolinkError=reolink_error,
            CredentialsInvalidError=type("CredentialsInvalidError", (reolink_error,), {}))
    _module(monkeypatch, f"{PACKAGE}.runtime", VideolinkRuntime=SimpleNamespace)
    _module(
        monkeypatch,
        f"{PACKAGE}.api",
        DeviceInfo=SimpleNamespace,
        VideolinkClient=Client,
        VideolinkAuthError=type("AuthError", (Exception,), {}),
        VideolinkConnectionError=type("ConnectionError", (Exception,), {}),
        VideolinkError=type("VideolinkError", (Exception,), {}),
    )
    _module(
        monkeypatch,
        f"{PACKAGE}.const",
        CONF_CHANNEL="channel",
        CONF_RTSP_PORT="rtsp_port",
        CONF_STREAM="stream",
        CONF_VIDEO_SOURCE="video_source",
        CONF_VERIFY_SSL="verify_ssl",
        DEFAULT_CHANNEL=0,
        DEFAULT_RTSP_PORT=554,
        DEFAULT_STREAM="main",
        DEFAULT_VIDEO_SOURCE="flv",
        DEFAULT_VERIFY_SSL=False,
        DOMAIN="videolink_doorbell",
        STREAMS=("main", "sub"),
        VIDEO_SOURCES=("flv", "rtsp"),
    )
    _module(
        monkeypatch,
        f"{PACKAGE}.camera",
        device_identifier=lambda unique_id, channel: unique_id.removesuffix(
            f"_channel_{channel}"
        ),
    )
    return SimpleNamespace(
        registry=registry, websocket_api=websocket_api, Client=Client
    )


def test_visitor_rising_edges_produce_rings(
    boundaries, monkeypatch: pytest.MonkeyPatch
) -> None:
    event = _load(monkeypatch, "event")

    class Host:
        pressed = False

        def __init__(self, *args, **kwargs):
            self.baichuan = SimpleNamespace()

        def visitor_detected(self, channel):
            assert channel == 0
            return self.pressed

    monkeypatch.setattr(event, "Host", Host)
    entry = SimpleNamespace(
        data={"username": "admin", "password": "test"},
        entry_id="entry-1",
        unique_id="serial_channel_0",
        runtime_data=SimpleNamespace(client=SimpleNamespace(host="camera.local", port=443)),
    )
    ring = event.VideolinkDoorbellRing(entry, SimpleNamespace())
    trigger = Mock()
    monkeypatch.setattr(ring, "_trigger_event", trigger)
    monkeypatch.setattr(ring, "async_write_ha_state", Mock())
    for pressed in (False, True, True, False, True):
        ring._host.pressed = pressed
        ring._handle_push()
    assert trigger.call_count == 2
    trigger.assert_any_call("ring")
    assert ring.async_write_ha_state.call_count == 3


@pytest.mark.asyncio
async def test_reconfigure_video_source_updates_without_relogin(
    boundaries, monkeypatch
):
    config_flow = _load(monkeypatch, "config_flow")
    flow = config_flow.VideolinkWebConfigFlow()
    entry = SimpleNamespace(
        data={
            "host": "camera.local",
            "username": "admin",
            "password": "secret",
            "port": 443,
            "video_source": "flv",
        }
    )
    update = Mock()
    flow.hass = SimpleNamespace(
        config_entries=SimpleNamespace(async_update_entry=update)
    )
    flow._get_reconfigure_entry = lambda: entry
    flow.async_abort = lambda **kwargs: kwargs
    flow._async_validate = Mock(
        side_effect=AssertionError("source change must not reconnect")
    )
    assert await flow.async_step_reconfigure(
        {"advanced": {"video_source": "rtsp"}}
    ) == {"reason": "reconfigure_successful"}
    update.assert_called_once_with(entry, data={**entry.data, "video_source": "rtsp"})


def test_duplicate_connection_is_detected_when_serial_changes(boundaries, monkeypatch):
    config_flow = _load(monkeypatch, "config_flow")
    flow = config_flow.VideolinkWebConfigFlow()
    other = SimpleNamespace(entry_id="other", data={"host": "camera.local", "port": 443, "channel": 0})
    flow.hass = SimpleNamespace(
        config_entries=SimpleNamespace(async_entries=lambda domain: [other])
    )
    assert flow._connection_is_configured(
        {"host": "https://camera.local/", "port": 443}
    )


@pytest.mark.asyncio
async def test_websocket_denies_users_without_camera_control(boundaries, monkeypatch):
    websocket = _load(monkeypatch, "websocket")
    connection = SimpleNamespace(
        user=SimpleNamespace(
            permissions=SimpleNamespace(check_entity=lambda *args: False)
        ),
        send_error=Mock(),
        send_result=Mock(),
    )
    await websocket.websocket_native_talk(
        object(), connection, {"id": 7, "entity_id": "camera.front", "action": "start"}
    )
    connection.send_error.assert_called_once_with(
        7, "unauthorized", "Camera control permission required"
    )
    connection.send_result.assert_not_called()


@pytest.mark.asyncio
async def test_websocket_audio_requires_exact_negotiated_frame(boundaries, monkeypatch):
    websocket = _load(monkeypatch, "websocket")

    class Client(boundaries.Client):
        async def native_talk_start(self, channel, *, owner, require_owner):
            assert (channel, owner, require_owner) == (0, "owner", True)
            return SimpleNamespace(length_per_encoder=4)

        async def native_talk_audio(self, pcm, *, wait, owner):
            self.sent = (pcm, wait, owner)

    monkeypatch.setattr(websocket, "VideolinkClient", Client)
    client = Client()
    boundaries.registry.async_get.return_value = SimpleNamespace(
        domain="camera", disabled_by=None, platform="videolink_doorbell", config_entry_id="entry-1"
    )
    entry = SimpleNamespace(state="loaded", runtime_data=SimpleNamespace(client=client), data={"channel": 0})
    hass = SimpleNamespace(
        states=SimpleNamespace(get=lambda _: object()),
        config_entries=SimpleNamespace(async_get_entry=lambda entry_id: entry)
    )
    connection = SimpleNamespace(
        user=SimpleNamespace(
            permissions=SimpleNamespace(check_entity=lambda *args: True)
        ),
        send_error=Mock(),
        send_result=Mock(),
    )
    message = {
        "id": 8,
        "entity_id": "camera.front",
        "action": "audio",
        "token": "owner",
        "pcm": base64.b64encode(b"\x00" * 8).decode(),
    }
    await websocket.websocket_native_talk(hass, connection, message)
    assert client.sent == (b"\x00" * 8, False, "owner")
    connection.send_result.assert_called_once()
    connection.send_error.assert_not_called()

    message["id"] = 9
    message["pcm"] = base64.b64encode(b"\x00" * 6).decode()
    await websocket.websocket_native_talk(hass, connection, message)
    assert connection.send_error.call_args.args[:2] == (9, "invalid_format")


@pytest.mark.asyncio
async def test_start_has_cleanup_before_subscribe(boundaries, monkeypatch):
    websocket = _load(monkeypatch, "websocket")

    class Client(boundaries.Client):
        native_talk_stop = None

        async def native_talk_start(self, *args, **kwargs):
            assert connection.subscriptions
            return SimpleNamespace(sample_rate=16000, length_per_encoder=1024, audio_stream_mode="speaker")

    from unittest.mock import AsyncMock
    client = Client()
    client.native_talk_stop = AsyncMock()
    monkeypatch.setattr(websocket, "VideolinkClient", Client)
    boundaries.registry.async_get.return_value = SimpleNamespace(domain="camera", disabled_by=None, platform="videolink_doorbell", config_entry_id="entry")
    entry = SimpleNamespace(state="loaded", runtime_data=SimpleNamespace(client=client), data={})
    tasks = []
    hass = SimpleNamespace(
        states=SimpleNamespace(get=lambda _: object()),
        config_entries=SimpleNamespace(async_get_entry=lambda _: entry),
        async_create_task=lambda coro: tasks.append(asyncio.create_task(coro)),
    )
    connection = SimpleNamespace(
        user=SimpleNamespace(permissions=SimpleNamespace(check_entity=lambda *args: True)),
        subscriptions={}, send_error=Mock(), send_result=Mock(),
    )
    await websocket.websocket_native_talk(hass, connection, {"id": 1, "entity_id": "camera.front", "action": "start"})
    token = connection.send_result.call_args.args[1]["token"]
    connection.subscriptions[1]()
    await asyncio.gather(*tasks)
    client.native_talk_stop.assert_awaited_once_with(owner=token)


@pytest.mark.asyncio
@pytest.mark.parametrize("case,code", [
    ("missing_runtime", "not_ready"), ("unloaded", "not_ready"),
    ("event", "not_supported"), ("disabled", "not_supported"),
    ("not_added", "not_ready"),
])
async def test_websocket_rejects_unready_or_non_camera_entities(boundaries, monkeypatch, case, code):
    websocket = _load(monkeypatch, "websocket")
    entity = SimpleNamespace(domain="camera", disabled_by=None, platform="videolink_doorbell", config_entry_id="entry")
    boundaries.registry.async_get.return_value = entity
    entry = SimpleNamespace(state="loaded", runtime_data=SimpleNamespace(client=boundaries.Client()))
    if case == "missing_runtime":
        del entry.runtime_data
    elif case == "unloaded":
        entry.state = "not_loaded"
    elif case == "event":
        entity.domain = "event"
    elif case == "disabled":
        entity.disabled_by = "user"
    hass = SimpleNamespace(
        config_entries=SimpleNamespace(async_get_entry=lambda _: entry),
        states=SimpleNamespace(get=lambda _: None if case == "not_added" else object()),
    )
    connection = SimpleNamespace(
        user=SimpleNamespace(permissions=SimpleNamespace(check_entity=lambda *args: True)),
        send_error=Mock(), send_result=Mock(),
    )
    await websocket.websocket_native_talk(hass, connection, {"id": 1, "entity_id": "camera.front", "action": "start"})
    assert connection.send_error.call_args.args[1] == code
    connection.send_result.assert_not_called()
