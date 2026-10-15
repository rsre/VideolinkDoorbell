"""Rendered geometry in the shipped Home Assistant Sections dashboard.

Run with VIDEOLINK_BROWSER_TESTS=1 and the frontend required by HA installed.
Only media/device I/O is replaced; HA renders and sizes the real dashboard.
"""

import asyncio
import json
import os
from pathlib import Path

import pytest
from aiohttp import web

pytest.importorskip("pytest_homeassistant_custom_component")

from homeassistant.components.lovelace.const import LOVELACE_DATA
from homeassistant.setup import async_setup_component

pytestmark = pytest.mark.skipif(
    os.environ.get("VIDEOLINK_BROWSER_TESTS") != "1",
    reason="Browser suite requires explicit opt-in and installed Chromium",
)
ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture
async def sections_dashboard(hass, hass_storage, hass_client, hass_access_token, tmp_path):
    from playwright.async_api import TimeoutError as PlaywrightTimeout
    from playwright.async_api import async_playwright

    hass_storage["onboarding"] = {
        "version": 4, "data": {"done": ["user", "core_config", "integration", "analytics"]},
    }
    hass_storage["lovelace_dashboards"] = {
        "version": 1, "data": {"items": [{
            "id": "layout-test", "url_path": "layout-test", "mode": "storage",
            "title": "Layout test", "show_in_sidebar": True, "require_admin": False,
        }]},
    }
    assert await async_setup_component(hass, "frontend", {
        "frontend": {},
    })
    await hass.data[LOVELACE_DATA].resources.async_create_item({
        "url": "/layout-card.js", "res_type": "module",
    })
    await hass.data[LOVELACE_DATA].dashboards["layout-test"].async_save({
        "title": "Layout regression",
        "views": [{
            "title": "Layout", "path": "layout", "type": "sections", "max_columns": 1,
            "sections": [
                {"type": "grid", "cards": [{
                    "type": "custom:videolink-doorbell", "entity": "camera.layout_test",
                    "title": "Front door", "video_fit": "full",
                }]},
                {"type": "grid", "cards": [{"type": "markdown", "content": "Following section"}]},
            ],
        }],
    })
    source = await asyncio.to_thread((ROOT / "frontend/videolink-doorbell.js").read_text)

    async def card_script(request):
        # Keep real rendering and sizing. The video is supplied by canvas below.
        return web.Response(text=source + """
customElements.get('videolink-doorbell').prototype._start = async () => {};
""", content_type="application/javascript")

    hass.http.app.router.add_get("/layout-card.js", card_script)
    http = await hass_client()
    origin = str(http.make_url("/")).rstrip("/")
    async with async_playwright() as playwright:
        browser = await playwright.chromium.launch(args=["--autoplay-policy=no-user-gesture-required"])
        context = await browser.new_context()
        await context.add_init_script("localStorage.setItem('hassTokens', " + json.dumps(json.dumps({
            "hassUrl": origin, "access_token": hass_access_token,
            "token_type": "Bearer", "expires_in": 1800,
        })) + ");")
        page = await context.new_page()
        errors = []
        page.on("pageerror", lambda error: errors.append(str(error)))
        page.on("console", lambda message: errors.append(message.text) if message.type == "error" else None)
        try:
            await page.goto(origin + "/layout-test/layout", wait_until="domcontentloaded")
            try:
                await page.locator("videolink-doorbell ha-card").wait_for(timeout=15000)
            except PlaywrightTimeout:
                await page.screenshot(path=str(tmp_path / "dashboard-startup.png"), full_page=True)
                print("BROWSER ERRORS", errors, "URL", page.url)
                print("CARD ERRORS", await page.locator("hui-error-card").evaluate_all("cards => cards.map(c => ({error: c._error, config: c._config, text: c.shadowRoot?.textContent}))"))
                raise
            yield page
        finally:
            await context.close()
            await browser.close()


@pytest.mark.parametrize("viewport_width", [390, 1280])
async def test_video_does_not_overlap_following_section(sections_dashboard, viewport_width):
    from playwright.async_api import expect

    page = sections_dashboard
    await page.set_viewport_size({"width": viewport_width, "height": 900})
    card = page.locator("videolink-doorbell")
    following = page.locator("hui-grid-section").nth(1)
    for video_fit in ("contain", "full"):
        await card.evaluate("(card, fit) => card.setConfig({...card._config, video_fit: fit})", video_fit)
        for width, height in ((640, 360), (640, 480)):
            await card.evaluate("""async (card, [width, height]) => {
                window.layoutStream?.getTracks().forEach(track => track.stop());
                const canvas = document.createElement('canvas');
                canvas.width = width;
                canvas.height = height;
                const context = canvas.getContext('2d');
                context.fillStyle = '#3388aa';
                context.fillRect(0, 0, width, height);
                const video = card.shadowRoot.querySelector('video');
                const stream = window.layoutStream = canvas.captureStream(0);
                video.srcObject = stream;
                stream.getVideoTracks()[0].requestFrame();
                await video.play();
                await new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve)));
            }""", [width, height])
            video = card.locator("video")
            assert await video.evaluate("video => [video.videoWidth, video.videoHeight]") == [width, height]
            await expect(following).to_be_visible()
            bounds = await card.locator("ha-card").bounding_box()
            media = await video.bounding_box()
            controls = await card.locator(".controls").bounding_box()
            next_section = await following.bounding_box()
            assert bounds and media and controls and next_section
            expected_ratio = width / height if video_fit == "full" else 16 / 9
            assert abs(media["height"] - media["width"] / expected_ratio) < 2
            assert controls["y"] >= media["y"] + media["height"] - 1
            assert bounds["y"] + bounds["height"] >= controls["y"] + controls["height"] - 1
            assert next_section["y"] >= bounds["y"] + bounds["height"], {
                "viewport": viewport_width, "fit": video_fit, "stream": [width, height],
                "card": bounds, "next_section": next_section,
            }
    await page.evaluate("window.layoutStream.getTracks().forEach(track => track.stop())")
