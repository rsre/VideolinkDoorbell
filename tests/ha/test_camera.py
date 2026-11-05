"""Cached stream behavior against Home Assistant's Camera base class."""

from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

pytest.importorskip("homeassistant")
pytest.importorskip("reolink_aio")

from custom_components.videolink_doorbell.api import DeviceInfo
from custom_components.videolink_doorbell.camera import VideolinkWebCamera
from custom_components.videolink_doorbell.runtime import VideolinkRuntime


@pytest.fixture
def camera():
    client = SimpleNamespace(
        base_url="https://camera.local",
        flv_url=AsyncMock(return_value="https://camera.local/flv?token=old"),
        rtsp_url=Mock(return_value="rtsp://camera.local/video"),
        device_info=AsyncMock(return_value=DeviceInfo("Front", "Model", "serial", "FW")),
        snapshot=AsyncMock(return_value=b"jpeg"),
        rtsp_backchannel_url=Mock(return_value="rtsp://camera.local/backchannel"),
    )
    entry = SimpleNamespace(data={}, title="Front", unique_id="serial_channel_0", async_start_reauth=Mock())
    entry.runtime_data = VideolinkRuntime(client, DeviceInfo("Front", "Model", "serial", "FW"), Mock(), entry)
    camera = VideolinkWebCamera(entry)
    camera._sources._registration.async_ensure = AsyncMock()
    camera._sources._registration.async_refresh_provider = AsyncMock()
    camera._sources._camera = camera
    camera.async_write_ha_state = Mock()
    camera.hass = Mock()
    return camera


@pytest.mark.asyncio
async def test_token_refresh_updates_existing_hls_stream(camera):
    old = await camera.stream_source()
    camera.stream = SimpleNamespace(source=old, update_source=Mock())
    camera._client.flv_url.return_value = "https://camera.local/flv?token=new"
    await camera._sources.async_refresh_active()
    camera.stream.update_source.assert_called_once_with("https://camera.local/flv?token=new")


@pytest.mark.asyncio
async def test_live_source_change_updates_existing_hls_stream(camera):
    old = await camera.stream_source()
    camera.stream = SimpleNamespace(source=old, update_source=Mock())
    await camera._entry.runtime_data.async_config_entry_updated(None, SimpleNamespace(data={"video_source": "rtsp"}))
    camera.stream.update_source.assert_called_once_with("rtsp://camera.local/video")


@pytest.mark.asyncio
async def test_unused_camera_does_not_refresh_urls(camera):
    await camera._sources.async_refresh_active()
    camera._client.flv_url.assert_not_awaited()


@pytest.mark.asyncio
async def test_snapshot_auth_failure_starts_reauth_and_recovery_restores_availability(camera):
    from homeassistant.exceptions import HomeAssistantError

    from custom_components.videolink_doorbell.api import VideolinkAuthError

    camera._client.snapshot.side_effect = VideolinkAuthError("Invalid credentials")
    with pytest.raises(HomeAssistantError):
        await camera.async_camera_image()
    assert not camera.available
    camera._entry.async_start_reauth.assert_called_once_with(camera.hass)
    camera._client.snapshot.side_effect = None
    await camera.async_camera_image()
    assert camera.available


@pytest.mark.asyncio
@pytest.mark.parametrize("orientation_name", [
    "NO_TRANSFORM", "MIRROR", "ROTATE_180", "FLIP", "ROTATE_LEFT_AND_FLIP",
    "ROTATE_LEFT", "ROTATE_RIGHT_AND_FLIP", "ROTATE_RIGHT",
])
async def test_orientation_preserves_backchannel_through_core_provider(camera, monkeypatch, orientation_name):
    from homeassistant.components import go2rtc as core_go2rtc
    from homeassistant.components.camera import prefs
    from homeassistant.components.stream import Orientation

    from custom_components.videolink_doorbell import go2rtc as adapter

    settings = AsyncMock(return_value=SimpleNamespace(orientation=Orientation[orientation_name]))
    monkeypatch.setattr(prefs, "get_dynamic_camera_stream_settings", settings)
    monkeypatch.setattr(core_go2rtc, "get_dynamic_camera_stream_settings", settings)
    registered = {}
    async def add(name, urls):
        registered[name] = SimpleNamespace(producers=[SimpleNamespace(url=url) for url in urls])
    streams = SimpleNamespace(list=AsyncMock(side_effect=lambda: registered), add=AsyncMock(side_effect=add))
    monkeypatch.setattr(adapter, "get_streams_api", Mock(return_value=streams))
    camera._sources._registration.async_ensure = adapter.Go2RtcRegistration.async_ensure.__get__(camera._sources._registration)
    camera._client.rtsp_backchannel_url = Mock(return_value="rtsp://camera.local/backchannel")
    camera.entity_id = "camera.front"
    camera.platform = SimpleNamespace(platform_name="videolink_doorbell")
    provider = object.__new__(core_go2rtc.WebRTCProvider)
    provider._supported_schemes = {"https", "rtsp"}
    provider._hass = camera.hass
    provider._rest_client = SimpleNamespace(streams=streams)
    await provider._update_stream_source(camera)
    assert streams.add.await_count == 1
    sources = streams.add.call_args.args[1]
    assert (sources[0] == "https://camera.local/flv?token=old") == (orientation_name == "NO_TRANSFORM")
    assert "rtsp://camera.local/backchannel" in sources
    assert all(not url.startswith("ffmpeg:rtsp://camera.local/backchannel") for url in sources)


@pytest.mark.asyncio
async def test_public_sources_leave_registration_to_core(camera, monkeypatch):
    from homeassistant.components import camera as core_camera

    from custom_components.videolink_doorbell import go2rtc as adapter

    monkeypatch.setattr(adapter, "supports_multiple_sources", lambda: True)
    if not hasattr(core_camera, "CameraStreamSource"):
        monkeypatch.setattr(core_camera, "CameraStreamSource", lambda url, orientation=None: SimpleNamespace(url=url, orientation=orientation), raising=False)
    registration = camera._sources._registration
    registration.async_ensure = adapter.Go2RtcRegistration.async_ensure.__get__(registration)
    streams_api = SimpleNamespace(list=AsyncMock(), add=AsyncMock())
    monkeypatch.setattr(adapter, "get_streams_api", Mock(return_value=streams_api))
    sources = await camera.async_get_stream_sources()
    assert [source.url for source in sources] == ["https://camera.local/flv?token=old", "rtsp://camera.local/backchannel"]
    streams_api.list.assert_not_awaited()
    streams_api.add.assert_not_awaited()
    adapter.get_streams_api.assert_not_called()


@pytest.mark.asyncio
async def test_source_change_asks_core_to_refresh_and_retries_failure(camera, monkeypatch):
    from aiohttp import ClientError

    from custom_components.videolink_doorbell import go2rtc as adapter

    monkeypatch.setattr(adapter, "supports_multiple_sources", lambda: True)
    registration = camera._sources._registration
    registration.async_ensure = adapter.Go2RtcRegistration.async_ensure.__get__(registration)
    registration.async_refresh_provider = adapter.Go2RtcRegistration.async_refresh_provider.__get__(registration)
    provider = SimpleNamespace(domain="go2rtc", _update_stream_source=AsyncMock(side_effect=[ClientError(), None]))
    camera._webrtc_provider = provider
    await camera.stream_source()
    camera.stream = SimpleNamespace(source="https://camera.local/flv?token=old", update_source=Mock())
    camera._client.flv_url.return_value = "https://camera.local/flv?token=new"
    await camera._sources.async_refresh_active()
    assert camera._sources._provider_dirty
    await camera._sources.async_refresh_active()
    assert not camera._sources._provider_dirty
    assert provider._update_stream_source.await_count == 2
    camera.stream.update_source.assert_called_with("https://camera.local/flv?token=new")


@pytest.mark.asyncio
async def test_camera_removal_cancels_inflight_source_requests(camera):
    import asyncio

    entered = asyncio.Event()
    async def source(*args):
        entered.set()
        await asyncio.Event().wait()
    camera._client.flv_url.side_effect = source
    request = asyncio.create_task(camera.stream_source())
    await entered.wait()
    await camera._sources.async_detach(camera)
    assert request.cancelled()
    assert camera._sources._url is None

@pytest.mark.asyncio
async def test_failed_initial_source_recovers_on_health_interval(camera):
    from homeassistant.exceptions import HomeAssistantError

    from custom_components.videolink_doorbell.api import VideolinkConnectionError
    camera._client.flv_url.side_effect = VideolinkConnectionError("offline")
    with pytest.raises(HomeAssistantError):
        await camera.stream_source()
    assert not camera.available
    camera._client.flv_url.side_effect = None
    await camera._sources.async_refresh_active()
    assert camera.available
    camera._client.snapshot.assert_not_awaited()


def test_camera_capabilities_on_older_core(camera, monkeypatch):
    from homeassistant.components import camera as core_camera

    monkeypatch.delattr(core_camera, "CameraEntityCapabilities", raising=False)
    assert camera.camera_entity_capabilities is None
    assert camera.extra_state_attributes["rtsp_talk_available"]


@pytest.mark.parametrize("labs_enabled", [False, True])
@pytest.mark.parametrize("webrtc_available", [False, True])
def test_builtin_talk_requires_labs_and_webrtc(
    camera, monkeypatch, labs_enabled, webrtc_available
):
    import sys

    from homeassistant.components import camera as core_camera

    if not hasattr(core_camera, "CameraEntityCapabilities"):
        pytest.skip("Built-in talk requires Home Assistant 2026.11+")
    camera._webrtc_provider = SimpleNamespace(domain="go2rtc") if webrtc_available else None
    camera_module = sys.modules[core_camera.Camera.__module__]
    monkeypatch.setattr(
        camera_module, "async_is_preview_feature_enabled", lambda *args: labs_enabled
    )
    assert camera.camera_entity_capabilities.two_way_audio
    assert camera.camera_capabilities.two_way_audio is (labs_enabled and webrtc_available)


@pytest.mark.asyncio
@pytest.mark.parametrize("video_source", ["flv", "rtsp"])
async def test_builtin_talk_tracks_backchannel_failure_and_recovery(
    camera, monkeypatch, video_source
):
    from homeassistant.components import camera as core_camera

    from custom_components.videolink_doorbell import go2rtc as adapter

    if not hasattr(core_camera, "CameraEntityCapabilities"):
        pytest.skip("Built-in talk requires Home Assistant 2026.11+")
    # The legacy adapter is still used by Core 2026.11. Exercise its real
    # registration path, including a failed provider discovery and retry.
    monkeypatch.setattr(adapter, "supports_multiple_sources", lambda: False)
    monkeypatch.setattr(adapter, "get_streams_api", Mock(return_value=None))
    monkeypatch.setattr(adapter, "async_oriented_video_source", AsyncMock(side_effect=lambda hass, entity_id, url: url))
    camera.entity_id = "camera.front"
    camera.platform = SimpleNamespace(platform_name="videolink_doorbell")
    camera._sources._source = video_source
    registration = camera._sources._registration
    registration.async_ensure = adapter.Go2RtcRegistration.async_ensure.__get__(registration)

    assert camera.camera_entity_capabilities.two_way_audio
    await camera.stream_source()
    assert not camera.camera_entity_capabilities.two_way_audio
    assert not camera.extra_state_attributes["rtsp_talk_available"]
    camera.async_write_ha_state.assert_called_once()

    streams = SimpleNamespace(list=AsyncMock(return_value={}), add=AsyncMock())
    adapter.get_streams_api.return_value = streams
    await camera.stream_source()
    assert camera.camera_entity_capabilities.two_way_audio
    assert camera.extra_state_attributes["rtsp_talk_available"]
    assert camera.async_write_ha_state.call_count == 2
    sources = streams.add.call_args.args[1]
    assert sources[0] == (
        "https://camera.local/flv?token=old" if video_source == "flv"
        else "rtsp://camera.local/video"
    )
    assert "rtsp://camera.local/backchannel" in sources
