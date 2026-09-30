"""Chromium card → official HA connection → actual HA WebSocket handler.

Only device I/O is simulated. WebRTC uses real browser peers, microphone capture
uses Chromium's fake device, and native commands exercise real client ownership.
Run explicitly with VIDEOLINK_BROWSER_TESTS=1 after building tests/browser.
"""

import asyncio
import os
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest
from aiohttp import web

pytest.importorskip("pytest_homeassistant_custom_component")

from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.setup import async_setup_component

from custom_components.videolink_doorbell import backend
from custom_components.videolink_doorbell.api import DeviceInfo, VideolinkClient
from custom_components.videolink_doorbell.videolink_client import api
from custom_components.videolink_doorbell.videolink_client.native_talk import TalkConfig

pytestmark = pytest.mark.skipif(
    os.environ.get("VIDEOLINK_BROWSER_TESTS") != "1",
    reason="Browser suite requires explicit opt-in and installed Chromium",
)
ROOT = Path(__file__).resolve().parents[2]


class DeviceTalkChannel:
    """Native hardware boundary; ownership remains in the real API client."""

    def __init__(self, host, username, password, *, channel, mix_frame_callback):
        self.channel = channel
        self.talk_config = TalkConfig(audio_stream_mode="mixAudioStream")
        self.callback = mix_frame_callback
        self.active = False
        self.failed = False
        self.frames = []

    async def start(self):
        self.active = True

    async def stop(self):
        self.active = False

    def set_mix_callback(self, callback):
        self.callback = callback

    async def enqueue_pcm(self, pcm):
        self.frames.append(pcm)
        completion = asyncio.get_running_loop().create_future()
        completion.set_result(None)
        return completion

    def mix_diagnostics(self):
        return {"frames_received": 0}

    def ring_audio(self):
        self.callback(SimpleNamespace(incoming_pcm=bytes(2048)))


async def eventually(predicate):
    """Yield to the real WebSocket server until its observable state changes."""
    async with asyncio.timeout(10):
        while not predicate():
            await asyncio.sleep(0.01)


@pytest.fixture
async def browser_runtime(hass, entry_runtime, monkeypatch, hass_client):
    from playwright.async_api import async_playwright

    entry, _, _ = entry_runtime
    client = VideolinkClient(async_get_clientsession(hass), "camera.local", "admin", "test")
    client.device_info = AsyncMock(return_value=DeviceInfo("Front", "Model", "serial", "FW"))
    monkeypatch.setattr(backend, "VideolinkClient", Mock(return_value=client))
    channels = []

    def channel_factory(*args, **kwargs):
        channel = DeviceTalkChannel(*args, **kwargs)
        channels.append(channel)
        return channel

    monkeypatch.setattr(api, "NativeTalkChannel", channel_factory)
    assert await async_setup_component(hass, "websocket_api", {})
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    camera = next(item.entity_id for item in er.async_entries_for_config_entry(er.async_get(hass), entry.entry_id)
                  if item.domain == "camera")
    harness = await asyncio.to_thread((ROOT / "tests/browser/dist/harness.js").read_bytes)
    card = await asyncio.to_thread((ROOT / "frontend/videolink-doorbell.js").read_bytes)

    async def index(request):
        return web.Response(text='<script type="module" src="/card.js"></script>'
                                 '<script type="module" src="/harness.js"></script>', content_type="text/html")

    async def card_script(request):
        return web.Response(body=card, content_type="application/javascript")

    async def harness_script(request):
        return web.Response(body=harness, content_type="application/javascript")

    hass.http.app.router.add_get("/", index)
    hass.http.app.router.add_get("/card.js", card_script)
    hass.http.app.router.add_get("/harness.js", harness_script)
    http = await hass_client()
    async with async_playwright() as playwright:
        browser = await playwright.chromium.launch(args=[
            "--use-fake-device-for-media-stream", "--use-fake-ui-for-media-stream",
            "--autoplay-policy=no-user-gesture-required",
            "--allow-loopback-in-peer-connection",
        ])
        context = await browser.new_context(permissions=["microphone"])
        try:
            yield SimpleNamespace(
                context=context, origin=str(http.make_url("/")).rstrip("/"),
                camera=camera, channels=channels, client=client,
            )
        finally:
            await context.close()
            await browser.close()
            await hass.config_entries.async_unload(entry.entry_id)


async def mount(runtime, token, *, native_ready=True):
    from playwright.async_api import TimeoutError as PlaywrightTimeout

    page = await runtime.context.new_page()
    errors = []
    page.on("pageerror", lambda error: errors.append(str(error)))
    page.on("console", lambda message: errors.append(message.text) if message.type == "error" else None)
    await page.goto(runtime.origin)
    await page.wait_for_function("window.mountCard !== undefined")
    await page.evaluate("([token, entity]) => window.mountCard(token, entity)", [token, runtime.camera])
    try:
        condition = "browserTest.card._streamReady"
        if native_ready:
            condition += " && browserTest.card._nativeSessionReady"
        await page.wait_for_function(condition, timeout=10000)
    except PlaywrightTimeout:
        state = await page.evaluate("({diagnostics: browserTest.card._diagnostics, status: browserTest.card._status?.textContent, peer: browserTest.card._peer?.connectionState, peers: [...browserTest.peers.values()].map(p => p.connectionState), events: browserTest.events})")
        pytest.fail(f"Card failed to connect: {state}; browser errors: {errors}")
    return page


async def hold_talk(page, channel):
    await page.locator("videolink-doorbell button.talk").focus()
    before = len(channel.frames)
    await page.keyboard.down("Space")
    await eventually(lambda: len(channel.frames) > before)
    assert len(channel.frames[-1]) == 2048


async def remove(page):
    await page.evaluate("browserTest.removeCard()")
    await page.wait_for_function("!browserTest.card._nativeToken && !browserTest.card._peer")
    assert await page.evaluate("browserTest.card._haConnection === undefined")
    # Only the harness's observers remain: card and temporary subscription
    # listeners must be removed through the official Connection API.
    assert await page.evaluate("browserTest.connection.eventListeners.get('disconnected').length") == 1
    assert await page.evaluate("browserTest.connection.eventListeners.get('ready').length") == 1


async def test_websocket_loss_releases_microphone_and_reconnects_native_session(
    browser_runtime, hass_access_token,
):
    runtime = browser_runtime
    page = await mount(runtime, hass_access_token)
    initial_token = await page.evaluate("browserTest.card._nativeToken")
    first = runtime.channels[-1]
    first.ring_audio()
    await page.wait_for_function("browserTest.card._nativeMixFramesReceived === 1")
    await hold_talk(page, first)
    await page.evaluate("browserTest.cutConnection()")
    await eventually(lambda: not first.active)
    await page.wait_for_function("browserTest.events.includes('disconnected')")
    # The WebRTC peer can remain connected even though HA has released its owner.
    # The card must stop capture and discard the session before reconnecting.
    await page.wait_for_function(
        "!browserTest.card._micStream && !browserTest.card._nativeToken && !browserTest.card._streamReady",
        timeout=5000,
    )
    assert await page.locator("videolink-doorbell button.talk").is_disabled()
    await page.keyboard.up("Space")
    await page.evaluate("browserTest.resumeConnection()")
    await page.wait_for_function("browserTest.events.includes('ready') && browserTest.card._streamReady && browserTest.card._nativeSessionReady")
    assert await page.evaluate("browserTest.card._nativeToken") != initial_token
    assert len(runtime.channels) == 2
    restarted = runtime.channels[-1]
    restarted.ring_audio()
    await page.wait_for_function("browserTest.card._nativeMixFramesReceived === 1")
    await hold_talk(page, restarted)
    await page.keyboard.up("Space")
    await remove(page)
    await eventually(lambda: not restarted.active)
    await page.evaluate("browserTest.connection.close()")


async def test_disconnect_before_native_subscription_ack_does_not_block_restart(
    browser_runtime, hass_access_token, monkeypatch,
):
    runtime = browser_runtime
    subscribed = asyncio.Event()
    release = asyncio.Event()
    set_callback = runtime.client.native_talk_set_mix_callback

    async def delayed_ack(callback, *, owner):
        await set_callback(callback, owner=owner)
        if not subscribed.is_set():
            subscribed.set()
            await release.wait()

    monkeypatch.setattr(runtime.client, "native_talk_set_mix_callback", delayed_ack)
    page = await mount(runtime, hass_access_token, native_ready=False)
    await asyncio.wait_for(subscribed.wait(), 5)
    first = runtime.channels[-1]
    await page.evaluate("browserTest.cutConnection()")
    await eventually(lambda: not first.active)
    try:
        await page.wait_for_function("!browserTest.card._nativeToken && !browserTest.card._peer", timeout=5000)
    finally:
        release.set()
    await page.evaluate("browserTest.resumeConnection()")
    await page.wait_for_function("browserTest.card._streamReady && browserTest.card._nativeSessionReady")
    assert len(runtime.channels) == 2
    restarted = runtime.channels[-1]
    await hold_talk(page, restarted)
    await page.keyboard.up("Space")
    await remove(page)
    await eventually(lambda: not restarted.active)
    await page.evaluate("browserTest.connection.close()")


async def test_second_card_takeover_rejects_old_audio_and_old_cleanup_preserves_owner(
    browser_runtime, hass_access_token,
):
    runtime = browser_runtime
    first_page = await mount(runtime, hass_access_token)
    old_token = await first_page.evaluate("browserTest.card._nativeToken")
    first = runtime.channels[-1]
    second_page = await mount(runtime, hass_access_token)
    second = runtime.channels[-1]
    assert len(runtime.channels) == 2
    assert not first.active and second.active
    result = await first_page.evaluate("""async ([entity, token]) => {
        try {
          await browserTest.callWS({type: 'videolink_doorbell/native_talk', action: 'audio',
            entity_id: entity, token, pcm: btoa(String.fromCharCode(...new Uint8Array(2048)))});
          return 'unexpected success';
        } catch (error) { return error.code; }
    }""", [runtime.camera, old_token])
    assert result == "native_talk_failed"
    assert not first.frames and second.active
    await remove(first_page)
    assert second.active
    second.ring_audio()
    await second_page.wait_for_function("browserTest.card._nativeMixFramesReceived === 1")
    await hold_talk(second_page, second)
    await second_page.keyboard.up("Space")
    await remove(second_page)
    await eventually(lambda: not second.active)
    assert len(runtime.channels) == 2
    await first_page.evaluate("browserTest.connection.close()")
    await second_page.evaluate("browserTest.connection.close()")
