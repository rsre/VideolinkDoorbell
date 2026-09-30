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
    _attr_device_class = EventDeviceClass.DOORBELL
    _attr_event_types: ClassVar[list[str]] = [DoorbellEventType.RING]

    def __init__(self, entry: ConfigEntry[VideolinkRuntime], hass: HomeAssistant) -> None:
        super().__init__()
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
        if pressed and not self._pressed:
            self._trigger_event(DoorbellEventType.RING)
            self.async_write_ha_state()
        self._pressed = pressed

    async def async_added_to_hass(self) -> None:
        """Listen for presses independently of the camera's video session."""
        await super().async_added_to_hass()
        self._host.baichuan.register_callback(
            self._callback_id, self._handle_push, cmd_id=33, channel=self._channel
        )
        self._task = asyncio.create_task(self._listen())

    async def _listen(self) -> None:
        """Maintain the camera's push subscription until the entity is removed."""
        while True:
            try:
                if not self._host_ready:
                    # The Baichuan decoder needs the camera's channel map.
                    await self._host.get_host_data()
                    self._host_ready = True
                await self._host.baichuan.subscribe_events()
                while True:
                    await asyncio.sleep(_CHECK_SECONDS)
                    await self._host.baichuan.check_subscribe_events()
            except asyncio.CancelledError:
                raise
            except Exception:
                _LOGGER.exception("Doorbell event subscription failed; retrying")
                await asyncio.sleep(_RETRY_SECONDS)

    async def async_will_remove_from_hass(self) -> None:
        """Close the subscription and its authenticated camera connection."""
        self._host.baichuan.unregister_callback(self._callback_id)
        if self._task is not None:
            self._task.cancel()
            await asyncio.gather(self._task, return_exceptions=True)
            self._task = None
        try:
            await self._host.baichuan.unsubscribe_events()
        except Exception:
            _LOGGER.exception("Unable to unsubscribe from doorbell events")
        finally:
            try:
                await self._host.logout()
            except Exception:
                _LOGGER.exception("Unable to close the doorbell event connection")
        await super().async_will_remove_from_hass()
