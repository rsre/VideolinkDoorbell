"""Backend entry points, independent of the HACS dashboard card installer."""

from __future__ import annotations

import logging
from dataclasses import replace

from homeassistant.config_entries import (
    ConfigEntry,
    ConfigEntryAuthFailed,
    ConfigEntryNotReady,
)
from homeassistant.const import CONF_HOST, CONF_PASSWORD, CONF_PORT, CONF_USERNAME
from homeassistant.core import HomeAssistant
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .api import (
    VideolinkAuthError,
    VideolinkClient,
    VideolinkConnectionError,
    VideolinkError,
)
from .const import (
    CONF_CHANNEL,
    CONF_VERIFY_SSL,
    DEFAULT_CHANNEL,
    DEFAULT_VERIFY_SSL,
    DOMAIN,
    PLATFORMS,
)
from .runtime import VideolinkRuntime
from .websocket import async_register as async_register_websocket

type VideolinkConfigEntry = ConfigEntry[VideolinkRuntime]

CONFIG_SCHEMA = cv.config_entry_only_config_schema(DOMAIN)
_LOGGER = logging.getLogger(__name__)


async def async_setup(hass: HomeAssistant, config: dict) -> bool:
    """Register device-facing commands without installing dashboard assets."""
    async_register_websocket(hass)
    return True


async def async_setup_entry(hass: HomeAssistant, entry: VideolinkConfigEntry) -> bool:
    """Set up Videolink Doorbell from a config entry."""
    client = VideolinkClient(
        async_get_clientsession(hass),
        entry.data[CONF_HOST],
        entry.data[CONF_USERNAME],
        entry.data[CONF_PASSWORD],
        port=entry.data[CONF_PORT],
        verify_ssl=entry.data.get(CONF_VERIFY_SSL, DEFAULT_VERIFY_SSL),
    )
    try:
        info = replace(await client.device_info(), uid=await client.device_uid())
    except VideolinkAuthError as err:
        await client.logout()
        raise ConfigEntryAuthFailed from err
    except VideolinkConnectionError as err:
        await client.logout()
        raise ConfigEntryNotReady from err
    except VideolinkError as err:
        await client.logout()
        raise ConfigEntryNotReady("Camera returned invalid device information") from err
    channel = entry.data.get(CONF_CHANNEL, DEFAULT_CHANNEL)
    if not info.uid:
        await client.logout()
        raise ConfigEntryNotReady("Camera identity unavailable: GetP2p returned no UID")
    if entry.unique_id != f"{info.uid}_channel_{channel}":
        await client.logout()
        raise ConfigEntryNotReady("Camera UID does not match the configured device")
    runtime = entry.runtime_data = VideolinkRuntime(client, info, hass, entry)
    runtime.async_initialize()
    entry.async_on_unload(entry.add_update_listener(runtime.async_config_entry_updated))
    try:
        await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    except BaseException:
        # Forwarding may already have added entities before another platform
        # fails or setup is cancelled. Roll those back before retrying setup.
        try:
            await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
        finally:
            await runtime.async_close()
        raise
    return True


async def async_migrate_entry(hass: HomeAssistant, entry: VideolinkConfigEntry) -> bool:
    """Require recreating pre-UID entries; no serial-based identity migration."""
    _LOGGER.error("Videolink now uses camera UIDs; remove and re-add this integration entry")
    return False


async def async_unload_entry(hass: HomeAssistant, entry: VideolinkConfigEntry) -> bool:
    """Unload a config entry."""
    if not await hass.config_entries.async_unload_platforms(entry, PLATFORMS):
        return False
    await entry.runtime_data.async_close()
    return True
