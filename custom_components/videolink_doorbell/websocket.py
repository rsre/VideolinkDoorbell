"""Home Assistant WebSocket commands for the experimental native talk path."""

from __future__ import annotations

import base64
import binascii
import secrets

import voluptuous as vol

from homeassistant.components import websocket_api
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers import entity_registry as er

from .api import VideolinkClient
from .const import CONF_CHANNEL, DEFAULT_CHANNEL, DOMAIN


COMMAND = "videolink_doorbell/native_talk"


def async_register(hass: HomeAssistant) -> None:
    """Register native-talk WebSocket commands."""
    websocket_api.async_register_command(hass, websocket_native_talk)


@websocket_api.websocket_command(
    {
        vol.Required("type"): COMMAND,
        vol.Required("action"): vol.In({"start", "audio", "tone", "stop", "subscribe"}),
        vol.Required("entity_id"): cv.entity_id,
        vol.Optional("pcm"): str,
        vol.Optional("token"): str,
    }
)
@websocket_api.async_response
async def websocket_native_talk(hass: HomeAssistant, connection, msg: dict) -> None:
    """Start, feed, or stop one native Baichuan talk session."""
    entity = er.async_get(hass).async_get(msg["entity_id"])
    if entity is None or entity.platform != DOMAIN or not entity.config_entry_id:
        connection.send_error(msg["id"], "not_supported", "Entity is not a Videolink camera")
        return
    entry = hass.config_entries.async_get_entry(entity.config_entry_id)
    client = entry.runtime_data if entry is not None else None
    if not isinstance(client, VideolinkClient):
        connection.send_error(msg["id"], "not_ready", "Videolink camera is not ready")
        return

    try:
        action = msg["action"]
        if action == "start":
            token = secrets.token_urlsafe(24)
            config = await client.native_talk_start(
                entry.data.get(CONF_CHANNEL, DEFAULT_CHANNEL), owner=token
            )
            result = {
                "ok": True,
                "token": token,
                "sample_rate": config.sample_rate,
                "samples_per_frame": config.length_per_encoder,
                "audio_stream_mode": config.audio_stream_mode,
            }
        else:
            token = msg.get("token")
            if not token:
                raise ValueError("native talk token is required")
        if action == "audio":
            config = await client.native_talk_start(
                entry.data.get(CONF_CHANNEL, DEFAULT_CHANNEL),
                owner=token,
                require_owner=True,
            )
            pcm = _decode_pcm(msg.get("pcm"), config.length_per_encoder)
            await client.native_talk_audio(pcm, wait=False, owner=token)
            result = {"ok": True}
        elif action == "tone":
            await client.native_talk_tone(
                entry.data.get(CONF_CHANNEL, DEFAULT_CHANNEL), owner=token
            )
            result = {"ok": True}
        elif action == "stop":
            await client.native_talk_stop(owner=token)
            result = {"ok": True}
        elif action == "subscribe":
            subscription_id = msg["id"]
            active = True

            def on_mix_frame(frame) -> None:
                if not active:
                    return
                try:
                    connection.send_message({
                        "id": subscription_id,
                        "type": "event",
                        "event": {
                            "type": "videolink_doorbell/native_talk_mix",
                            "pcm": base64.b64encode(frame.cleaned_near_end or b"").decode(),
                        },
                    })
                except Exception:
                    return

            await client.native_talk_set_mix_callback(on_mix_frame, owner=token)

            def unsubscribe() -> None:
                nonlocal active
                active = False
                hass.async_create_task(client.native_talk_stop(owner=token))

            connection.subscriptions[subscription_id] = unsubscribe
            result = {"ok": True}
    except (ValueError, binascii.Error) as err:
        connection.send_error(msg["id"], "invalid_format", str(err))
        return
    except Exception as err:  # Let HA surface camera/network failures to the card.
        connection.send_error(msg["id"], "native_talk_failed", str(err))
        return
    connection.send_result(msg["id"], result)


@callback
def _decode_pcm(encoded: str | None, samples_per_frame: int) -> bytes:
    if not encoded:
        raise ValueError("pcm is required for audio messages")
    try:
        pcm = base64.b64decode(encoded, validate=True)
    except (ValueError, binascii.Error) as err:
        raise ValueError("pcm must be strict base64") from err
    expected_bytes = samples_per_frame * 2
    if len(pcm) != expected_bytes:
        raise ValueError(
            f"pcm must contain exactly {samples_per_frame} signed 16-bit samples"
        )
    return pcm
