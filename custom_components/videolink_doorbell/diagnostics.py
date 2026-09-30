"""Redacted, local-only Home Assistant diagnostics."""

from collections.abc import Mapping
from typing import Any

from homeassistant.components.diagnostics import async_redact_data
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_HOST, CONF_PASSWORD, CONF_PORT, CONF_USERNAME
from homeassistant.core import HomeAssistant

from .const import (
    CONF_CHANNEL,
    CONF_RTSP_PORT,
    CONF_STREAM,
    CONF_VERIFY_SSL,
    CONF_VIDEO_SOURCE,
)
from .runtime import VideolinkRuntime

_CONFIG_FIELDS = frozenset({
    CONF_HOST, CONF_PASSWORD, CONF_PORT, CONF_USERNAME, CONF_CHANNEL,
    CONF_RTSP_PORT, CONF_STREAM, CONF_VERIFY_SSL, CONF_VIDEO_SOURCE,
})
_TO_REDACT = frozenset({CONF_HOST, CONF_PASSWORD, CONF_USERNAME, "name", "serial"})


def _configuration(data: Mapping[str, Any]) -> dict[str, Any]:
    """Allow only known settings so future tokens/URLs cannot enter diagnostics."""
    return async_redact_data(
        {key: value for key, value in data.items() if key in _CONFIG_FIELDS},
        _TO_REDACT,
    )


async def async_get_config_entry_diagnostics(
    hass: HomeAssistant, entry: ConfigEntry[VideolinkRuntime],
) -> dict[str, Any]:
    """Report configuration and cached health without device I/O or raw audio."""
    diagnostics: dict[str, Any] = {
        "entry": {
            "version": entry.version,
            "minor_version": entry.minor_version,
            "state": entry.state.value,
            "data": _configuration(entry.data),
            "options": _configuration(entry.options),
        },
        "device": None,
        "runtime": None,
    }
    if (runtime := getattr(entry, "runtime_data", None)) is not None:
        info = runtime.device_info
        diagnostics["device"] = async_redact_data({
            "name": info.name, "model": info.model,
            "serial": info.serial, "firmware": info.firmware,
        }, _TO_REDACT)
        diagnostics["runtime"] = runtime.diagnostics()
    return diagnostics
