"""Compatibility adapter for go2rtc composite stream registration."""

from __future__ import annotations

from typing import Any, Protocol

from homeassistant.config_entries import ConfigEntryState
from homeassistant.core import HomeAssistant


class Go2RtcStreamsApi(Protocol):
    """Subset of the go2rtc client used by this integration."""

    async def list(self) -> dict[str, Any]:
        """List configured streams."""

    async def add(self, name: str, sources: list[str]) -> None:
        """Add or replace a stream."""


def get_streams_api(hass: HomeAssistant) -> Go2RtcStreamsApi | None:
    """Return a compatible streams API from the loaded go2rtc provider.

    Older Home Assistant versions do not expose public multiple-source camera
    registration. Keep the private-provider compatibility boundary isolated here.
    """
    for entry in hass.config_entries.async_entries("go2rtc"):
        if entry.state is not ConfigEntryState.LOADED:
            continue
        rest_client = getattr(entry.runtime_data, "_rest_client", None)
        streams = getattr(rest_client, "streams", None)
        if callable(getattr(streams, "list", None)) and callable(
            getattr(streams, "add", None)
        ):
            return streams
    return None


async def async_oriented_video_source(hass: HomeAssistant, entity_id: str, url: str) -> str:
    """Match Core's video transformation without transforming the audio backchannel.

    This compatibility boundary is needed by HA versions without multiple camera
    sources. Matching the provider's primary URL prevents it replacing the stream.
    """
    from homeassistant.components.camera.prefs import get_dynamic_camera_stream_settings
    from homeassistant.components.stream import Orientation

    orientation = (await get_dynamic_camera_stream_settings(hass, entity_id)).orientation
    if orientation is Orientation.NO_TRANSFORM:
        return url
    filters = {
        Orientation.MIRROR: "#raw=-vf hflip",
        Orientation.ROTATE_180: "#rotate=180",
        Orientation.FLIP: "#raw=-vf vflip",
        Orientation.ROTATE_LEFT_AND_FLIP: "#raw=-vf transpose=2,vflip",
        Orientation.ROTATE_LEFT: "#rotate=-90",
        Orientation.ROTATE_RIGHT_AND_FLIP: "#raw=-vf transpose=1,vflip",
        Orientation.ROTATE_RIGHT: "#rotate=90",
    }
    prefix = "" if url.startswith("ffmpeg") else "ffmpeg:"
    return f"{prefix}{url}#video=h264#audio=copy{filters[orientation]}"
