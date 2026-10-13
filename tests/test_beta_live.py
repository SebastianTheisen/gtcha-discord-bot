"""Beta: Live-Verbindung im echten Browser - "Live" nach dem Start, und eine hängende Verbindung (wie nach einem
Server-Neustart über Tailscale auf iOS) wird vom Wächter verworfen und neu aufgebaut statt ewig "Verbinde…"."""

import asyncio
import os

import pytest

from tests.test_ui_smoke import _seed
from webapp.server import BETA_DIST

playwright_api = pytest.importorskip("playwright.async_api")


async def _run(tmp_path, browser_path):
    from aiohttp.test_utils import TestServer

    from webapp.server import App, make_app
    await _seed(tmp_path)
    app = App(str(tmp_path / "gtcha_bot.db"), str(tmp_path), "x", role="beta")
    server = TestServer(make_app(app), port=0)
    await server.start_server()
    base = str(server.make_url("/")).rstrip("/")
    token = (await app.bridge.redeem_code(await app.bridge.create_code(42, "Basti")))["token"]
    states = {}
    async with playwright_api.async_playwright() as pw:
        try:
            browser = await pw.chromium.launch(**({"executable_path": browser_path} if browser_path else {}))
        except Exception as e:
            await server.close()
            if os.getenv("CI"):
                raise
            pytest.skip(f"Kein Chromium: {e}")
        ctx = await browser.new_context(viewport={"width": 390, "height": 844}, service_workers="block")
        page = await ctx.new_page()
        hang = {"on": False}

        async def route(r):
            if not r.request.url.startswith(base):
                return await r.abort()
            if hang["on"] and "/api/stream" in r.request.url:
                return   # nie beantworten: Verbindung hängt ohne Antwort und ohne Fehler
            await r.continue_()

        await page.route("**/*", route)
        await page.goto(base + "/")
        await page.evaluate("t => localStorage.setItem('deviceToken', t)", token)
        live = page.locator("header .live")

        await page.reload()
        await live.filter(has_text="Live").wait_for(timeout=10000)
        states["start"] = await live.inner_text()

        hang["on"] = True     # Neustart simulieren: neue Verbindung bleibt hängen
        await page.evaluate("document.dispatchEvent(new Event('visibilitychange'))")
        await live.filter(has_text="Verbinde").wait_for(timeout=5000)
        states["hanging"] = await live.inner_text()
        await live.filter(has_text="Offline").wait_for(timeout=15000)   # Wächter nach 10 s
        states["watchdog"] = await live.inner_text()

        hang["on"] = False    # Server wieder erreichbar: Neuaufbau nach 2 s
        await live.filter(has_text="Live").wait_for(timeout=10000)
        states["recovered"] = await live.inner_text()
        await browser.close()
    await server.close()
    return {k: v.strip() for k, v in states.items()}


@pytest.mark.skipif(not (BETA_DIST / "index.html").exists(), reason="Beta nicht gebaut (npm run build)")
def test_beta_live_connection_recovers_from_hanging_stream(tmp_path, monkeypatch):
    monkeypatch.setenv("APP_ADMIN_IDS", "42")   # Admin: immer freigeschaltet (ohne Übertragung)
    browser_path = os.getenv("UI_TEST_CHROMIUM") or (
        "/opt/pw-browsers/chromium" if os.path.exists("/opt/pw-browsers/chromium") else None)
    states = asyncio.run(_run(tmp_path, browser_path))
    assert states == {"start": "Live", "hanging": "Verbinde…", "watchdog": "Offline", "recovered": "Live"}
