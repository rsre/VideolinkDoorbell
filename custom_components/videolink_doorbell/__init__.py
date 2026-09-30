"""HACS entry point composing the backend and bundled dashboard card installer."""

from homeassistant.core import HomeAssistant

from .backend import CONFIG_SCHEMA as CONFIG_SCHEMA
from .backend import VideolinkConfigEntry as VideolinkConfigEntry
from .backend import async_migrate_entry as async_migrate_entry
from .backend import async_setup as async_setup_backend
from .backend import async_setup_entry as async_setup_entry
from .backend import async_unload_entry as async_unload_entry
from .hacs_frontend import async_setup as async_setup_frontend


async def async_setup(hass: HomeAssistant, config: dict) -> bool:
    """Set up the backend and automatic card installation for HACS users."""
    if not await async_setup_backend(hass, config):
        return False
    await async_setup_frontend(hass)
    return True
