"""Doorbell button presses from Reolink's Baichuan event stream."""

from __future__ import annotations

import asyncio
import logging
from typing import ClassVar

from homeassistant.components.event import (
    DoorbellEventType,
    EventDeviceClass,
    EventEntity,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_PASSWORD, CONF_USERNAME
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.device_registry import DeviceInfo as HADeviceInfo
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from reolink_aio.api import Host
from reolink_aio.exceptions import CredentialsInvalidError, ReolinkError

from .api import VideolinkAuthError, VideolinkError
from .camera import device_identifier
from .const import (
    CONF_CHANNEL,
    CONF_VERIFY_SSL,
    DEFAULT_CHANNEL,
    DEFAULT_VERIFY_SSL,
    DOMAIN,
)
from .runtime import VideolinkRuntime

_LOGGER = logging.getLogger(__name__)
_RETRY_SECONDS = 30
_CHECK_SECONDS = 60


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry[VideolinkRuntime],
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Add one ring event entity for the configured doorbell channel."""
    async_add_entities([VideolinkDoorbellRing(entry, hass)])


class VideolinkDoorbellRing(EventEntity):
    """Report a ring for each visitor signal's rising edge."""

    _attr_has_entity_name = True
    _attr_name = "Doorbell"
    _attr_should_poll = False
    _attr_available = False
    _attr_device_class = EventDeviceClass.DOORBELL
    _attr_event_types: ClassVar[list[str]] = [DoorbellEventType.RING]

    def __init__(self, entry: ConfigEntry[VideolinkRuntime], hass: HomeAssistant) -> None:
        super().__init__()
        self._entry = entry
        self._client = entry.runtime_data.client
        self._failure_logged = False
        self._channel = entry.data.get(CONF_CHANNEL, DEFAULT_CHANNEL)
        if entry.unique_id is None:
            raise ValueError("Videolink config entry has no unique ID")
        identifier = device_identifier(entry.unique_id, self._channel)
        self._attr_unique_id = f"{identifier}_channel_{self._channel}_ring"
        self._attr_device_info = HADeviceInfo(identifiers={(DOMAIN, identifier)})
        self._pressed = False
        self._task: asyncio.Task[None] | None = None
        self._host_ready = False
        self._callback_id = f"{DOMAIN}_{entry.entry_id}_ring"
        self._host = Host(
            entry.runtime_data.client.host,
            entry.data[CONF_USERNAME],
            entry.data[CONF_PASSWORD],
            port=entry.runtime_data.client.port,
            use_https=True,
            aiohttp_get_session_callback=lambda: async_get_clientsession(
                hass, verify_ssl=entry.data.get(CONF_VERIFY_SSL, DEFAULT_VERIFY_SSL)
            ),
        )

    @callback
    def _handle_push(self) -> None:
        """The Baichuan library has already updated its visitor state."""
        pressed = self._host.visitor_detected(self._channel)
        recovered = not self._attr_available
        self._attr_available = True
        self._failure_logged = False
        if pressed and not self._pressed:
            self._trigger_event(DoorbellEventType.RING)
            self.async_write_ha_state()
        elif recovered:
            self.async_write_ha_state()
        self._pressed = pressed

    @callback
    def _set_subscription_available(self, available: bool) -> None:
        if available != self._attr_available:
            self._attr_available = available
            if not available:
                self._pressed = False
            self.async_write_ha_state()
        if available:
            if self._failure_logged:
                _LOGGER.info("Doorbell event subscription recovered")
            self._failure_logged = False
        elif not self._failure_logged:
            _LOGGER.warning("Doorbell event subscription unavailable; reconnecting")
            self._failure_logged = True

    async def async_added_to_hass(self) -> None:
        """Listen for presses independently of the camera's video session."""
        await super().async_added_to_hass()
        self._host.baichuan.register_callback(
            self._callback_id, self._handle_push, cmd_id=33, channel=self._channel
        )
        self._task = self._entry.async_create_background_task(
            self.hass, self._listen(), "Videolink doorbell event subscription"
        )

    async def _listen(self) -> None:
        """Maintain the camera's push subscription until the entity is removed."""
        while True:
            try:
                if not self._host_ready:
                    # The Baichuan decoder needs the camera's channel map.
                    await self._host.get_host_data()
                    self._host_ready = True
                await self._host.baichuan.subscribe_events()
                self._set_subscription_available(self._host.baichuan.events_active)
                while True:
                    await asyncio.sleep(_CHECK_SECONDS)
                    await self._host.baichuan.check_subscribe_events()
                    active = self._host.baichuan.events_active
                    self._set_subscription_available(active)
                    if not active:
                        # The library retries internally and can swallow login
                        # errors. Verify credentials through the independent CGI
                        # client so those failures can still initiate reauth.
                        await self._client.validate_credentials()
            except asyncio.CancelledError:
                raise
            except (CredentialsInvalidError, VideolinkAuthError):
                self._set_subscription_available(False)
                self._entry.async_start_reauth(self.hass)
                return
            except (ReolinkError, VideolinkError, OSError):
                self._set_subscription_available(False)
                await asyncio.sleep(_RETRY_SECONDS)
            except Exception:
                self._set_subscription_available(False)
                _LOGGER.exception("Unexpected doorbell subscription error")
                return

    async def async_will_remove_from_hass(self) -> None:
        """Close the subscription and its authenticated camera connection."""
        self._host.baichuan.unregister_callback(self._callback_id)
        if self._task is not None:
            self._task.cancel()
            await asyncio.gather(self._task, return_exceptions=True)
            self._task = None
        try:
            async with asyncio.timeout(10):
                await self._host.baichuan.unsubscribe_events()
        except (ReolinkError, OSError):
            _LOGGER.debug("Unable to unsubscribe from doorbell events")
        finally:
            try:
                async with asyncio.timeout(10):
                    await self._host.logout()
            except (ReolinkError, OSError):
                _LOGGER.debug("Unable to close the doorbell event connection")
        await super().async_will_remove_from_hass()
