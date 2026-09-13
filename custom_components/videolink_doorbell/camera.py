"""Camera platform for Videolink Doorbell."""

from __future__ import annotations

import logging

from homeassistant.components.camera import Camera, CameraEntityFeature
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.device_registry import DeviceInfo as HADeviceInfo
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .api import DeviceInfo, VideolinkClient
from .const import (
    CONF_CHANNEL,
    CONF_RTSP_PORT,
    CONF_STREAM,
    DEFAULT_CHANNEL,
    DEFAULT_RTSP_PORT,
    DEFAULT_STREAM,
    DOMAIN,
)
from .go2rtc import get_streams_api

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry[VideolinkClient],
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Create the camera entity."""
    client = entry.runtime_data
    info = await client.device_info()
    async_add_entities([VideolinkWebCamera(entry, client, info)])


class VideolinkWebCamera(Camera):
    """A Videolink camera using the web console API and FLV preview stream."""

    _attr_has_entity_name = True
    _attr_name = None
    _attr_supported_features = CameraEntityFeature.STREAM

    def __init__(
        self,
        entry: ConfigEntry[VideolinkClient],
        client: VideolinkClient,
        info: DeviceInfo,
    ) -> None:
        super().__init__()
        self._client = client
        self._channel = entry.data.get(CONF_CHANNEL, DEFAULT_CHANNEL)
        self._stream = entry.data.get(CONF_STREAM, DEFAULT_STREAM)
        self._rtsp_port = entry.data.get(CONF_RTSP_PORT, DEFAULT_RTSP_PORT)
        identifier = info.serial or f"{client.host}:{client.port}"
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

    async def stream_source(self) -> str:
        """Return FLV video and register an RTSP audio backchannel in go2rtc."""
        flv_url = await self._client.flv_url(self._channel, self._stream)
        await self._async_register_go2rtc_sources(flv_url)
        return flv_url

    async def _async_register_go2rtc_sources(self, flv_url: str) -> None:
        """Register the console video and talk backchannel as one go2rtc stream."""
        # Home Assistant's provider currently accepts one source from Camera, but
        # go2rtc supports multiple producers. Register the composite first; the
        # provider preserves it because the primary FLV producer already matches.
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
            expected = {flv_url, rtsp_url}
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
                    flv_url,
                    rtsp_url,
                    f"ffmpeg:{identifier}#audio=opus",
                ],
            )
        except Exception:  # noqa: BLE001 - video should survive provider failures
            _LOGGER.exception(
                "Unable to register the RTSP two-way-audio backchannel with go2rtc"
            )
