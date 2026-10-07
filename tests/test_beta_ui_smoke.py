"""Oberflächen-Test der Beta (webapp/beta): echter Server mit Testdaten, jede Seite in Chromium öffnen.

Braucht die gebaute Oberfläche (npm run build in webapp/beta) - ohne Build oder ohne Browser übersprungen
(im CI wird beides vorher erledigt)."""

import asyncio
import os
from pathlib import Path

import pytest

from tests.test_ui_smoke import _seed

playwright_api = pytest.importorskip("playwright.async_api")
DIST = Path(__file__).resolve().parent.parent / "webapp" / "beta" / "dist"

PAGES = [
    ("#/", "Banner"), ("#/top", "Top 10"), ("#/search", "Karten suchen"), ("#/banner/24114", "Lohnt sich"),
    ("#/inbox", "Benachrichtigungen"), ("#/me", "Meine gemeldeten Hits"), ("#/users", "System-Status"),
]


async def _run(tmp_path, browser_path):
    from aiohttp.test_utils import TestServer

    from webapp.server import App, make_app
    await _seed(tmp_path)
    os.environ["APP_ADMIN_IDS"] = "42"
    app = App(str(tmp_path / "gtcha_bot.db"), str(tmp_path), "x", role="beta")
    server = TestServer(make_app(app), port=0)
    await server.start_server()
    base = str(server.make_url("/")).rstrip("/") + "/beta/"
    token = (await app.bridge.redeem_code(await app.bridge.create_code(42, "Basti")))["token"]
    problems = []
    async with playwright_api.async_playwright() as pw:
        try:
            browser = await pw.chromium.launch(**({"executable_path": browser_path} if browser_path else {}))
        except Exception as e:
            await server.close()
            if os.getenv("CI"):
                raise
            pytest.skip(f"Kein Chromium: {e}")
        page = await browser.new_page(viewport={"width": 390, "height": 844})
        page.on("pageerror", lambda e: problems.append(f"JS-Fehler: {e}"))
        await page.goto(base)
        await page.evaluate(f"localStorage.setItem('deviceToken', '{token}')")
        await page.reload()
        for path, text in PAGES:
            await page.goto(base + path)
            try:
                await page.get_by_text(text, exact=False).first.wait_for(timeout=8000)
            except Exception:
                problems.append(f"{path}: „{text}“ fehlt")
        # Karte antippen: Melden-Fenster mit Marktplatz-Links
        await page.goto(base + "#/banner/24114")
        await page.locator(".tabs button", has_text="Karten").click()
        await page.locator(".card").first.click()
        for text in ("T1 melden", "Cardmarket"):
            try:
                await page.get_by_text(text).first.wait_for(timeout=5000)
            except Exception:
                problems.append(f"Kartenfenster: „{text}“ fehlt")
        if (await page.inner_text(".live")).strip() != "Live":
            problems.append("Live-Verbindung nicht aufgebaut")
        await browser.close()
    await server.close()
    return problems


@pytest.mark.skipif(not (DIST / "index.html").exists(), reason="Beta nicht gebaut (npm run build in webapp/beta)")
def test_beta_pages_render(tmp_path):
    browser = "/opt/pw-browsers/chromium" if os.path.exists("/opt/pw-browsers/chromium") else None
    problems = asyncio.run(_run(tmp_path, browser))
    assert not problems, "\n".join(problems)
