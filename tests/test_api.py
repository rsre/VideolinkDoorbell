"""Unit tests for the standalone Videolink API client."""

from __future__ import annotations

import asyncio
import importlib.util
import sys
import traceback
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from aiohttp import ClientResponseError, RequestInfo
from multidict import CIMultiDict, CIMultiDictProxy
from yarl import URL

API_PATH = Path(__file__).parents[1] / "custom_components/videolink_doorbell/api.py"
SPEC = importlib.util.spec_from_file_location("videolink_api_under_test", API_PATH)
assert SPEC is not None and SPEC.loader is not None
api = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = api
SPEC.loader.exec_module(api)


@pytest.mark.asyncio
@pytest.mark.parametrize("snapshot", [False, True])
async def test_http_errors_do_not_expose_tokens(snapshot: bool) -> None:
    url = URL("https://camera.local/cgi-bin/api.cgi?token=secret-sentinel")
    request = RequestInfo(url, "GET", CIMultiDictProxy(CIMultiDict()), url)

    class Response:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            pass

        def raise_for_status(self):
            raise ClientResponseError(request, (), status=503)

    class Session:
        get = post = lambda *args, **kwargs: Response()

    client = api.VideolinkClient(Session(), "camera.local", "user", "password")
    client._token = "secret-sentinel"
    client._token_expires = datetime.now(timezone.utc) + timedelta(hours=1)
    with pytest.raises(api.VideolinkConnectionError) as caught:
        if snapshot:
            await client.snapshot(0)
        else:
            await client._request([{"cmd": "GetDevInfo"}], client._token)
    assert "secret-sentinel" not in "".join(traceback.format_exception(caught.value))


@pytest.mark.asyncio
async def test_invalid_snapshot_is_not_a_credentials_error():
    class Response:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            pass

        def raise_for_status(self):
            pass

        async def read(self):
            return b"invalid jpeg"

    class Session:
        get = lambda *args, **kwargs: Response()

    client = api.VideolinkClient(Session(), "camera.local", "user", "password")
    from unittest.mock import AsyncMock
    client.ensure_login = AsyncMock(return_value="token")
    with pytest.raises(api.VideolinkError) as error:
        await client.snapshot(0)
    assert not isinstance(error.value, api.VideolinkAuthError)


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
        "/Preview_01_main#backchannel=1#transport=udp"
    )


def test_rtsp_url_omits_backchannel_parameters() -> None:
    client = api.VideolinkClient(object(), "camera.local", "user", "password")
    assert client.rtsp_url(0, "main", 554).endswith(
        "/h264Preview_01_main"
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


@pytest.mark.asyncio
async def test_native_talk_owner_prevents_other_cards_from_stopping_session(monkeypatch) -> None:
    class Channel:
        def __init__(self, *args, **kwargs):
            self.talk_config = object()
            self.failed = False
            self.stopped = False
            self.channel = kwargs["channel"]

        async def start(self):
            pass

        async def stop(self):
            self.stopped = True

        def mix_diagnostics(self):
            return {"raw_messages": 3}

        def set_raw_callback(self, callback):
            self.raw_callback = callback

        def decrypt_wire_prefix(self, payload):
            return payload[:64]

    monkeypatch.setattr(api, "NativeTalkChannel", Channel)
    client = api.VideolinkClient(object(), "camera.local", "user", "password")
    await client.native_talk_start(0, owner="card-one")
    channel = client._native_talk
    assert client.native_talk_mix_diagnostics(owner="card-one") == {"raw_messages": 3}
    callback = lambda *_args: None
    client.native_talk_set_raw_callback(callback, owner="card-one")
    assert channel.raw_callback is callback
    assert client.native_talk_decrypt_wire_prefix(b"prefix", owner="card-one") == b"prefix"
    with pytest.raises(api.VideolinkConnectionError, match="no longer active"):
        client.native_talk_mix_diagnostics(owner="card-two")
    with pytest.raises(api.VideolinkConnectionError, match="no longer active"):
        client.native_talk_set_raw_callback(callback, owner="card-two")
    with pytest.raises(api.VideolinkConnectionError, match="no longer active"):
        client.native_talk_decrypt_wire_prefix(b"prefix", owner="card-two")
    with pytest.raises(api.VideolinkError, match="another card"):
        await client.native_talk_start(0, owner="card-two")
    await client.native_talk_stop(owner="card-two")
    assert channel.stopped is False
    await client.native_talk_stop(owner="card-one")
    assert channel.stopped is True
    with pytest.raises(api.VideolinkConnectionError, match="no longer active"):
        await client.native_talk_start(0, owner="card-one", require_owner=True)
    assert client._native_talk is None


@pytest.mark.asyncio
async def test_new_card_claim_replaces_previous_native_talk_owner(monkeypatch) -> None:
    class Channel:
        def __init__(self, *args, **kwargs):
            self.talk_config = object()
            self.failed = False
            self.stopped = False
            self.channel = kwargs["channel"]

        async def start(self):
            pass

        async def stop(self):
            self.stopped = True

    monkeypatch.setattr(api, "NativeTalkChannel", Channel)
    client = api.VideolinkClient(object(), "camera.local", "user", "password")
    await client.native_talk_start(0, owner="old-card")
    old_channel = client._native_talk

    await client.native_talk_start(0, owner="new-card", take_over=True)
    new_channel = client._native_talk
    assert old_channel.stopped is True
    assert new_channel is not old_channel
    assert client._native_talk_owner == "new-card"

    with pytest.raises(api.VideolinkConnectionError, match="no longer active"):
        await client.native_talk_audio(b"frame", owner="old-card")
    await client.native_talk_stop(owner="old-card")
    assert new_channel.stopped is False
    await client.native_talk_stop(owner="new-card")
    assert new_channel.stopped is True


@pytest.mark.parametrize("host", ["http://[", "[", "https://[invalid]", "camera.local:bad", "camera.local/path"])
def test_invalid_hosts_raise_validation_errors(host):
    with pytest.raises(api.VideolinkInvalidHostError):
        api.VideolinkClient._normalize_host(host)
