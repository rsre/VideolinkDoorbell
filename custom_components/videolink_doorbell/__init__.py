"""Videolink Doorbell integration."""

from __future__ import annotations

from pathlib import Path

from homeassistant.components.frontend import add_extra_js_url
from homeassistant.components.http import StaticPathConfig
from homeassistant.config_entries import (
    ConfigEntry,
    ConfigEntryAuthFailed,
    ConfigEntryNotReady,
)
from homeassistant.const import CONF_HOST, CONF_PASSWORD, CONF_PORT, CONF_USERNAME
from homeassistant.core import HomeAssistant
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .api import VideolinkAuthError, VideolinkClient, VideolinkConnectionError
from .const import (
    CONF_CHANNEL,
    DEFAULT_CHANNEL,
    DEFAULT_VERIFY_SSL,
    DOMAIN,
    PLATFORMS,
    CONF_VERIFY_SSL,
)

type VideolinkConfigEntry = ConfigEntry[VideolinkClient]

CARD_URL = "/videolink_doorbell/videolink-doorbell.js"
CARD_PATH = Path(__file__).parent / "frontend" / "videolink-doorbell.js"
CONFIG_SCHEMA = cv.config_entry_only_config_schema(DOMAIN)


async def async_setup(hass: HomeAssistant, config: dict) -> bool:
    """Register the bundled Lovelace card once when the integration loads."""
    await hass.http.async_register_static_paths(
        [StaticPathConfig(CARD_URL, str(CARD_PATH), True)]
    )
    add_extra_js_url(hass, f"{CARD_URL}?v=0.12.1")
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
        await client.ensure_login()
    except VideolinkAuthError as err:
        raise ConfigEntryAuthFailed from err
    except VideolinkConnectionError as err:
        raise ConfigEntryNotReady from err
    entry.runtime_data = client
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True


async def async_migrate_entry(hass: HomeAssistant, entry: VideolinkConfigEntry) -> bool:
    """Include the configured channel in legacy config-entry unique IDs."""
    if entry.version == 1:
        channel = entry.data.get(CONF_CHANNEL, DEFAULT_CHANNEL)
        unique_id = entry.unique_id
        if unique_id is not None and not unique_id.endswith(f"_channel_{channel}"):
            unique_id = f"{unique_id}_channel_{channel}"
        hass.config_entries.async_update_entry(entry, unique_id=unique_id, version=2)
    return True


async def async_unload_entry(hass: HomeAssistant, entry: VideolinkConfigEntry) -> bool:
    """Unload a config entry."""
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
