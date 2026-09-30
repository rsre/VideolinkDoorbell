"""Compatibility adapter for go2rtc composite stream registration."""

from __future__ import annotations

import asyncio
import logging
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
        rest_client = getattr(getattr(entry, "runtime_data", None), "_rest_client", None)
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


def supports_multiple_sources() -> bool:
    """Detect the public Core camera API, rather than parsing HA versions."""
    from homeassistant.components.camera import Camera

    return hasattr(Camera, "async_get_stream_sources")


class Go2RtcRegistration:
    """One entry's serialized compatibility boundary for stream registration."""

    def __init__(self, hass: HomeAssistant) -> None:
        self._hass = hass
        self._lock = asyncio.Lock()
        self._failed = False

    async def async_ensure(self, camera, video_url: str, backchannel_url: str) -> None:
        """Modern Core owns all writes; older Core needs a matching composite."""
        if supports_multiple_sources():
            return
        from aiohttp import ClientError
        from homeassistant.components.go2rtc.util import get_camera_identifier
        from homeassistant.exceptions import HomeAssistantError

        async with self._lock:
            streams_api = get_streams_api(self._hass)
            if streams_api is None:
                self._report_failure("provider unavailable")
                return
            try:
                oriented = await async_oriented_video_source(self._hass, camera.entity_id, video_url)
                identifier = get_camera_identifier(camera)
                streams = await streams_api.list()
                current = {producer.url for producer in streams[identifier].producers} if identifier in streams else set()
                if not {oriented, backchannel_url}.issubset(current):
                    await streams_api.add(identifier, [oriented, backchannel_url, f"ffmpeg:{identifier}#audio=opus"])
            except (ClientError, HomeAssistantError, TimeoutError) as err:
                self._report_failure(type(err).__name__)
                return
            self._failed = False

    async def async_refresh_provider(self, camera) -> bool:
        """Ask Core to refresh its sources after token/config changes.

        Core currently exposes source discovery but no public source-change hook.
        Keep this guarded provider callback in the same compatibility boundary;
        the provider remains the sole writer on versions with multiple sources.
        """
        if not supports_multiple_sources():
            return True
        provider = camera.webrtc_provider
        if provider is None or provider.domain != "go2rtc":
            return True
        update = getattr(provider, "_update_stream_source", None)
        if not callable(update):
            self._report_failure("provider source refresh API unavailable")
            return False
        async with self._lock:
            await update(camera)
            self._failed = False
            return True

    def _report_failure(self, reason: str) -> None:
        if not self._failed:
            logging.getLogger(__name__).warning("Unable to register the go2rtc talk backchannel (%s)", reason)
        self._failed = True
