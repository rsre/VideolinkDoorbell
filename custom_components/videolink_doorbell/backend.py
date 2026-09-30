"""Backend entry points, independent of the HACS dashboard card installer."""

from __future__ import annotations

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
        info = await client.device_info()
    except VideolinkAuthError as err:
        raise ConfigEntryAuthFailed from err
    except VideolinkConnectionError as err:
        raise ConfigEntryNotReady from err
    except VideolinkError as err:
        raise ConfigEntryNotReady("Camera returned invalid device information") from err
    runtime = entry.runtime_data = VideolinkRuntime(client, info, hass, entry)
    runtime.async_initialize()
    entry.async_on_unload(entry.add_update_listener(runtime.async_config_entry_updated))
    try:
        await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    except BaseException:
        await runtime.async_close()
        raise
    return True


async def async_migrate_entry(hass: HomeAssistant, entry: VideolinkConfigEntry) -> bool:
    """Migrate legacy config-entry identities and device titles."""
    updates: dict[str, object] = {}
    if entry.version == 1:
        channel = entry.data.get(CONF_CHANNEL, DEFAULT_CHANNEL)
        unique_id = entry.unique_id
        if unique_id is not None and not unique_id.endswith(f"_channel_{channel}"):
            updates["unique_id"] = f"{unique_id}_channel_{channel}"
    if entry.version < 4:
        host = VideolinkClient._normalize_host(entry.data[CONF_HOST])
        suffix = f" ({host})"
        title = updates.get("title", entry.title)
        if isinstance(title, str) and title.endswith(suffix):
            updates["title"] = title[: -len(suffix)]
        updates["version"] = 4
    if updates:
        hass.config_entries.async_update_entry(entry, **updates)
    return True


async def async_unload_entry(hass: HomeAssistant, entry: VideolinkConfigEntry) -> bool:
    """Unload a config entry."""
    if not await hass.config_entries.async_unload_platforms(entry, PLATFORMS):
        return False
    await entry.runtime_data.async_close()
    return True
