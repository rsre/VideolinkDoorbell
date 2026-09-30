"""Lifecycle and resources owned by one Videolink config entry."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable, Coroutine
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import EVENT_HOMEASSISTANT_STOP
from homeassistant.core import HomeAssistant, callback

from .api import DeviceInfo, VideolinkClient, VideolinkConnectionError

if TYPE_CHECKING:
    from .subscription import DoorbellSubscription

_LOGGER = logging.getLogger(__name__)
_CLEANUP_TIMEOUT = 10


@dataclass(slots=True)
class VideolinkRuntime:
    """Own device I/O, startup operations, background work and final cleanup."""

    client: VideolinkClient
    device_info: DeviceInfo
    hass: HomeAssistant
    entry: ConfigEntry[VideolinkRuntime]
    closing: bool = field(default=False, init=False)
    closed: bool = field(default=False, init=False)
    _tasks: set[asyncio.Task] = field(default_factory=set, init=False)
    _starts: set[asyncio.Task] = field(default_factory=set, init=False)
    _cleanups: list[Callable[[], Awaitable[None]]] = field(default_factory=list, init=False)
    _doorbell: DoorbellSubscription | None = field(default=None, init=False)
    _shutdown_task: asyncio.Task | None = field(default=None, init=False)
    _shutdown_unsub: Callable[[], None] | None = field(default=None, init=False)

    @property
    def doorbell(self) -> DoorbellSubscription:
        """Lazily create the entry's subscription; connect only for consumers."""
        if self._doorbell is None:
            from .subscription import DoorbellSubscription

            self._doorbell = DoorbellSubscription(self)
            self.async_add_cleanup(self._doorbell.async_close)
        return self._doorbell

    @callback
    def async_initialize(self) -> None:
        """Close device transports during Home Assistant shutdown as well as unload."""
        self._shutdown_unsub = self.hass.bus.async_listen_once(
            EVENT_HOMEASSISTANT_STOP, self._async_stop
        )

    async def _async_stop(self, _event) -> None:
        await self.async_close()

    @callback
    def async_add_cleanup(self, cleanup: Callable[[], Awaitable[None]]) -> None:
        """Register an entry-owned resource's asynchronous close operation."""
        if self.closing:
            raise VideolinkConnectionError("Videolink entry is unloading")
        self._cleanups.append(cleanup)

    @callback
    def async_create_task(self, coro: Coroutine[Any, Any, None], name: str) -> asyncio.Task | None:
        """Track connection cleanup work so unloading can settle it."""
        if self.closing:
            coro.close()
            return
        task = self.entry.async_create_background_task(self.hass, coro, name)
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)
        return task

    async def async_start_native(self, channel: int, **kwargs):
        """Cancel in-flight native acquisition when this entry closes."""
        if self.closing:
            raise VideolinkConnectionError("Videolink entry is unloading")
        task = asyncio.current_task()
        assert task is not None
        self._starts.add(task)
        try:
            return await self.client.native_talk_start(channel, **kwargs)
        finally:
            self._starts.discard(task)

    async def async_stop_native(self, *, owner: str | None = None) -> None:
        """Release a native session through the entry's lifecycle boundary."""
        await self.client.native_talk_stop(owner=owner)

    async def async_close(self) -> None:
        """Idempotently close resources, even if an unload caller is cancelled."""
        if self._shutdown_task is None:
            self.closing = True
            self._shutdown_task = asyncio.create_task(
                self._async_close_resources(), name="Videolink entry cleanup"
            )
        await asyncio.shield(self._shutdown_task)

    async def _async_close_resources(self) -> None:
        if self._shutdown_unsub is not None:
            self._shutdown_unsub()
            self._shutdown_unsub = None
        tasks = self._starts | self._tasks
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        # Resource-specific cancellation and deadlines remain inside each close
        # operation; this boundary also guards native cleanup and faulty resources.
        cleanups = [*reversed(self._cleanups), self.client.native_talk_stop]
        self._cleanups.clear()
        for cleanup in cleanups:
            try:
                async with asyncio.timeout(_CLEANUP_TIMEOUT):
                    await cleanup()
            except Exception as err:  # noqa: BLE001 - finish independent resource cleanup
                _LOGGER.warning("Videolink resource cleanup failed (%s)", type(err).__name__)
        self.closed = True
