"""Lädt die komplette Kartenliste eines oder mehrerer Banner und gleicht sie mit der Pack-Zahl ab.

Klärt, ob card_list den aktuellen Restbestand zeigt (Summe num == verbleibende Packs)
und berechnet den Durchschnittswert pro Zug.

Aufruf auf dem VPS (im laufenden Container, nutzt dessen Tor-Proxy):
    docker exec -i gtcha-discord-bot python - 24114 < scripts/diagnose_pool.py
"""

import asyncio
import json
import os
import re
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

    copies = lambda c: int(c.get("duplication") or 0)
    sum_dup = sum(copies(c) for c in cards)
    sum_value = sum(copies(c) * int(c.get("buy_point") or 0) for c in cards)
    print(f"Summe duplication: {sum_dup}  <->  verbleibende Packs: {remaining}  |  Gesamt-Packs: {total}")
    if sum_dup:
        ev = sum_value / sum_dup
        ratio = f" = {ev / price * 100:.1f} % des Preises" if price else ""
        print(f"Ø Rückgabe pro Zug (Startbestand, mit duplication gewichtet): {ev:,.0f} Coins{ratio}")
    for c in cards:
        if str(c.get("action_type")) != "0":
            print(f"  action_type={c.get('action_type')}: {c.get('name')} | {c.get('buy_point')} Coins | duplication={c.get('duplication')}")

    for field in ("action_type", "duplication", "rarity"):
        print(f"  Werte von {field}: {dict(Counter(str(c.get(field)) for c in cards).most_common(8))}")

    print("  Top 5 nach buy_point:")
    for i, c in enumerate(sorted(cards, key=lambda c: -int(c.get("buy_point") or 0))[:5], 1):
        print(f"    {i}. {c.get('name')} | {int(c.get('buy_point') or 0):,} Coins | duplication={c.get('duplication')} "
              f"| {c.get('image_url')}")
    print("  Niedrigste 3 nach buy_point:")
    for c in sorted(cards, key=lambda c: int(c.get("buy_point") or 0))[:3]:
        print(f"    {c.get('name')} | {c.get('buy_point')} Coins | duplication={c.get('duplication')}")


async def main():
    pids = [int(a) for a in sys.argv[1:]] or [24114]
    print(f"Proxy: {PROXY or 'keiner'}")
    async with async_playwright() as pw:
        browser = await pw.chromium.launch(
            headless=True,
            args=["--no-sandbox", "--disable-dev-shm-usage", "--blink-settings=imagesEnabled=false"])
        ctx = await browser.new_context(locale="de-DE", proxy={"server": PROXY} if PROXY else None)
        page = await ctx.new_page()
        scripts = []
        page.on("response", lambda r: scripts.append(r) if r.request.resource_type == "script" else None)
        await page.goto(f"{BASE}/pack-detail?packId={pids[0]}", wait_until="domcontentloaded", timeout=120000)
        await asyncio.sleep(6)
        for pid in pids:
            try:
                await analyse(page, pid)
            except Exception as e:
                print(f"Banner {pid}: Fehler {e}")

        endpoints = set()
        for resp in scripts:
            try:
                code = await resp.text()
            except Exception:
                continue
            endpoints.update(re.findall(r"""api/user/[A-Za-z0-9_/\-]+""", code))
        print(f"\n=== API-Adressen im JavaScript der Seite ({len(endpoints)}):")
        for e in sorted(endpoints):
            print(f"  {e}")
        print("\n=== Probeabruf von Adressen, die nach Gewinnern/Verlauf klingen:")
        hints = ("rank", "winner", "hist", "result")
        for e in sorted(endpoints):
            if any(h in e.lower() for h in hints):
                for path in (f"/{e.rstrip('/')}/{pids[0]}", f"/{e.rstrip('/')}/{pids[0]}/1", f"/{e.rstrip('/')}"):
                    data = await get(page, path)
                    print(f"  {path}: {json.dumps(data, ensure_ascii=False)[:400]}")
        await browser.close()


asyncio.run(main())
