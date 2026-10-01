"""Lädt die komplette Kartenliste eines oder mehrerer Banner und gleicht sie mit der Pack-Zahl ab.

Klärt, ob card_list den aktuellen Restbestand zeigt (Summe num == verbleibende Packs)
und berechnet den Durchschnittswert pro Zug.

Aufruf auf dem VPS (im laufenden Container, nutzt dessen Tor-Proxy):
    docker exec -i gtcha-discord-bot python - 24114 24125 < scripts/diagnose_pool.py
"""

import asyncio
import json
import os
import sys
from collections import Counter

from playwright.async_api import async_playwright

BASE = "https://gtchaxonline.com"
PROXY = os.getenv("SCRAPER_PROXY")

FETCH_JS = """async (url) => { const r = await fetch(url, {headers: {Accept: 'application/json'}});
                               try { return await r.json(); } catch (e) { return {fehler: r.status}; } }"""


async def get(page, path):
    return await page.evaluate(FETCH_JS, path)


async def analyse(page, pid):
    print(f"\n################ Banner {pid} ################")
    detail = (await get(page, f"/api/user/pack/detail/{pid}")).get("detail", {})
    price = int(detail.get("point") or 0)
    remaining = int(detail.get("pack_count") or 0)
    total = int(detail.get("total_pack_count") or 0)
    print(f"Preis: {price} Coins | Packs: {remaining} / {total}")

    first = await get(page, f"/api/user/pack/card_list/{pid}/1")
    pages = int((first.get("page") or {}).get("all_page") or 1)
    cards = list(first.get("list") or [])
    for n in range(2, pages + 1):
        cards += (await get(page, f"/api/user/pack/card_list/{pid}/{n}")).get("list") or []
    print(f"Kartenliste: {len(cards)} Einträge auf {pages} Seiten "
          f"(API meldet all={(first.get('page') or {}).get('all')})")
    if not cards:
        print(f"Rohantwort Seite 1: {json.dumps(first, ensure_ascii=False)[:500]}")
        return

    sum_num = sum(int(c.get("num") or 0) for c in cards)
    sum_value = sum(int(c.get("num") or 0) * int(c.get("buy_point") or 0) for c in cards)
    print(f"Summe num: {sum_num}  <->  verbleibende Packs: {remaining}  |  Gesamt-Packs: {total}")
    if sum_num:
        ev = sum_value / sum_num
        ratio = f" = {ev / price * 100:.1f} % des Preises" if price else ""
        print(f"Ø Rückgabe pro Zug (über num gewichtet): {ev:,.0f} Coins{ratio}")

    for field in ("num", "card_type", "action_type", "duplication", "is_digital_content", "rarity"):
        print(f"  Werte von {field}: {dict(Counter(str(c.get(field)) for c in cards).most_common(8))}")

    print("  Top 5 nach buy_point:")
    for i, c in enumerate(sorted(cards, key=lambda c: -int(c.get("buy_point") or 0))[:5], 1):
        print(f"    {i}. {c.get('name')} | {int(c.get('buy_point') or 0):,} Coins | num={c.get('num')} "
              f"| {c.get('image_url')}")
    print("  Niedrigste 3 nach buy_point:")
    for c in sorted(cards, key=lambda c: int(c.get("buy_point") or 0))[:3]:
        print(f"    {c.get('name')} | {c.get('buy_point')} Coins | num={c.get('num')}")


async def main():
    pids = [int(a) for a in sys.argv[1:]] or [24114]
    print(f"Proxy: {PROXY or 'keiner'}")
    async with async_playwright() as pw:
        browser = await pw.chromium.launch(
            headless=True,
            args=["--no-sandbox", "--disable-dev-shm-usage", "--blink-settings=imagesEnabled=false"])
        ctx = await browser.new_context(locale="de-DE", proxy={"server": PROXY} if PROXY else None)
        page = await ctx.new_page()
        await page.goto(BASE, wait_until="domcontentloaded", timeout=120000)
        await asyncio.sleep(3)
        for pid in pids:
            try:
                await analyse(page, pid)
            except Exception as e:
                print(f"Banner {pid}: Fehler {e}")
        await browser.close()


asyncio.run(main())
