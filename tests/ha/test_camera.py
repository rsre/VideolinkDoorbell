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
    )
    entry = SimpleNamespace(data={}, title="Front", unique_id="serial_channel_0")
    camera = VideolinkWebCamera(entry, client, DeviceInfo("Front", "Model", "serial", "FW"))
    camera._async_register_go2rtc_sources = AsyncMock()
    camera.async_write_ha_state = Mock()
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
