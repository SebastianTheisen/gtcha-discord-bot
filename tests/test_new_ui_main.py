"""Neue Oberfläche als Haupt-App (WEBAPP_UI=beta): bisherige Geräte behalten Verknüpfung - nichts neu einrichten."""

import asyncio
import json
import os

import pytest

from tests.test_ui_smoke import _seed
from webapp.server import BETA_DIST

playwright_api = pytest.importorskip("playwright.async_api")


async def _run(tmp_path, browser_path):
    from aiohttp.test_utils import TestServer

    from webapp.server import App, make_app
    await _seed(tmp_path)
    app = App(str(tmp_path / "gtcha_bot.db"), str(tmp_path), "x")    # Live-Container (keine Beta-Rolle)
    assert app.new_ui and not app.beta
    server = TestServer(make_app(app), port=0)
    await server.start_server()
    base = str(server.make_url("/")).rstrip("/")
    token = (await app.bridge.redeem_code(await app.bridge.create_code(42, "Basti")))["token"]
    out = {}
    async with playwright_api.async_playwright() as pw:
        try:
            browser = await pw.chromium.launch(**({"executable_path": browser_path} if browser_path else {}))
        except Exception as e:
            await server.close()
            if os.getenv("CI"):
                raise
            pytest.skip(f"Kein Chromium: {e}")
        page = await (await browser.new_context(service_workers="block")).new_page()
        await page.route("**/*", lambda r: r.continue_() if r.request.url.startswith(base) else r.abort())
        await page.goto(base + "/")
        # Gerät, das bisher die alte App genutzt hat: Verknüpfung liegt im selben Speicher
        await page.evaluate("t => localStorage.setItem('deviceToken', t)", token)
        await page.goto(base + "/#/banner/24114")      # Link wie aus einem Push der alten App
        await page.reload()
        await page.wait_for_selector("text=Zurück", timeout=10000)   # Detailseite ohne neue Verknüpfung
        await page.goto(base + "/#/")
        await page.locator("header .live").filter(has_text="Live").wait_for(timeout=10000)
        out["title"] = await page.title()
        out["badge"] = await page.locator("header .beta").count()
        out["cards"] = await page.locator(".card, .banner-card, article").count()
        manifest = await page.evaluate("fetch('/manifest.webmanifest').then(r => r.json())")
        out["manifest"] = (manifest["name"], manifest["start_url"], manifest.get("id"))
        await browser.close()
    await server.close()
    return out


@pytest.mark.skipif(not (BETA_DIST / "index.html").exists(), reason="Beta nicht gebaut (npm run build)")
def test_live_container_serves_new_ui_without_relinking(tmp_path, monkeypatch):
    monkeypatch.setenv("WEBAPP_UI", "beta")
    monkeypatch.setenv("APP_ADMIN_IDS", "42")
    browser_path = os.getenv("UI_TEST_CHROMIUM") or (
        "/opt/pw-browsers/chromium" if os.path.exists("/opt/pw-browsers/chromium") else None)
    out = asyncio.run(_run(tmp_path, browser_path))
    assert out["title"] == "GTCHA Tracker" and out["badge"] == 0
    assert out["manifest"] == ("GTCHA Tracker", "/", None)   # gleiche App wie bisher (installierte bleiben)
