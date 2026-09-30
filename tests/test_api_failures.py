"""Exercise malformed CGI payloads, HTTP authentication and timeout recovery."""

import json
import sys
import traceback
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from aiohttp import ClientResponseError, RequestInfo
from multidict import CIMultiDict, CIMultiDictProxy
from yarl import URL

sys.path.insert(0, str(Path(__file__).parents[1] / "custom_components/videolink_doorbell"))
from videolink_client import api

LOGIN = [{"code": 0, "value": {"Token": {"name": "new-secret-token", "leaseTime": 3600}}}]
DEVICE = [{"code": 0, "value": {"DevInfo": {"name": "Front", "model": "Model", "serial": "serial", "firmVer": "FW"}}}]


class Response:
    def __init__(self, payload=None, *, status=200, failure=None, raw_json=None):
        self.payload = payload
        self.status = status
        self.failure = failure
        self.raw_json = raw_json

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_args):
        pass

    def raise_for_status(self):
        if self.status >= 400:
            url = URL("https://camera.local/cgi-bin/api.cgi?token=old-secret-token")
            request = RequestInfo(url, "POST", CIMultiDictProxy(CIMultiDict()), url)
            raise ClientResponseError(request, (), status=self.status)

    async def json(self, **_kwargs):
        if self.failure:
            raise self.failure
        return json.loads(self.raw_json) if self.raw_json is not None else self.payload

    async def read(self):
        if self.failure:
            raise self.failure
        return self.payload


class Session:
    def __init__(self, *responses):
        self.responses = iter(responses)
        self.calls = []

    def request(self, url, **kwargs):
        self.calls.append(kwargs)
        return next(self.responses)

    post = get = request


def client_for(*responses):
    client = api.VideolinkClient(Session(*responses), "camera.local", "user", "password")
    client._token = "old-secret-token"
    client._token_expires = datetime.now(timezone.utc) + timedelta(hours=1)
    return client


@pytest.mark.asyncio
@pytest.mark.parametrize("payload", [
    None, {}, [], [None], ["invalid"], [{"code": 0, "value": []}],
    [{"code": 0, "value": {"DevInfo": []}}],
])
async def test_malformed_device_responses_raise_api_error_and_recover(payload):
    client = client_for(Response(payload), Response(DEVICE))
    with pytest.raises(api.VideolinkError):
        await client.device_info()
    assert (await client.device_info()).serial == "serial"


@pytest.mark.asyncio
@pytest.mark.parametrize("field", ["name", "model", "serial", "firmVer"])
async def test_malformed_device_field_is_rejected_before_entity_setup(field):
    client = client_for(Response([{"code": 0, "value": {"DevInfo": {field: ["invalid"]}}}]))
    with pytest.raises(api.VideolinkError, match="device information"):
        await client.device_info()


@pytest.mark.asyncio
async def test_optional_null_device_fields_use_safe_defaults():
    client = client_for(Response([{"code": 0, "value": {"DevInfo": {
        "name": None, "model": None, "serial": None, "firmVer": None,
    }}}]))
    assert await client.device_info() == api.DeviceInfo("Videolink Camera", "Unknown", "", "")


@pytest.mark.asyncio
async def test_invalid_json_is_sanitized_and_next_request_recovers():
    client = client_for(Response(raw_json="{old-secret-token"), Response(DEVICE))
    with pytest.raises(api.VideolinkConnectionError) as caught:
        await client.device_info()
    assert "old-secret-token" not in "".join(traceback.format_exception(caught.value))
    assert (await client.device_info()).serial == "serial"


@pytest.mark.asyncio
@pytest.mark.parametrize("token_data", [None, [], {}, {"name": []}, {"name": True},
                                        {"name": "token", "leaseTime": None},
                                        {"name": "token", "leaseTime": float("inf")},
                                        {"name": "token", "leaseTime": 10**30}])
async def test_malformed_login_does_not_poison_future_authentication(token_data):
    client = client_for(Response([{"code": 0, "value": {"Token": token_data}}]), Response(LOGIN))
    client._token = None
    with pytest.raises(api.VideolinkError):
        await client.ensure_login()
    assert await client.ensure_login() == "new-secret-token"


@pytest.mark.asyncio
@pytest.mark.parametrize("status", [401, 403])
async def test_login_http_authentication_failure_is_sanitized(status):
    client = client_for(Response(status=status))
    client._token = None
    with pytest.raises(api.VideolinkAuthError) as caught:
        await client.ensure_login()
    assert "secret-token" not in "".join(traceback.format_exception(caught.value))
    assert len(client._session.calls) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("status", [401, 403])
@pytest.mark.parametrize("snapshot", [False, True])
@pytest.mark.parametrize("persistent", [False, True])
async def test_http_authentication_refreshes_once_then_recovers_or_raises(status, snapshot, persistent):
    retry = Response(status=status) if persistent else Response(b"\xff\xd8jpeg" if snapshot else DEVICE)
    client = client_for(Response(status=status), Response(LOGIN), retry)
    if persistent:
        with pytest.raises(api.VideolinkAuthError) as caught:
            await (client.snapshot(0) if snapshot else client.device_info())
        assert "secret-token" not in "".join(traceback.format_exception(caught.value))
        assert client._token is None
    elif snapshot:
        assert await client.snapshot(0) == b"\xff\xd8jpeg"
    else:
        assert (await client.device_info()).serial == "serial"
    assert len(client._session.calls) == 3
    assert client._session.calls[1]["json"][0]["cmd"] == "Login"


@pytest.mark.asyncio
@pytest.mark.parametrize("snapshot", [False, True])
async def test_timeout_is_connection_failure_and_next_request_recovers(snapshot):
    success = b"\xff\xd8jpeg" if snapshot else DEVICE
    client = client_for(Response(failure=TimeoutError("old-secret-token")), Response(success))
    with pytest.raises(api.VideolinkConnectionError) as caught:
        await (client.snapshot(0) if snapshot else client.device_info())
    assert "old-secret-token" not in "".join(traceback.format_exception(caught.value))
    assert client._session.calls[0]["timeout"].total == 15
    if snapshot:
        assert await client.snapshot(0) == success
    else:
        assert (await client.device_info()).serial == "serial"
