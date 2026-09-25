"""Home Assistant WebSocket commands for the experimental native talk path."""

from __future__ import annotations

import base64
import binascii
from datetime import datetime, timezone
import secrets
import time

import voluptuous as vol

from homeassistant.auth.permissions.const import POLICY_CONTROL
from homeassistant.components import websocket_api
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers import entity_registry as er

from .api import VideolinkClient
from .const import CONF_CHANNEL, DEFAULT_CHANNEL, DOMAIN


COMMAND = "videolink_doorbell/native_talk"
RAW_CAPTURE_MAX_BYTES = 16 * 1024 * 1024


def async_register(hass: HomeAssistant) -> None:
    """Register native-talk WebSocket commands."""
    websocket_api.async_register_command(hass, websocket_native_talk)


@websocket_api.websocket_command(
    {
        vol.Required("type"): COMMAND,
        vol.Required("action"): vol.In({"start", "audio", "tone", "stop", "subscribe", "diagnostics", "raw_fetch"}),
        vol.Required("entity_id"): cv.entity_id,
        vol.Optional("pcm"): str,
        vol.Optional("token"): str,
        vol.Optional("claim", default=False): bool,
        vol.Optional("dump_raw", default=False): bool,
        vol.Optional("dump_decrypted_header", default=False): bool,
        vol.Optional("cursor", default=0): vol.All(vol.Coerce(int), vol.Range(min=0)),
    }
)
@websocket_api.async_response
async def websocket_native_talk(hass: HomeAssistant, connection, msg: dict) -> None:
    """Start, feed, or stop one native Baichuan talk session."""
    if connection.user is None or not connection.user.permissions.check_entity(
        msg["entity_id"], POLICY_CONTROL
    ):
        connection.send_error(msg["id"], "unauthorized", "Camera control permission required")
        return
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
                entry.data.get(CONF_CHANNEL, DEFAULT_CHANNEL),
                owner=token,
                take_over=msg.get("claim", False),
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
            if msg.get("dump_decrypted_header") and not msg.get("dump_raw"):
                raise ValueError("decrypted headers require dump_raw")
            raw_frames: list[tuple] = []
            raw_bytes = 0
            raw_dropped = 0

            def on_mix_frame(frame) -> None:
                if not active:
                    return
                try:
                    connection.send_message({
                        "id": subscription_id,
                        "type": "event",
                        "event": {
                            "type": "videolink_doorbell/native_talk_mix",
                            "pcm": base64.b64encode(frame.incoming_pcm).decode(),
                        },
                    })
                except Exception:
                    return

            await client.native_talk_set_mix_callback(on_mix_frame, owner=token)
            if msg.get("dump_raw"):
                def on_raw_frame(header, extension: bytes, payload: bytes) -> None:
                    nonlocal raw_bytes, raw_dropped
                    if not active:
                        return
                    decrypted_prefix = None
                    if msg.get("dump_decrypted_header") and header.message_id == 202 and payload:
                        try:
                            decrypted_prefix = client.native_talk_decrypt_wire_prefix(
                                payload, owner=token
                            )
                        except Exception:
                            pass
                    size = len(extension) + len(payload) + 64 + len(decrypted_prefix or b"")
                    if raw_bytes + size > RAW_CAPTURE_MAX_BYTES:
                        raw_dropped += 1
                        return
                    raw_frames.append((time.time_ns(), header, extension, payload, decrypted_prefix))
                    raw_bytes += size

                client.native_talk_set_raw_callback(on_raw_frame, owner=token)

            def unsubscribe() -> None:
                nonlocal active
                active = False
                client._native_raw_captures.pop(token, None)
                hass.async_create_task(client.native_talk_stop(owner=token))

            connection.subscriptions[subscription_id] = unsubscribe
            result = {"ok": True}
            if msg.get("dump_raw"):
                # Capture stays on the HA side during timing-critical trials.
                # Fetch and encode only after the last trial, before stop.
                client._native_raw_captures[token] = (
                    raw_frames, lambda: raw_dropped
                )
        elif action == "diagnostics":
            result = client.native_talk_mix_diagnostics(owner=token)
        elif action == "raw_fetch":
            client.native_talk_mix_diagnostics(owner=token)
            capture = client._native_raw_captures.get(token)
            if capture is None:
                raise ValueError("raw capture was not enabled for this session")
            frames, dropped = capture
            cursor = msg["cursor"]
            chunk = frames[cursor : cursor + 32]
            result = {
                "frames": [
                    {
                        "received_at_utc": datetime.fromtimestamp(
                            received_ns / 1_000_000_000, timezone.utc
                        ).isoformat(),
                        "message_id": header.message_id,
                        "response_code": header.response_code,
                        "message_class": header.message_class,
                        "channel_id": header.channel_id,
                        "stream_type": header.stream_type,
                        "message_number": header.message_number,
                        "body_length": header.body_length,
                        "payload_offset": header.payload_offset,
                        "extension_b64": base64.b64encode(extension).decode(),
                        "payload_b64": base64.b64encode(payload).decode(),
                        **({"decrypted_payload_prefix_b64": base64.b64encode(decrypted_prefix).decode()}
                           if decrypted_prefix is not None else {}),
                    }
                    for received_ns, header, extension, payload, decrypted_prefix in chunk
                ],
                "next_cursor": cursor + len(chunk),
                "done": cursor + len(chunk) >= len(frames),
                "dropped": dropped(),
            }
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
