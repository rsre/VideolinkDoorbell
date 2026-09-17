"""Unit tests for the standalone Videolink API client."""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
import importlib.util
from pathlib import Path
import sys

import pytest


API_PATH = Path(__file__).parents[1] / "custom_components/videolink_doorbell/api.py"
SPEC = importlib.util.spec_from_file_location("videolink_api_under_test", API_PATH)
assert SPEC is not None and SPEC.loader is not None
api = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = api
SPEC.loader.exec_module(api)


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("camera.local", "camera.local"),
        (" https://camera.local/ ", "camera.local"),
        ("192.168.1.20", "192.168.1.20"),
        ("[2001:db8::1]", "2001:db8::1"),
    ],
)
def test_normalize_host(value: str, expected: str) -> None:
    assert api.VideolinkClient._normalize_host(value) == expected


@pytest.mark.parametrize(
    "value",
    [
        "",
        "camera.local/path",
        "camera.local:8443",
        "user@camera.local",
        "camera.local?q=1",
    ],
)
def test_normalize_host_rejects_invalid_values(value: str) -> None:
    with pytest.raises(api.VideolinkError):
        api.VideolinkClient._normalize_host(value)


def test_rtsp_backchannel_url_explicitly_requests_onvif_backchannel() -> None:
    client = api.VideolinkClient(object(), "camera.local", "user", "password")
    assert client.rtsp_backchannel_url(0, "main", 554).endswith(
        "/h264Preview_01_main#backchannel=1"
    )


@pytest.mark.asyncio
async def test_concurrent_token_requests_share_one_login() -> None:
    class Client(api.VideolinkClient):
        login_count = 0

        async def login(self) -> None:
            self.login_count += 1
            await asyncio.sleep(0)
            self._token = "token"
            self._token_expires = datetime.now(timezone.utc) + timedelta(hours=1)

    client = Client(object(), "camera.local", "user", "password")
    assert await asyncio.gather(client.ensure_login(), client.ensure_login()) == [
        "token",
        "token",
    ]
    assert client.login_count == 1


@pytest.mark.asyncio
async def test_snapshot_retries_a_rejected_token() -> None:
    class Response:
        def __init__(self, data: bytes) -> None:
            self.data = data

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args) -> None:
            return None

        def raise_for_status(self) -> None:
            return None

        async def read(self) -> bytes:
            return self.data

    class Session:
        def __init__(self) -> None:
            self.responses = [
                Response(b'{"error":"expired"}'),
                Response(b"\xff\xd8jpeg"),
            ]

        def get(self, *args, **kwargs):
            return self.responses.pop(0)

    class Client(api.VideolinkClient):
        login_count = 0

        async def ensure_login(self) -> str:
            self.login_count += 1
            return f"token-{self.login_count}"

    client = Client(Session(), "camera.local", "user", "password")
    assert await client.snapshot(0) == b"\xff\xd8jpeg"
    assert client.login_count == 2
