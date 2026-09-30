"""Camera reboot invalidates a token without replacing its registered entity."""

import asyncio
from datetime import timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest
from aiohttp import web
from aiohttp.test_utils import TestServer

pytest.importorskip("pytest_homeassistant_custom_component")

from homeassistant.components.camera.const import DATA_COMPONENT
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.util import dt as dt_util
from pytest_homeassistant_custom_component.common import async_fire_time_changed

from custom_components.videolink_doorbell import backend
from custom_components.videolink_doorbell.api import VideolinkClient
from custom_components.videolink_doorbell.const import DOMAIN


async def test_camera_restart_recovers_snapshot_and_existing_hls_source(
    hass, entry_runtime, monkeypatch, socket_enabled,
):
    entry, _, _ = entry_runtime
    camera_state = {"online": True, "generation": 1, "logins": 0}

    async def cgi(request):
        if not camera_state["online"]:
            return web.Response(status=503)
        commands = await request.json() if request.method == "POST" else []
        cmd = commands[0]["cmd"] if commands else request.query["cmd"]
        token = f"session-{camera_state['generation']}"
        if cmd == "Login":
            camera_state["logins"] += 1
            value = {"Token": {"name": token, "leaseTime": 3600}}
        elif request.query.get("token") != token:
            return web.Response(status=403)
        elif cmd == "Snap":
            return web.Response(body=b"\xff\xd8jpeg", content_type="image/jpeg")
        elif cmd == "GetDevInfo":
            value = {"DevInfo": {"name": "Front", "model": "Model", "serial": "serial", "firmVer": "FW"}}
        else:
            assert cmd == "GetNetPort"
            value = {"NetPort": {"rtmpPort": 1935}}
        return web.json_response([{"code": 0, "value": value}])

    app = web.Application()
    app.router.add_route("*", "/cgi-bin/api.cgi", cgi)
    async with TestServer(app) as server:
        class CameraClient(VideolinkClient):
            @property
            def base_url(self):
                return str(server.make_url("/")).rstrip("/")

        client = CameraClient(async_get_clientsession(hass), "camera.local", "admin", "test")
        monkeypatch.setattr(backend, "VideolinkClient", Mock(return_value=client))
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()
        camera = next(iter(hass.data[DATA_COMPONENT].entities))
        sources = entry.runtime_data.streams
        sources._registration.async_ensure = AsyncMock()
        sources._registration.async_refresh_provider = AsyncMock(return_value=True)
        old_url = await camera.stream_source()
        hls = SimpleNamespace(source=old_url, available=True, stop=AsyncMock())
        hls.update_source = Mock(side_effect=lambda url: setattr(hls, "source", url))
        camera.stream = hls
        try:
            camera_state["online"] = False
            with pytest.raises(HomeAssistantError):
                await camera.async_camera_image()
            assert hass.states.get(camera.entity_id).state == "unavailable"
            # Reboot: the server revokes the previously issued token.
            camera_state.update(online=True, generation=2)
            assert await camera.async_camera_image() == b"\xff\xd8jpeg"
            assert camera.available
            assert camera_state["logins"] == 2
            assert not hass.config_entries.flow.async_progress_by_handler(DOMAIN)
            async_fire_time_changed(hass, dt_util.utcnow() + timedelta(seconds=31))
            await hass.async_block_till_done()
            assert sources._refresh_task is not None
            await asyncio.wait_for(sources._refresh_task, 2)
            assert hls.source != old_url
            assert "token=session-2" in hls.source
            hls.update_source.assert_called_once_with(hls.source)
            assert hass.data[DATA_COMPONENT].get_entity(camera.entity_id) is camera
            assert entry.unique_id == "serial_channel_0"
        finally:
            assert await hass.config_entries.async_unload(entry.entry_id)
        assert sources._cancel_interval is None
        hls.update_source.reset_mock()
        async_fire_time_changed(hass, dt_util.utcnow() + timedelta(seconds=62))
        await hass.async_block_till_done()
        hls.update_source.assert_not_called()
