"""Small async client for the API used by the Videolink web console."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import secrets
import ssl
from typing import Any
from urllib.parse import quote, urlencode, urlsplit

from aiohttp import ClientError, ClientSession, ClientTimeout


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
        username = quote(self.username, safe="")
        password = quote(self.password, safe="")
        return (
            f"rtsp://{username}:{password}@{self.url_host}:{rtsp_port}/"
            f"h264Preview_{channel + 1:02d}_{stream}#backchannel=1#transport=udp"
        )
