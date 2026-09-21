"""Config flow for Videolink Doorbell."""

from __future__ import annotations

from typing import Any

import voluptuous as vol

from homeassistant import config_entries
from homeassistant.const import CONF_HOST, CONF_PASSWORD, CONF_PORT, CONF_USERNAME
from homeassistant.data_entry_flow import FlowResult
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.selector import SelectSelector, SelectSelectorConfig

from .api import (
    DeviceInfo,
    VideolinkAuthError,
    VideolinkClient,
    VideolinkConnectionError,
    VideolinkError,
)
from .const import (
    CONF_CHANNEL,
    CONF_RTSP_PORT,
    CONF_STREAM,
    CONF_VERIFY_SSL,
    DEFAULT_CHANNEL,
    DEFAULT_RTSP_PORT,
    DEFAULT_STREAM,
    DEFAULT_VERIFY_SSL,
    DOMAIN,
    STREAMS,
)


class VideolinkWebConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Handle setup through the Home Assistant UI."""

    VERSION = 4

    def _schema(self, defaults: dict[str, Any] | None = None) -> vol.Schema:
        """Build the camera configuration schema."""
        values = defaults or {}
        return vol.Schema(
            {
                vol.Required(
                    CONF_HOST, default=values.get(CONF_HOST, "192.168.1.40")
                ): str,
                vol.Required(CONF_PORT, default=values.get(CONF_PORT, 443)): vol.All(
                    vol.Coerce(int), vol.Range(min=1, max=65535)
                ),
                vol.Required(CONF_USERNAME, default=values.get(CONF_USERNAME, "")): str,
                vol.Required(CONF_PASSWORD, default=values.get(CONF_PASSWORD, "")): str,
                vol.Required(
                    CONF_STREAM, default=values.get(CONF_STREAM, DEFAULT_STREAM)
                ): SelectSelector(
                    SelectSelectorConfig(
                        options=list(STREAMS), translation_key="stream"
                    )
                ),
                vol.Required(
                    CONF_RTSP_PORT,
                    default=values.get(CONF_RTSP_PORT, DEFAULT_RTSP_PORT),
                ): vol.All(vol.Coerce(int), vol.Range(min=1, max=65535)),
                vol.Required(
                    CONF_VERIFY_SSL,
                    default=values.get(CONF_VERIFY_SSL, DEFAULT_VERIFY_SSL),
                ): bool,
            }
        )

    async def _async_validate(self, user_input: dict[str, Any]) -> DeviceInfo:
        """Validate settings and return camera identity."""
        client = VideolinkClient(
            async_get_clientsession(self.hass),
            user_input[CONF_HOST],
            user_input[CONF_USERNAME],
            user_input[CONF_PASSWORD],
            port=user_input[CONF_PORT],
            verify_ssl=user_input[CONF_VERIFY_SSL],
        )
        return await client.device_info()

    @staticmethod
    def _unique_id(info: DeviceInfo, user_input: dict[str, Any]) -> str:
        """Build the stable per-channel config-entry unique ID."""
        normalized = VideolinkClient._normalize_host(user_input[CONF_HOST])
        device_id = info.serial or f"{normalized}:{user_input[CONF_PORT]}"
        return f"{device_id}_channel_{user_input.get(CONF_CHANNEL, DEFAULT_CHANNEL)}"

    def _connection_is_configured(self, user_input: dict[str, Any]) -> bool:
        """Detect an existing entry before a changing API serial can bypass it."""
        host = VideolinkClient._normalize_host(user_input[CONF_HOST])
        port = user_input[CONF_PORT]
        channel = user_input.get(CONF_CHANNEL, DEFAULT_CHANNEL)
        return any(
            VideolinkClient._normalize_host(other.data[CONF_HOST]) == host
            and other.data[CONF_PORT] == port
            and other.data.get(CONF_CHANNEL, DEFAULT_CHANNEL) == channel
            for other in self.hass.config_entries.async_entries(DOMAIN)
        )

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Collect and validate camera settings."""
        errors: dict[str, str] = {}
        if user_input is not None:
            user_input = {**user_input, CONF_CHANNEL: DEFAULT_CHANNEL}
            try:
                info = await self._async_validate(user_input)
            except VideolinkAuthError:
                errors["base"] = "invalid_auth"
            except VideolinkConnectionError:
                errors["base"] = "cannot_connect"
            except VideolinkError:
                errors["base"] = "unknown"
            else:
                await self.async_set_unique_id(self._unique_id(info, user_input))
                self._abort_if_unique_id_configured()
                if self._connection_is_configured(user_input):
                    return self.async_abort(reason="already_configured")
                return self.async_create_entry(title=info.name, data=user_input)

        return self.async_show_form(
            step_id="user", data_schema=self._schema(), errors=errors
        )

    async def async_step_reconfigure(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Update camera connection and stream settings."""
        entry = self._get_reconfigure_entry()
        errors: dict[str, str] = {}
        if user_input is not None:
            updated = {**entry.data, **user_input}
            try:
                info = await self._async_validate(updated)
            except VideolinkAuthError:
                errors["base"] = "invalid_auth"
            except VideolinkConnectionError:
                errors["base"] = "cannot_connect"
            except VideolinkError:
                errors["base"] = "unknown"
            else:
                unique_id = self._unique_id(info, updated)
                if any(
                    other.entry_id != entry.entry_id and other.unique_id == unique_id
                    for other in self.hass.config_entries.async_entries(DOMAIN)
                ):
                    return self.async_abort(reason="already_configured")
                self.hass.config_entries.async_update_entry(
                    entry,
                    unique_id=unique_id,
                    title=info.name,
                )
                return self.async_update_reload_and_abort(
                    entry,
                    data_updates=updated,
                )
        return self.async_show_form(
            step_id="reconfigure",
            data_schema=self._schema(dict(entry.data)),
            errors=errors,
        )

    async def async_step_reauth(self, entry_data: dict[str, Any]) -> FlowResult:
        """Start credential renewal for an existing entry."""
        return await self.async_step_reauth_confirm()

    async def async_step_reauth_confirm(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Validate replacement credentials."""
        entry = self._get_reauth_entry()
        errors: dict[str, str] = {}
        if user_input is not None:
            updated = {**entry.data, **user_input}
            try:
                info = await self._async_validate(updated)
            except VideolinkAuthError:
                errors["base"] = "invalid_auth"
            except VideolinkConnectionError:
                errors["base"] = "cannot_connect"
            except VideolinkError:
                errors["base"] = "unknown"
            else:
                await self.async_set_unique_id(self._unique_id(info, updated))
                self._abort_if_unique_id_mismatch()
                return self.async_update_reload_and_abort(
                    entry, data_updates=user_input
                )
        schema = vol.Schema(
            {
                vol.Required(CONF_USERNAME, default=entry.data[CONF_USERNAME]): str,
                vol.Required(CONF_PASSWORD): str,
            }
        )
        return self.async_show_form(
            step_id="reauth_confirm", data_schema=schema, errors=errors
        )
