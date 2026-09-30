"""Entry-owned stream sources, refresh scheduling and provider coordination."""

from __future__ import annotations

import asyncio
import logging
from datetime import timedelta
from typing import TYPE_CHECKING

from aiohttp import ClientError
from homeassistant.core import callback
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.event import async_track_time_interval

from .api import VideolinkConnectionError, VideolinkError
from .const import (
    CONF_CHANNEL,
    CONF_RTSP_PORT,
    CONF_STREAM,
    CONF_VIDEO_SOURCE,
    DEFAULT_CHANNEL,
    DEFAULT_RTSP_PORT,
    DEFAULT_STREAM,
    DEFAULT_VIDEO_SOURCE,
)
from .go2rtc import Go2RtcRegistration

if TYPE_CHECKING:
    from collections.abc import Callable

    from .camera import VideolinkWebCamera
    from .runtime import VideolinkRuntime

_LOGGER = logging.getLogger(__name__)


class VideolinkStreams:
    """Keep a single source model and one timer for an enabled camera."""

    def __init__(self, runtime: VideolinkRuntime) -> None:
        self._runtime = runtime
        self._registration = Go2RtcRegistration(runtime.hass)
        self._lock = asyncio.Lock()
        self._camera: VideolinkWebCamera | None = None
        self._cancel_interval: Callable[[], None] | None = None
        self._inflight: set[asyncio.Task] = set()
        self._updates: set[asyncio.Task] = set()
        self._refresh_task: asyncio.Task | None = None
        self._generation = 0
        self._provider_dirty = False
        self._url: str | None = None
        self._set_config(runtime.entry.data)

    @callback
    def diagnostics(self) -> dict[str, bool]:
        """Expose cached stream health without authenticated source URLs."""
        return {
            "camera_attached": self._camera is not None,
            "source_resolved": self._url is not None,
            "provider_refresh_pending": self._provider_dirty,
            "refresh_running": self._refresh_task is not None and not self._refresh_task.done(),
        }

    def _set_config(self, data) -> None:
        self._channel = data.get(CONF_CHANNEL, DEFAULT_CHANNEL)
        self._stream = data.get(CONF_STREAM, DEFAULT_STREAM)
        self._source = data.get(CONF_VIDEO_SOURCE, DEFAULT_VIDEO_SOURCE)
        self._rtsp_port = data.get(CONF_RTSP_PORT, DEFAULT_RTSP_PORT)

    @callback
    def async_attach(self, camera: VideolinkWebCamera) -> None:
        if self._runtime.closing:
            raise VideolinkConnectionError("Videolink entry is unloading")
        if self._camera is not None:
            raise RuntimeError("Videolink entry already has an enabled camera")
        self._camera = camera
        self._cancel_interval = async_track_time_interval(
            self._runtime.hass, self._schedule_refresh, timedelta(seconds=30)
        )

    async def async_detach(self, camera: VideolinkWebCamera) -> None:
        if self._camera is not camera:
            return
        if self._cancel_interval is not None:
            self._cancel_interval()
            self._cancel_interval = None
        self._camera = None
        self._url = None
        self._provider_dirty = False
        self._generation += 1
        await self._async_cancel_updates()

    async def _async_cancel_updates(self) -> None:
        tasks = self._inflight | self._updates
        if self._refresh_task is not None:
            tasks.add(self._refresh_task)
        tasks.discard(asyncio.current_task())
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        self._refresh_task = None

    @callback
    def _schedule_refresh(self, now) -> None:
        if self._refresh_task is None or self._refresh_task.done():
            self._refresh_task = self._runtime.async_create_task(
                self.async_refresh_active(now), "Videolink stream source refresh"
            )

    @property
    def backchannel_url(self) -> str:
        return self._runtime.client.rtsp_backchannel_url(self._channel, self._stream, self._rtsp_port)

    async def async_source(self, camera: VideolinkWebCamera) -> str:
        """Read the authoritative URL and update any existing HLS stream."""
        task = asyncio.current_task()
        assert task is not None
        self._inflight.add(task)
        try:
            return await self._async_source(camera)
        finally:
            self._inflight.discard(task)

    async def _async_source(self, camera: VideolinkWebCamera) -> str:
        async with self._lock:
            if self._runtime.closing:
                raise VideolinkConnectionError("Videolink entry is unloading")
            generation = self._generation
            try:
                if self._source == "rtsp":
                    url = self._runtime.client.rtsp_url(self._channel, self._stream, self._rtsp_port)
                else:
                    url = await self._runtime.client.flv_url(self._channel, self._stream)
            except VideolinkError as err:
                if not self._runtime.closing and generation == self._generation:
                    camera.async_handle_device_error(err)
                raise
            if self._runtime.closing or generation != self._generation:
                raise VideolinkConnectionError("Videolink camera is being removed")
            if url != self._url:
                self._url = url
                camera.async_update_stream_source(url)
            # Only the legacy adapter writes here. Modern Core discovers both
            # sources through the camera API and performs its own registration.
            await self._registration.async_ensure(camera, url, self.backchannel_url)
            return url

    async def async_refresh_active(self, _now=None) -> None:
        camera = self._camera
        if camera is None or self._url is None or self._source != "flv":
            return
        await self._async_refresh(camera)

    async def _async_refresh(self, camera: VideolinkWebCamera) -> None:
        task = asyncio.current_task()
        assert task is not None
        self._updates.add(task)
        try:
            old_url = self._url
            try:
                url = await self.async_source(camera)
                if url != old_url or self._provider_dirty:
                    self._provider_dirty = True
                    self._provider_dirty = not await self._registration.async_refresh_provider(camera)
            except (VideolinkError, HomeAssistantError, ClientError, TimeoutError):
                _LOGGER.debug("Camera source refresh failed; retrying at the next interval")
        finally:
            self._updates.discard(task)

    async def async_config_entry_updated(self, entry) -> None:
        async with self._lock:
            old_source = self._source
            self._set_config(entry.data)
        camera = self._camera
        if self._source != old_source and camera is not None:
            await self._async_refresh(camera)
            if self._camera is camera:
                camera.async_write_ha_state()

    async def async_close(self) -> None:
        if self._camera is not None:
            await self.async_detach(self._camera)
        else:
            await self._async_cancel_updates()
        # Wait for an already-running source refresh before final transport close.
        async with self._lock:
            self._url = None
