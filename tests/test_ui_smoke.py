"""Oberflächen-Test: echter Server mit Testdaten, jede Seite in Chromium öffnen.

Schlägt fehl bei JS-Fehlern, fehlendem Design (CSS nicht geladen) oder Fehlermeldungen auf der Seite -
so wäre z. B. die Seite ohne Design nach dem Übertragen vor dem Merge aufgefallen.
Ohne installierten Browser wird der Test übersprungen (im CI wird Chromium installiert).
"""

import asyncio
import json
import os

import aiosqlite
import pytest

from tests.test_webapp_history import COINS, PENDING, SHIPPED

playwright_api = pytest.importorskip("playwright.async_api")

PAGES = [
    ("#/", "Banner-Liste"),
    ("#/hot", "Top 10"),
    ("#/banner/24114", "Banner-Detail"),
    ("#/search", "Suche"),
    ("#/settings", "Ich"),
    ("#/inbox", "Glocke"),
    ("#/users", "Nutzer (Admin)"),
]


async def _seed(tmp_path):
    from database.db import Database
    from utils.card_pool import summarize_cards
    db = Database(str(tmp_path / "gtcha_bot.db"))
    await db.init()
    pool = summarize_cards([
        {"id": 1034580, "name": "Pikachu ex", "buy_point": 45000, "duplication": 1, "action_type": 2},
        {"id": 7, "name": "Glurak", "buy_point": 9000, "duplication": 2, "action_type": 0},
        {"id": 8, "name": "Normal", "buy_point": 600, "duplication": 60, "action_type": 0},
    ])
    async with aiosqlite.connect(db.db_path) as conn:
        for pid, price in ((24114, 1000), (24141, 3000)):
            await conn.execute(
                "INSERT INTO banners (pack_id, category, title, price_coins, current_packs, total_packs, is_active, "
                "created_at, updated_at, card_pool) VALUES (?, 'Pokemon', ?, ?, 40, 63, 1, '2026-09-01', '2026-10-01', ?)",
                (pid, f"Test {pid}", price, json.dumps(pool)))
        await conn.execute(   # Store-Pack (nur App): is_active = 2
            "INSERT INTO banners (pack_id, category, title, price_coins, current_packs, total_packs, is_active, "
            "created_at, updated_at, card_pool) VALUES (24126, 'Store', '宝石ガチャ BtoB', 5000, 200, 200, 2, "
            "'2026-10-03', '2026-10-03', ?)", (json.dumps(pool),))
        await conn.execute(   # beendeter Banner (Archiv)
            "INSERT INTO banners (pack_id, category, title, price_coins, current_packs, total_packs, is_active, "
            "created_at, updated_at, card_pool) VALUES (24099, 'MIX', 'Altes Pack', 800, 0, 50, 0, "
            "'2026-09-01', datetime('now'), ?)", (json.dumps(pool),))
        await conn.execute("INSERT INTO pack_history (banner_id, old_count, new_count, changed_at) "
                           "VALUES (24114, 41, 40, '2026-10-01T12:00:00')")
        await conn.commit()


async def _run(tmp_path, browser_path):
    from aiohttp.test_utils import TestServer

    from webapp.server import App, make_app
    await _seed(tmp_path)
    app = App(str(tmp_path / "gtcha_bot.db"), str(tmp_path), "x")
    server = TestServer(make_app(app), port=0)
    await server.start_server()
    base = str(server.make_url("/")).rstrip("/")
    code = await app.bridge.create_code(42, "Basti")
    token = (await app.bridge.redeem_code(code))["token"]
    os.environ["APP_ADMIN_IDS"] = "42"   # Admin-Abschnitt "Discord-Ansicht" und Reiter "Nutzer" mit prüfen
    problems = []
    async with playwright_api.async_playwright() as pw:
        try:
            browser = await pw.chromium.launch(**({"executable_path": browser_path} if browser_path else {}))
        except Exception as e:
            await server.close()
            if os.getenv("CI"):   # im CI muss der Test laufen - nicht unbemerkt überspringen
                raise
            pytest.skip(f"Kein Chromium: {e}")
        page = await browser.new_page(viewport={"width": 390, "height": 844})
        # nur der eigene Server - Bilder/GTCHA nicht aus dem Netz laden
        await page.route("**/*", lambda route: route.continue_() if route.request.url.startswith(base)
                         else route.abort())
        errors = []
        page.on("pageerror", lambda e: errors.append(str(e)))
        await page.goto(base + "/")
        await page.evaluate("t => { localStorage.setItem('deviceToken', t); localStorage.setItem('wish', "
                            "JSON.stringify([{id: '1034580', name: 'Pikachu ex'}])); }", token)
        # Lesezeichen-Ablauf: Formular wie vom Lesezeichen -> eigenständige Ergebnisseite
        payload = {"t": token, "ms": 5000, "v": 0, "pages": [
            {"path": "buy-point-history", "pages": [{"text": COINS}]},
            {"path": "pending-detail", "pages": [{"text": PENDING}]},
            {"path": "shipped-detail", "pages": [{"text": SHIPPED}]}]}
        await page.evaluate("""([url, d]) => { const f = document.createElement('form'); f.method = 'POST';
            f.action = url; const i = document.createElement('input'); i.name = 'd'; i.value = d;
            f.appendChild(i); document.body.appendChild(f); f.submit(); }""",
                            [base + "/api/import-form", json.dumps(payload)])
        await page.wait_for_selector("h1")
        if "übertragen" not in (await page.inner_text("body")):
            problems.append("Ergebnisseite: kein Ergebnis")
        for hash_, name in PAGES:
            errors.clear()
            await page.goto(f"{base}/{hash_}")
            if hash_ == "#/inbox":   # ohne echtes Push-Abo: Abo vortäuschen
                await page.evaluate("() => { window.currentSubscription = async () => ({ endpoint: 'E' }); route(); }")
            await page.wait_for_timeout(1200)
            styled = await page.evaluate("getComputedStyle(document.querySelector('.tabbar')).position === 'fixed'")
            text = await page.inner_text("#view")
            if not styled:
                problems.append(f"{name}: Design (CSS) nicht geladen")
            if not text.strip() or "nicht erreichbar" in text or "Lädt …" == text.strip():
                problems.append(f"{name}: kein Inhalt ({text[:80]!r})")
            problems += [f"{name}: JS-Fehler {e}" for e in errors]
        # Kategorie "Store": das Store-Pack erscheint dort und unter "Alle" (wie jeder andere Pack)
        await page.goto(base + "/#/")
        await page.wait_for_timeout(1000)
        await page.click('[data-cat="Alle"]')
        await page.wait_for_timeout(500)
        if "24126" not in await page.inner_text("#results"):
            problems.append("Store-Pack fehlt unter 'Alle'")
        await page.click('[data-cat="Store"]')
        await page.wait_for_timeout(500)
        text = await page.inner_text("#results")
        if "24126" not in text:
            problems.append("Store-Pack fehlt in der Kategorie 'Store'")
        if "Edelstein-Gacha BtoB" not in text:   # Titel sichtbar und übersetzt
            problems.append(f"Store-Pack ohne deutschen Titel: {text[:120]!r}")
        if "24099" in await page.inner_text("#results") or "24099" in text:
            problems.append("Beendeter Banner steht in der normalen Liste")
        await page.click('[data-cat="Archiv"]')
        await page.wait_for_timeout(1000)
        text = await page.inner_text("#results")
        if "24099" not in text or "Beendet am" not in text or "24126" in text:
            problems.append(f"Archiv falsch: {text[:160]!r}")
        # Zurück: direkt geöffnete Detailseite -> Übersicht; aus der Liste -> wieder die Liste (eine Ebene)
        page2 = await browser.new_page(viewport={"width": 390, "height": 844})
        await page2.route("**/*", lambda route: route.continue_() if route.request.url.startswith(base)
                          else route.abort())
        await page2.goto(base + "/#/banner/24099")
        await page2.wait_for_timeout(1000)
        detail = await page2.inner_text("#view")
        if ("Beendet" not in detail or "Lohnt sich" in detail or "Rest kaufen" in detail
                or "Ø Rückgabe pro Zug" in detail or "Noch im Banner" in detail):
            problems.append(f"Archiv-Detail falsch: {detail[:160]!r}")
        await page2.click("[data-back]")
        await page2.wait_for_timeout(800)
        if not page2.url.endswith("#/"):
            problems.append(f"Zurück ohne Vorgeschichte geht nicht zur Übersicht: {page2.url}")
        await page2.goto(base + "/#/hot")
        await page2.wait_for_timeout(800)
        await page2.evaluate("location.hash = '#/banner/24114'")
        await page2.wait_for_timeout(1000)
        await page2.click("[data-back]")
        await page2.wait_for_timeout(800)
        if not page2.url.endswith("#/hot"):
            problems.append(f"Zurück geht nicht eine Ebene zurück: {page2.url}")
        await browser.close()
    await server.close()
    return problems


def test_all_pages_render_without_errors(tmp_path):
    browser_path = os.getenv("UI_TEST_CHROMIUM") or (
        "/opt/pw-browsers/chromium" if os.path.exists("/opt/pw-browsers/chromium") else None)
    problems = asyncio.run(_run(tmp_path, browser_path))
    assert problems == []
