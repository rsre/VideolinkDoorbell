"""Standard diagnostics never export credentials, URLs, captures or identities."""

import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

pytest.importorskip("pytest_homeassistant_custom_component")

from homeassistant.components.diagnostics import REDACTED
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.videolink_doorbell.api import DeviceInfo
from custom_components.videolink_doorbell.diagnostics import (
    async_get_config_entry_diagnostics,
)
from custom_components.videolink_doorbell.runtime import VideolinkRuntime


@pytest.fixture
def entry():
    return MockConfigEntry(
        domain="videolink_doorbell", title="private-camera-name", version=4,
        unique_id="private-camera-serial_channel_0",
        data={
            "host": "private-camera.local", "username": "private-user", "password": "private-password",
            "port": 443, "channel": 0, "stream": "sub", "video_source": "flv",
            "verify_ssl": True, "rtsp_port": 554,
            "token": "private-cgi-token", "stream_url": "rtsp://private-user:private-password@private-camera.local",
        },
        options={"password": "private-option-password", "unknown": {"audio": "private-microphone-audio"}},
    )


@pytest.mark.parametrize("with_services", [False, True])
async def test_diagnostics_are_redacted_and_do_not_contact_device(hass, entry, with_services):
    client = SimpleNamespace(
        native_talk_stop=AsyncMock(),
        native_talk_mix_diagnostics=Mock(return_value={"token": "private-native-token"}),
        native_talk_set_raw_callback=Mock(),
        rtsp_url=Mock(return_value="rtsp://private-stream-token@private-camera.local"),
        flv_url=AsyncMock(),
    )
    runtime = entry.runtime_data = VideolinkRuntime(
        client, DeviceInfo("private-camera-name", "Model", "private-camera-serial", "FW"), hass, entry,
    )
    if with_services:
        # A resolved authenticated source and an opted-in raw capture must remain
        # absent from downloads even while these services exist.
        runtime.streams._url = client.rtsp_url.return_value
        runtime.doorbell._available = True
        runtime.enable_raw_capture("private-native-token")
        callback = client.native_talk_set_raw_callback.call_args.args[0]
        header = SimpleNamespace(message_id=202, response_code=200, message_class=0x6414, channel_id=0,
                                 stream_type=0, message_number=1, body_length=2, payload_offset=0)
        callback(header, b"", b"private-microphone-audio")
        client.native_talk_mix_diagnostics.reset_mock()
        client.native_talk_set_raw_callback.reset_mock()

    result = await async_get_config_entry_diagnostics(hass, entry)
    assert "private-" not in json.dumps(result)
    assert result["entry"]["data"]["host"] == REDACTED
    assert result["entry"]["data"]["password"] == REDACTED
    assert result["entry"]["data"]["username"] == REDACTED
    assert result["entry"]["options"]["password"] == REDACTED
    assert result["entry"]["data"]["stream"] == "sub"
    assert result["device"]["serial"] == result["device"]["name"] == REDACTED
    assert result["device"]["model"] == "Model"
    if with_services:
        assert result["runtime"]["doorbell"]["available"] is True
        assert result["runtime"]["streams"]["source_resolved"] is True
    else:
        assert result["runtime"]["doorbell"] is result["runtime"]["streams"] is None
        assert runtime._doorbell is runtime._streams is None
    client.flv_url.assert_not_awaited()
    client.rtsp_url.assert_not_called()
    client.native_talk_mix_diagnostics.assert_not_called()
    client.native_talk_set_raw_callback.assert_not_called()
    await runtime.async_close()
    assert (await async_get_config_entry_diagnostics(hass, entry))["runtime"]["closed"] is True


async def test_diagnostics_before_successful_setup(hass, entry):
    result = await async_get_config_entry_diagnostics(hass, entry)
    assert result["device"] is result["runtime"] is None
    assert "private-" not in json.dumps(result)
    assert entry.data["password"] == "private-password"
