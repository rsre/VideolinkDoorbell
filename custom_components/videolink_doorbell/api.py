"""Small async client for the API used by the Videolink web console."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import math
import secrets
import ssl
import struct
import time
from typing import Any
from urllib.parse import quote, urlencode, urlsplit

from aiohttp import ClientError, ClientSession, ClientTimeout

try:
    from .native_talk import NativeTalkChannel
except ImportError:  # Keep the standalone API test loader working.
    import importlib.util
    import sys

    _native_talk_path = __file__.replace("api.py", "native_talk.py")
    _native_talk_spec = importlib.util.spec_from_file_location(
        "videolink_native_talk_under_test", _native_talk_path
    )
    if _native_talk_spec is None or _native_talk_spec.loader is None:
        raise
    _native_talk_module = importlib.util.module_from_spec(_native_talk_spec)
    sys.modules[_native_talk_spec.name] = _native_talk_module
    _native_talk_spec.loader.exec_module(_native_talk_module)
    NativeTalkChannel = _native_talk_module.NativeTalkChannel


class VideolinkError(Exception):
    """Base Videolink client error."""


class VideolinkAuthError(VideolinkError):
    """Raised when camera authentication fails."""


class VideolinkConnectionError(VideolinkError):
    """Raised when the camera cannot be reached."""


@dataclass(slots=True)
class DeviceInfo:
    """Identity returned by GetDevInfo."""

    name: str
    model: str
    serial: str
    firmware: str


class VideolinkClient:
    """Client matching the camera web console's token-based CGI API."""

    _TIMEOUT = ClientTimeout(total=15)

    def __init__(
        self,
        session: ClientSession,
        host: str,
        username: str,
        password: str,
        *,
        port: int = 443,
        verify_ssl: bool = False,
    ) -> None:
        self._session = session
        self.host = self._normalize_host(host)
        self.username = username
        self.password = password
        self.port = port
        self.verify_ssl = verify_ssl
        self._token: str | None = None
        self._token_expires = datetime.min.replace(tzinfo=timezone.utc)
        self._rtmp_port: int | None = None
        self._login_lock = asyncio.Lock()
        self._native_talk: NativeTalkChannel | None = None
        self._native_talk_lock = asyncio.Lock()
        self._native_talk_owner: str | None = None

    @staticmethod
    def _normalize_host(host: str) -> str:
        """Validate and normalize a hostname or IP address without a path."""
        raw_host = host.strip()
        if not raw_host:
            raise VideolinkError("Host is required")
        parsed = urlsplit(raw_host if "://" in raw_host else f"//{raw_host}")
        if (
            parsed.scheme not in ("", "http", "https")
            or parsed.username is not None
            or parsed.password is not None
            or parsed.path not in ("", "/")
            or parsed.query
            or parsed.fragment
        ):
            raise VideolinkError("Host must be a hostname or IP address without a path")
        try:
            parsed_port = parsed.port
        except ValueError as err:
            raise VideolinkError("Host contains an invalid port") from err
        if parsed_port is not None:
            raise VideolinkError("Enter the HTTPS port in the separate port field")
        if parsed.hostname is None:
            raise VideolinkError("Host is invalid")
        return parsed.hostname

    @property
    def url_host(self) -> str:
        """Return the host formatted for use in a URL."""
        return f"[{self.host}]" if ":" in self.host else self.host

    @property
    def base_url(self) -> str:
        """Return the camera HTTPS origin."""
        suffix = "" if self.port == 443 else f":{self.port}"
        return f"https://{self.url_host}{suffix}"

    @property
    def ssl_context(self) -> ssl.SSLContext | bool:
        """Return aiohttp TLS verification configuration."""
        return True if self.verify_ssl else False

    async def _request(
        self, commands: list[dict[str, Any]], token: str | None = None
    ) -> list[dict[str, Any]]:
        cmd = commands[0]["cmd"]
        params = {"cmd": cmd}
        if token:
            params["token"] = token
        try:
            async with self._session.post(
                f"{self.base_url}/cgi-bin/api.cgi",
                params=params,
                json=commands,
                ssl=self.ssl_context,
                timeout=self._TIMEOUT,
            ) as response:
                response.raise_for_status()
                payload = await response.json(content_type=None)
        except (ClientError, TimeoutError, ValueError) as err:
            raise VideolinkConnectionError(str(err)) from err
        if (
            not isinstance(payload, list)
            or not payload
            or not all(isinstance(item, dict) for item in payload)
        ):
            raise VideolinkError("Camera returned an invalid API response")
        return payload

    async def login(self) -> None:
        """Authenticate using the same Login command as the web console."""
        payload = await self._request(
            [
                {
                    "cmd": "Login",
                    "action": 0,
                    "param": {
                        "User": {"userName": self.username, "password": self.password}
                    },
                }
            ]
        )
        result = payload[0]
        if result.get("code") != 0:
            error = result.get("error", {})
            detail = (
                error.get("detail", "Login failed")
                if isinstance(error, dict)
                else "Login failed"
            )
            raise VideolinkAuthError(detail)
        value = result.get("value", {})
        if not isinstance(value, dict):
            raise VideolinkAuthError("Camera returned invalid login data")
        token_data = value.get("Token", {})
        if not isinstance(token_data, dict):
            raise VideolinkAuthError("Camera returned invalid login token data")
        token = token_data.get("name")
        if not token:
            raise VideolinkAuthError("Camera did not return a login token")
        self._token = token
        try:
            lease = max(60, int(token_data.get("leaseTime", 3600)))
        except (TypeError, ValueError) as err:
            self._token = None
            raise VideolinkError("Camera returned an invalid token lifetime") from err
        self._token_expires = datetime.now(timezone.utc) + timedelta(seconds=lease - 30)

    async def ensure_login(self) -> str:
        """Return a valid API token, renewing it shortly before expiry."""
        if self._token is None or datetime.now(timezone.utc) >= self._token_expires:
            async with self._login_lock:
                if (
                    self._token is None
                    or datetime.now(timezone.utc) >= self._token_expires
                ):
                    await self.login()
        assert self._token is not None
        return self._token

    async def command(
        self, cmd: str, param: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        """Run one authenticated CGI command, retrying once after token expiry."""
        for attempt in range(2):
            token = await self.ensure_login()
            payload = await self._request(
                [{"cmd": cmd, "action": 0, "param": param or {}}], token
            )
            result = payload[0]
            if result.get("code") == 0:
                value = result.get("value", {})
                if not isinstance(value, dict):
                    raise VideolinkError(f"{cmd} returned invalid data")
                return value
            error = result.get("error", {})
            if not isinstance(error, dict):
                raise VideolinkError(f"{cmd} failed")
            if error.get("rspCode") == -6:
                self._token = None
                if attempt == 0:
                    continue
                raise VideolinkAuthError("Session expired")
            raise VideolinkError(error.get("detail", f"{cmd} failed"))
        raise VideolinkAuthError("Session expired")

    async def device_info(self) -> DeviceInfo:
        """Read camera identity."""
        value = await self.command("GetDevInfo")
        info = value.get("DevInfo", value)
        if not isinstance(info, dict):
            raise VideolinkError("Camera returned invalid device information")
        return DeviceInfo(
            name=info.get("name") or info.get("model") or "Videolink Camera",
            model=info.get("model", "Unknown"),
            serial=info.get("serial", ""),
            firmware=info.get("firmVer", ""),
        )

    async def snapshot(self, channel: int) -> bytes:
        """Fetch a JPEG through the console's authenticated Snap endpoint."""
        for attempt in range(2):
            token = await self.ensure_login()
            params = {
                "cmd": "Snap",
                "channel": str(channel),
                "rs": secrets.token_hex(8),
                "token": token,
            }
            try:
                async with self._session.get(
                    f"{self.base_url}/cgi-bin/api.cgi",
                    params=params,
                    ssl=self.ssl_context,
                    timeout=self._TIMEOUT,
                ) as response:
                    response.raise_for_status()
                    data = await response.read()
            except (ClientError, TimeoutError) as err:
                raise VideolinkConnectionError(str(err)) from err
            if data.startswith(b"\xff\xd8"):
                return data
            self._token = None
            if attempt == 1:
                raise VideolinkAuthError("Camera did not return a JPEG snapshot")
        raise VideolinkAuthError("Camera did not return a JPEG snapshot")

    async def flv_url(self, channel: int, stream: str) -> str:
        """Build the same authenticated FLV preview URL as the web console."""
        if self._rtmp_port is None:
            value = await self.command("GetNetPort")
            net_port = value.get("NetPort", value)
            try:
                self._rtmp_port = int(net_port.get("rtmpPort", 1935))
            except (AttributeError, TypeError, ValueError) as err:
                raise VideolinkError("Camera returned an invalid RTMP port") from err
        token = await self.ensure_login()
        query = urlencode(
            {
                "token": token,
                "port": self._rtmp_port,
                "app": "bcs",
                "stream": f"channel{channel}_{stream}.bcs",
            }
        )
        return f"{self.base_url}/flv?{query}"

    def rtsp_backchannel_url(self, channel: int, stream: str, rtsp_port: int) -> str:
        """Build the complete secondary RTSP producer with ONVIF backchannel."""
        return self._rtsp_url(channel, stream, rtsp_port, backchannel=True)

    def rtsp_url(self, channel: int, stream: str, rtsp_port: int) -> str:
        """Build the normal RTSP preview URL without talkback parameters."""
        return self._rtsp_url(channel, stream, rtsp_port, backchannel=False)

    def _rtsp_url(
        self, channel: int, stream: str, rtsp_port: int, *, backchannel: bool
    ) -> str:
        """Build a camera RTSP URL, optionally enabling the ONVIF backchannel."""
        username = quote(self.username, safe="")
        password = quote(self.password, safe="")
        url = (
            f"rtsp://{username}:{password}@{self.url_host}:{rtsp_port}/"
            f"{'Preview' if backchannel else 'h264Preview'}_"
            f"{channel + 1:02d}_{stream}"
        )
        return f"{url}#backchannel=1#transport=udp" if backchannel else url

    async def native_talk_start(
        self,
        channel: int,
        *,
        owner: str | None = None,
        require_owner: bool = False,
        take_over: bool = False,
        mix_frame_callback=None,
    ):
        """Open and configure the experimental native Baichuan talk path."""
        async with self._native_talk_lock:
            if require_owner and (owner is None or self._native_talk_owner != owner):
                raise VideolinkConnectionError("Native talk session is no longer active")
            if self._native_talk_owner is not None and self._native_talk_owner != owner:
                if not take_over or owner is None:
                    raise VideolinkError("Native talk is already in use by another card")
                previous = self._native_talk
                try:
                    if previous is not None:
                        await previous.stop()
                except Exception:
                    # A broken old channel must not prevent a new owner from trying.
                    pass
                finally:
                    self._native_talk = None
                    self._native_talk_owner = None
            if self._native_talk is None:
                self._native_talk = NativeTalkChannel(
                    self.host,
                    self.username,
                    self.password,
                    channel=channel,
                    mix_frame_callback=mix_frame_callback,
                )
                try:
                    await self._native_talk.start()
                except Exception:
                    await self._native_talk.stop()
                    self._native_talk = None
                    raise
            elif self._native_talk.failed:
                await self._native_talk.restart()
            self._native_talk_owner = owner
            return self._native_talk.talk_config

    @property
    def native_talk_config(self):
        """Return the currently negotiated native talk configuration."""
        return self._native_talk.talk_config if self._native_talk is not None else None

    async def native_talk_audio(self, pcm16le: bytes, *, wait: bool = True, owner: str | None = None) -> None:
        """Encode and send exactly one negotiated PCM talk frame."""
        if self._native_talk is None:
            raise VideolinkConnectionError("Native talk session is not active")
        await self.native_talk_start(
            self._native_talk.channel, owner=owner, require_owner=owner is not None
        )
        channel = self._native_talk
        if channel is None:
            raise VideolinkConnectionError("Native talk session is not active")
        try:
            if wait:
                await channel.send_pcm(pcm16le)
            else:
                completion = await channel.enqueue_pcm(pcm16le)
                # The WebSocket command acknowledges enqueueing, not camera
                # playback. Consume a later worker exception so asyncio does
                # not report an unhandled Future exception.
                completion.add_done_callback(
                    lambda future: None if future.cancelled() else future.exception()
                )
        except RuntimeError as err:
            raise VideolinkConnectionError(str(err)) from err

    async def native_talk_tone(self, channel: int, *, seconds: float = 2.0, owner: str | None = None) -> None:
        """Generate and send a test tone without browser audio/WebSocket frames."""
        await self.native_talk_start(
            channel, owner=owner, require_owner=owner is not None
        )
        if self._native_talk is None or self._native_talk.talk_config is None:
            raise VideolinkConnectionError("Native talk session is not active")
        channel = self._native_talk
        config = channel.talk_config
        total_samples = int(seconds * config.sample_rate)
        frame_size = config.length_per_encoder
        samples = [
            int(10000 * math.sin(2 * math.pi * 440 * index / config.sample_rate))
            for index in range(total_samples)
        ]
        if len(samples) % frame_size:
            samples.extend([0] * (frame_size - len(samples) % frame_size))
        deadline = time.perf_counter()
        for offset in range(0, len(samples), frame_size):
            pcm = struct.pack(
                f"<{frame_size}h", *samples[offset : offset + frame_size]
            )
            await asyncio.sleep(max(0.0, deadline - time.perf_counter()))
            await channel.send_pcm(pcm)
            deadline += frame_size / config.sample_rate
        # Native playback is buffered in the camera. Keep the session alive
        # after the final frame so the caller cannot stop it before the queued
        # audio has reached the speaker.
        await asyncio.sleep(2.0)
        # The final TCP write can complete before the doorbell has played its
        # queued audio. Match the CLI probe's drain before the card stops talk.
        await asyncio.sleep(1.0)

    async def native_talk_set_mix_callback(self, callback, *, owner: str | None = None) -> None:
        """Set the consumer for cleaned native mix audio."""
        if self._native_talk_owner != owner:
            raise VideolinkError("Native talk belongs to another card")
        if self._native_talk is None or not self._native_talk.active:
            raise VideolinkConnectionError("Native talk session is not active")
        self._native_talk.set_mix_callback(callback)

    async def native_talk_stop(self, *, owner: str | None = None) -> None:
        """Stop and close the experimental native talk path."""
        async with self._native_talk_lock:
            if owner is not None and self._native_talk_owner != owner:
                return
            if self._native_talk is not None:
                session = self._native_talk
                try:
                    await session.stop()
                finally:
                    self._native_talk = None
                    self._native_talk_owner = None
