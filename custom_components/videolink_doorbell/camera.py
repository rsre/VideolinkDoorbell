"""Camera platform for Videolink Doorbell."""

from __future__ import annotations

from homeassistant.components.camera import Camera, CameraEntityFeature
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.device_registry import DeviceInfo as HADeviceInfo
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .api import VideolinkAuthError, VideolinkError
from .const import CONF_CHANNEL, DEFAULT_CHANNEL, DOMAIN
from .helpers import device_identifier
from .runtime import VideolinkRuntime


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry[VideolinkRuntime],
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Create the camera entity."""
    async_add_entities([VideolinkWebCamera(entry)])


class VideolinkWebCamera(Camera):
    """A Videolink camera using the configured preview stream."""

    _attr_has_entity_name = True
    _attr_name = None
    _attr_supported_features = CameraEntityFeature.STREAM

    def __init__(self, entry: ConfigEntry[VideolinkRuntime]) -> None:
        super().__init__()
        self._client = entry.runtime_data.client
        self._entry = entry
        self._sources = entry.runtime_data.streams
        self._channel = entry.data.get(CONF_CHANNEL, DEFAULT_CHANNEL)
        info = entry.runtime_data.device_info
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
            configuration_url=self._client.base_url,
        )

    async def async_camera_image(
        self, width: int | None = None, height: int | None = None
    ) -> bytes | None:
        """Return the current console snapshot."""
        try:
            image = await self._client.snapshot(self._channel)
        except VideolinkError as err:
            self.async_handle_device_error(err)
            raise HomeAssistantError(str(err)) from None
        self._set_available(True)
        return image

    @callback
    def _set_available(self, available: bool) -> None:
        if available != self._attr_available:
            self._attr_available = available
            if getattr(self, "hass", None) is not None:
                self.async_write_ha_state()

    @callback
    def async_handle_device_error(self, err: VideolinkError) -> None:
        self._set_available(False)
        if isinstance(err, VideolinkAuthError):
            self._entry.async_start_reauth(self.hass)

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        self._sources.async_attach(self)

    async def async_will_remove_from_hass(self) -> None:
        await self._sources.async_detach(self)
        await super().async_will_remove_from_hass()

    @callback
    def async_update_stream_source(self, url: str) -> None:
        """Apply the entry-owned source to Home Assistant's HLS worker."""
        if self.stream is not None and self.stream.source != url:
            self.stream.update_source(url)

    async def stream_source(self) -> str:
        try:
            return await self._sources.async_source(self)
        except VideolinkError as err:
            raise HomeAssistantError(str(err)) from None

    async def async_get_stream_sources(self):
        """Expose both sources through Core's public API when it is available."""
        from homeassistant.components.camera import CameraStreamSource
        from homeassistant.components.stream import Orientation

        return [
            CameraStreamSource(await self.stream_source()),
            CameraStreamSource(
                self._sources.backchannel_url,
                orientation=Orientation.NO_TRANSFORM,
            ),
        ]
