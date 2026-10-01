"""Vergleicht die aktuelle Kartenliste eines Banners (von der Seite) mit der gespeicherten.

Zeigt geänderte Werte, neue/fehlende Karten und alle Karten nahe einem Betrag.

Aufruf auf dem VPS:
    docker exec -i gtcha-discord-bot python - 24149 14900 < tools/compare_pool.py
"""

import asyncio
import json
import os
import sqlite3
import sys

sys.path.insert(0, "/app")
from playwright.async_api import async_playwright  # noqa: E402

from scraper.gtcha_scraper import FETCH_CARD_LIST_JS  # noqa: E402

KIND = {0: "normal", 1: "nur Coins", 2: "nur Versand"}


def fmt(v):
    return f"{int(v):,}".replace(",", ".")


async def main():
    pid = int(sys.argv[1])
    target = int(sys.argv[2]) if len(sys.argv) > 2 else None
    proxy = os.getenv("SCRAPER_PROXY")
    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=True, args=["--no-sandbox", "--disable-dev-shm-usage"])
        ctx = await browser.new_context(locale="de-DE", proxy={"server": proxy} if proxy else None)
        page = await ctx.new_page()
        await page.goto("https://gtchaxonline.com/api/user/point", wait_until="domcontentloaded", timeout=60000)
        cards = await page.evaluate(FETCH_CARD_LIST_JS, pid) or []
        await browser.close()
    print(f"Aktuelle Kartenliste von {pid}: {len(cards)} Einträge")

    row = sqlite3.connect("/app/data/gtcha_bot.db").execute(
        "SELECT card_pool, pool_updated_at FROM banners WHERE pack_id = ?", (pid,)).fetchone()
    stored = json.loads(row[0]) if row and row[0] else {}
    print(f"Gespeicherte Liste vom: {row[1] if row else '-'}")
    stored_hits = {h["id"]: h for h in stored.get("hits", [])}
    stored_values = {int(v) for v in (stored.get("normal_values") or {})} | {h["value"] for h in stored_hits.values()}

    print("\nVersand-Hits: gespeichert -> aktuell")
    for c in cards:
        if int(c.get("action_type") or 0) == 2:
            old = stored_hits.get(str(c.get("id")))
            mark = "" if old and old["value"] == int(c["buy_point"]) else "  <-- GEÄNDERT/NEU"
            print(f"  {c['name'][:45]:<45} {fmt(old['value']) if old else '-':>9} -> {fmt(c['buy_point']):>9}{mark}")

    new_values = sorted({int(c["buy_point"]) for c in cards} - stored_values, reverse=True)
    print(f"\nWerte, die in der gespeicherten Liste nicht vorkommen: {[fmt(v) for v in new_values[:15]] or 'keine'}")

    if target:
        print(f"\nAlle aktuellen Karten zwischen {fmt(target * 0.8)} und {fmt(target * 1.2)} Coins:")
        near = [c for c in cards if target * 0.8 <= int(c["buy_point"]) <= target * 1.2]
        for c in sorted(near, key=lambda c: -int(c["buy_point"])):
            kind = KIND.get(int(c.get("action_type") or 0), c.get("action_type"))
            print(f"  {fmt(c['buy_point']):>9}  x{c.get('duplication')}  {kind:<12} {c['name'][:50]}")
        if not near:
            print("  keine")


asyncio.run(main())
