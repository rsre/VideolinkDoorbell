"""Camera platform for Videolink Doorbell."""

from __future__ import annotations

import logging
from datetime import timedelta

from homeassistant.components.camera import Camera, CameraEntityFeature
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.device_registry import DeviceInfo as HADeviceInfo
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.helpers.event import async_track_time_interval

from .api import DeviceInfo, VideolinkClient, VideolinkError
from .const import (
    CONF_CHANNEL,
    CONF_RTSP_PORT,
    CONF_STREAM,
    CONF_VIDEO_SOURCE,
    DEFAULT_CHANNEL,
    DEFAULT_RTSP_PORT,
    DEFAULT_STREAM,
    DEFAULT_VIDEO_SOURCE,
    DOMAIN,
)
from .go2rtc import get_streams_api
from .runtime import VideolinkRuntime

_LOGGER = logging.getLogger(__name__)


def device_identifier(unique_id: str, channel: int) -> str:
    """Return the stable physical-device ID from a channel config-entry ID."""
    suffix = f"_channel_{channel}"
    if not unique_id.endswith(suffix):
        raise ValueError(f"Invalid Videolink config-entry unique ID: {unique_id}")
    return unique_id[: -len(suffix)]


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry[VideolinkRuntime],
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Create the camera entity."""
    client = entry.runtime_data.client
    info = entry.runtime_data.device_info
    camera = VideolinkWebCamera(entry, client, info)
    async_add_entities([camera])
    entry.async_on_unload(entry.add_update_listener(camera.async_config_entry_updated))


class VideolinkWebCamera(Camera):
    """A Videolink camera using the configured preview stream."""

    _attr_has_entity_name = True
    _attr_name = None
    _attr_supported_features = CameraEntityFeature.STREAM

    def __init__(
        self,
        entry: ConfigEntry[VideolinkRuntime],
        client: VideolinkClient,
        info: DeviceInfo,
    ) -> None:
        super().__init__()
        self._client = client
        self._entry = entry
        self._last_video_url: str | None = None
        self._channel = entry.data.get(CONF_CHANNEL, DEFAULT_CHANNEL)
        self._stream = entry.data.get(CONF_STREAM, DEFAULT_STREAM)
        self._video_source = entry.data.get(CONF_VIDEO_SOURCE, DEFAULT_VIDEO_SOURCE)
        self._rtsp_port = entry.data.get(CONF_RTSP_PORT, DEFAULT_RTSP_PORT)
        if entry.unique_id is None:
            raise ValueError("Videolink config entry has no unique ID")
        identifier = device_identifier(entry.unique_id, self._channel)
        self._attr_unique_id = f"{identifier}_channel_{self._channel}"
        self._attr_device_info = HADeviceInfo(
            identifiers={(DOMAIN, identifier)},
            name=entry.title,
            manufacturer="Videolink",
            model=info.model,
            sw_version=info.firmware,
            configuration_url=client.base_url,
        )

    async def async_camera_image(
        self, width: int | None = None, height: int | None = None
    ) -> bytes | None:
        """Return the current console snapshot."""
        return await self._client.snapshot(self._channel)

    async def async_added_to_hass(self) -> None:
        """Keep cached tokenized sources current while this entity exists."""
        await super().async_added_to_hass()
        self.async_on_remove(async_track_time_interval(
            self.hass, self._async_refresh_active_source, timedelta(seconds=30)
        ))

    async def _async_refresh_active_source(self, _now=None) -> None:
        """Renew active FLV URLs without polling disabled or unused cameras."""
        if self._video_source != "flv" or self._last_video_url is None:
            return
        try:
            await self._async_refresh_stream_source()
        except VideolinkError:
            _LOGGER.debug("Camera source refresh failed; retrying at the next interval")

    async def _async_video_url(self) -> str:
        if self._video_source == "rtsp":
            return self._client.rtsp_url(self._channel, self._stream, self._rtsp_port)
        return await self._client.flv_url(self._channel, self._stream)

    async def _async_refresh_stream_source(self) -> str:
        """Update both cached HLS and WebRTC sources when the URL changes."""
        video_url = await self._async_video_url()
        if self.stream is not None and self.stream.source != video_url:
            self.stream.update_source(video_url)
        if video_url != self._last_video_url:
            await self._async_register_go2rtc_sources(video_url)
            self._last_video_url = video_url
        return video_url

    async def async_config_entry_updated(
        self, _hass: HomeAssistant, entry: ConfigEntry[VideolinkRuntime]
    ) -> None:
        """Apply a live video-source change without recreating the entity."""
        video_source = entry.data.get(CONF_VIDEO_SOURCE, DEFAULT_VIDEO_SOURCE)
        if video_source == self._video_source:
            return
        self._video_source = video_source
        try:
            await self._async_refresh_stream_source()
        except VideolinkError:
            _LOGGER.warning("Unable to refresh the camera video source; retrying")
        self.async_write_ha_state()

    async def stream_source(self) -> str:
        """Return the configured video source and register talkback in go2rtc."""
        previous_url = self._last_video_url
        video_url = await self._async_refresh_stream_source()
        # A provider restart may have removed an unchanged registration.
        if video_url == previous_url:
            await self._async_register_go2rtc_sources(video_url)
        return video_url

    async def _async_register_go2rtc_sources(self, video_url: str) -> None:
        """Register the console video and talk backchannel as one go2rtc stream."""
        # Home Assistant's provider currently accepts one source from Camera, but
        # go2rtc supports multiple producers. Register the composite first.
        from homeassistant.components.go2rtc.util import get_camera_identifier

        streams_api = get_streams_api(self.hass)
        if streams_api is None:
            _LOGGER.warning(
                "go2rtc is unavailable; live video remains available but two-way audio is disabled"
            )
            return

        identifier = get_camera_identifier(self)
        rtsp_url = self._client.rtsp_backchannel_url(
            self._channel, self._stream, self._rtsp_port
        )
        try:
            streams = await streams_api.list()
            expected = {video_url, rtsp_url}
            current = (
                {producer.url for producer in streams.get(identifier, ()).producers}
                if identifier in streams
                else set()
            )
            if expected.issubset(current):
                return
            await streams_api.add(
                identifier,
                [
                    video_url,
                    rtsp_url,
                    f"ffmpeg:{identifier}#audio=opus",
                ],
            )
        except Exception:
            _LOGGER.exception(
                "Unable to register the RTSP two-way-audio backchannel with go2rtc"
            )
