"""Doorbell events presented from the entry-owned subscription service."""

from __future__ import annotations

from typing import ClassVar

from homeassistant.components.event import (
    DoorbellEventType,
    EventDeviceClass,
    EventEntity,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.device_registry import DeviceInfo as HADeviceInfo
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .const import CONF_CHANNEL, DEFAULT_CHANNEL, DOMAIN
from .helpers import device_identifier
from .runtime import VideolinkRuntime


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry[VideolinkRuntime],
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Add one ring event entity for the configured doorbell channel."""
    async_add_entities([VideolinkDoorbellRing(entry)])


class VideolinkDoorbellRing(EventEntity):
    """Expose transport health and rings without managing device connections."""

    _attr_has_entity_name = True
    _attr_name = "Doorbell"
    _attr_should_poll = False
    _attr_device_class = EventDeviceClass.DOORBELL
    _attr_event_types: ClassVar[list[str]] = [DoorbellEventType.RING]

    def __init__(self, entry: ConfigEntry[VideolinkRuntime]) -> None:
        super().__init__()
        self._subscription = entry.runtime_data.doorbell
        channel = entry.data.get(CONF_CHANNEL, DEFAULT_CHANNEL)
        if entry.unique_id is None:
            raise ValueError("Videolink config entry has no unique ID")
        identifier = device_identifier(entry.unique_id, channel)
        self._attr_unique_id = f"{identifier}_channel_{channel}_ring"
        self._attr_device_info = HADeviceInfo(identifiers={(DOMAIN, identifier)})

    @property
    def available(self) -> bool:
        return self._subscription.available

    @callback
    def _handle_event(self, ring: bool) -> None:
        if ring:
            self._trigger_event(DoorbellEventType.RING)
        self.async_write_ha_state()

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        await self._subscription.async_subscribe(self._handle_event)

    async def async_will_remove_from_hass(self) -> None:
        await self._subscription.async_unsubscribe(self._handle_event)
        await super().async_will_remove_from_hass()
