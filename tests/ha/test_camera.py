"""Cached stream behavior against Home Assistant's Camera base class."""

from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

pytest.importorskip("homeassistant")
pytest.importorskip("reolink_aio")

from custom_components.videolink_doorbell.api import DeviceInfo
from custom_components.videolink_doorbell.camera import VideolinkWebCamera


@pytest.fixture
def camera():
    client = SimpleNamespace(
        base_url="https://camera.local",
        flv_url=AsyncMock(return_value="https://camera.local/flv?token=old"),
        rtsp_url=Mock(return_value="rtsp://camera.local/video"),
        snapshot=AsyncMock(return_value=b"jpeg"),
    )
    entry = SimpleNamespace(data={}, title="Front", unique_id="serial_channel_0", async_start_reauth=Mock())
    camera = VideolinkWebCamera(entry, client, DeviceInfo("Front", "Model", "serial", "FW"))
    camera._async_register_go2rtc_sources = AsyncMock()
    camera.async_write_ha_state = Mock()
    camera.hass = Mock()
    return camera


@pytest.mark.asyncio
async def test_token_refresh_updates_existing_hls_stream(camera):
    old = await camera.stream_source()
    camera.stream = SimpleNamespace(source=old, update_source=Mock())
    camera._client.flv_url.return_value = "https://camera.local/flv?token=new"
    await camera._async_refresh_active_source()
    camera.stream.update_source.assert_called_once_with("https://camera.local/flv?token=new")


@pytest.mark.asyncio
async def test_live_source_change_updates_existing_hls_stream(camera):
    old = await camera.stream_source()
    camera.stream = SimpleNamespace(source=old, update_source=Mock())
    await camera.async_config_entry_updated(None, SimpleNamespace(data={"video_source": "rtsp"}))
    camera.stream.update_source.assert_called_once_with("rtsp://camera.local/video")


@pytest.mark.asyncio
async def test_unused_camera_does_not_refresh_urls(camera):
    await camera._async_refresh_active_source()
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

    from custom_components.videolink_doorbell import camera as camera_module

    settings = AsyncMock(return_value=SimpleNamespace(orientation=Orientation[orientation_name]))
    monkeypatch.setattr(prefs, "get_dynamic_camera_stream_settings", settings)
    monkeypatch.setattr(core_go2rtc, "get_dynamic_camera_stream_settings", settings)
    registered = {}
    async def add(name, urls):
        registered[name] = SimpleNamespace(producers=[SimpleNamespace(url=url) for url in urls])
    streams = SimpleNamespace(list=AsyncMock(side_effect=lambda: registered), add=AsyncMock(side_effect=add))
    monkeypatch.setattr(camera_module, "get_streams_api", Mock(return_value=streams))
    camera._async_register_go2rtc_sources = VideolinkWebCamera._async_register_go2rtc_sources.__get__(camera)
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
