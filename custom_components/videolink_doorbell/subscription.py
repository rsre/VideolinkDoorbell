"""Entry-owned Baichuan subscription, health and doorbell edge detection."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable
from typing import TYPE_CHECKING

from homeassistant.const import CONF_PASSWORD, CONF_USERNAME
from homeassistant.core import callback
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from reolink_aio.api import Host
from reolink_aio.exceptions import CredentialsInvalidError, ReolinkError

from .api import VideolinkAuthError, VideolinkConnectionError, VideolinkError
from .const import (
    CONF_CHANNEL,
    CONF_VERIFY_SSL,
    DEFAULT_CHANNEL,
    DEFAULT_VERIFY_SSL,
    DOMAIN,
)

if TYPE_CHECKING:
    from .runtime import VideolinkRuntime

_LOGGER = logging.getLogger(__name__)
_RETRY_SECONDS = 30
_CHECK_SECONDS = 60
_CLOSE_SECONDS = 10


class DoorbellSubscription:
    """Keep one subscription active while at least one entity consumes it."""

    def __init__(self, runtime: VideolinkRuntime) -> None:
        self._runtime = runtime
        self._channel = runtime.entry.data.get(CONF_CHANNEL, DEFAULT_CHANNEL)
        self._callback_id = f"{DOMAIN}_{runtime.entry.entry_id}_ring"
        self._listeners: set[Callable[[bool], None]] = set()
        self._lock = asyncio.Lock()
        self._host: Host | None = None
        self._task: asyncio.Task | None = None
        self._available = False
        self._pressed = False
        self._failure_logged = False

    @property
    def available(self) -> bool:
        """Whether the event transport is currently receiving device updates."""
        return self._available

    @callback
    def diagnostics(self) -> dict[str, bool | int]:
        """Expose transport health without device identity or connection details."""
        return {
            "available": self._available,
            "subscribers": len(self._listeners),
            "listener_running": self._task is not None and not self._task.done(),
        }

    async def async_subscribe(self, listener: Callable[[bool], None]) -> None:
        """Start on the first consumer; disabled entities open no transport."""
        async with self._lock:
            if self._runtime.closing:
                raise VideolinkConnectionError("Videolink entry is unloading")
            self._listeners.add(listener)
            if self._task is not None:
                return
            entry = self._runtime.entry
            hass = self._runtime.hass
            client = self._runtime.client
            host = self._host = Host(
                client.host, entry.data[CONF_USERNAME], entry.data[CONF_PASSWORD],
                port=client.port, use_https=True,
                aiohttp_get_session_callback=lambda: async_get_clientsession(
                    hass, verify_ssl=entry.data.get(CONF_VERIFY_SSL, DEFAULT_VERIFY_SSL)
                ),
            )
            host.baichuan.register_callback(
                self._callback_id, self._handle_push, cmd_id=33, channel=self._channel
            )
            self._task = self._runtime.async_create_task(
                self._listen(host), "Videolink doorbell event subscription"
            )

    async def async_unsubscribe(self, listener: Callable[[bool], None]) -> None:
        """Release the transport when its last consumer disappears."""
        async with self._lock:
            self._listeners.discard(listener)
            if not self._listeners:
                await self._async_stop()

    @callback
    def _notify(self, ring: bool = False) -> None:
        for listener in tuple(self._listeners):
            listener(ring)

    @callback
    def _handle_push(self) -> None:
        if self._host is None:
            return
        pressed = self._host.visitor_detected(self._channel)
        ring = pressed and not self._pressed
        recovered = not self._available
        self._set_available(True, notify=False)
        self._pressed = pressed
        if ring or recovered:
            self._notify(ring)

    @callback
    def _set_available(self, available: bool, *, notify: bool = True) -> None:
        changed = available != self._available
        self._available = available
        if not available:
            self._pressed = False
        if available:
            if self._failure_logged:
                _LOGGER.info("Doorbell event subscription recovered")
            self._failure_logged = False
        elif not self._failure_logged:
            _LOGGER.warning("Doorbell event subscription unavailable; reconnecting")
            self._failure_logged = True
        if changed and notify:
            self._notify()

    async def _listen(self, host: Host) -> None:
        host_ready = False
        while True:
            try:
                if not host_ready:
                    await host.get_host_data()
                    host_ready = True
                await host.baichuan.subscribe_events()
                self._set_available(host.baichuan.events_active)
                while True:
                    await asyncio.sleep(_CHECK_SECONDS)
                    await host.baichuan.check_subscribe_events()
                    active = host.baichuan.events_active
                    self._set_available(active)
                    if not active:
                        await self._runtime.client.validate_credentials()
            except asyncio.CancelledError:
                raise
            except (CredentialsInvalidError, VideolinkAuthError):
                self._set_available(False)
                self._runtime.entry.async_start_reauth(self._runtime.hass)
                return
            except (ReolinkError, VideolinkError, OSError):
                self._set_available(False)
                await asyncio.sleep(_RETRY_SECONDS)
            except Exception:
                self._set_available(False)
                _LOGGER.exception("Unexpected doorbell subscription error")
                return

    async def async_close(self) -> None:
        """Close independently of entity removal during entry shutdown."""
        async with self._lock:
            self._listeners.clear()
            await self._async_stop()

    async def _async_stop(self) -> None:
        host = self._host
        if host is None:
            return
        try:
            host.baichuan.unregister_callback(self._callback_id)
        except (ReolinkError, OSError):
            _LOGGER.debug("Unable to remove doorbell subscription callback")
        if self._task is not None:
            self._task.cancel()
            await asyncio.gather(self._task, return_exceptions=True)
            self._task = None
        try:
            async with asyncio.timeout(_CLOSE_SECONDS):
                await host.baichuan.unsubscribe_events()
        except (ReolinkError, OSError):
            _LOGGER.debug("Unable to unsubscribe from doorbell events")
        finally:
            try:
                async with asyncio.timeout(_CLOSE_SECONDS):
                    await host.logout()
            except (ReolinkError, OSError):
                _LOGGER.debug("Unable to close the doorbell event connection")
            finally:
                self._host = None
                self._available = False
                self._pressed = False
