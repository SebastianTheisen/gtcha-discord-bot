"""Testet, ob sich Kartenliste und Coin-Werte eines Banners aus der Seite auslesen lassen.

Aufruf auf dem VPS (im laufenden Container, nutzt dessen Tor-Proxy):
    docker exec -i gtcha-discord-bot python - 24114 < scripts/diagnose_cards.py
"""

import asyncio
import json
import os
import sys

from playwright.async_api import async_playwright

BASE = "https://gtchaxonline.com"
PROXY = os.getenv("SCRAPER_PROXY")
VALUE_HINTS = ("point", "coin", "price", "value", "rank", "amount", "sell", "kangen", "count", "num")


def short(obj, limit=900):
    return json.dumps(obj, ensure_ascii=False)[:limit]


def summarize(items):
    first = items[0]
    print(f"    Felder eines Eintrags: {list(first.keys())}")
    print(f"    Erster Eintrag komplett: {short(first)}")
    name_key = next((k for k in ("name", "card_name", "title") if k in first), None)
    value_keys = [k for k in first if any(h in k.lower() for h in VALUE_HINTS)]
    print(f"    Name-Feld: {name_key} | mögliche Wert-Felder: {value_keys}")
    for it in items[:25]:
        values = " | ".join(f"{k}={it.get(k)}" for k in value_keys)
        print(f"      {str(it.get(name_key, '?'))[:45]:<45} {values}")
    if len(items) > 25:
        print(f"      ... ({len(items)} Einträge insgesamt)")


def find_list(data):
    if isinstance(data, list) and data and isinstance(data[0], dict):
        return data
    if isinstance(data, dict):
        for k, v in data.items():
            if k == "csrf":
                continue
            found = find_list(v)
            if found:
                return found
    return None


async def main():
    pid = int(sys.argv[1]) if len(sys.argv) > 1 else 24114
    print(f"Banner {pid} | Proxy: {PROXY or 'keiner'}")
    async with async_playwright() as pw:
        browser = await pw.chromium.launch(
            headless=True,
            args=["--no-sandbox", "--disable-dev-shm-usage", "--blink-settings=imagesEnabled=false"])
        ctx = await browser.new_context(locale="de-DE", proxy={"server": PROXY} if PROXY else None)
        page = await ctx.new_page()
        card_urls = []

        async def on_response(resp):
            if "/api/" in resp.url and "card" in resp.url:
                card_urls.append(f"{resp.request.method} {resp.url.split('?')[0]} -> {resp.status}")

        page.on("response", on_response)
        await page.goto(f"{BASE}/pack-detail?packId={pid}", wait_until="domcontentloaded", timeout=120000)
        await asyncio.sleep(8)
        for _ in range(5):
            await page.mouse.wheel(0, 4000)
            await asyncio.sleep(1)

        print("\n=== Karten-API-Aufrufe der Seite:")
        for u in card_urls or ["(keine)"]:
            print(f"  {u}")

        print("\n=== card_list Seite für Seite (mit der Sitzung des Browsers):")
        for n in range(1, 8):
            data = await page.evaluate(
                """async (url) => { const r = await fetch(url, {headers: {Accept: 'application/json'}});
                                    try { return await r.json(); } catch (e) { return {fehler: r.status}; } }""",
                f"/api/user/pack/card_list/{pid}/{n}")
            items = find_list(data)
            extra = {k: v for k, v in data.items() if k not in ("csrf",) and not isinstance(v, list)} \
                if isinstance(data, dict) else {}
            print(f"  Seite {n}: {len(items) if items else 0} Einträge | weitere Felder: {short(extra, 300)}")
            if not items:
                if n == 1:
                    print(f"    Rohantwort: {short(data, 600)}")
                break
            summarize(items)

        print("\n=== Sichtbarer Text der ersten Karten auf der Seite:")
        cards = await page.eval_on_selector_all(
            ".card-container", "els => els.slice(0, 5).map(e => e.innerText.replace(/\\s+/g, ' ').trim())")
        for c in cards or ["(keine .card-container gefunden)"]:
            print(f"  {c[:200]}")

        await browser.close()


asyncio.run(main())
