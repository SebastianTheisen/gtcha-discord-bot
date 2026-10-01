"""Testet die Banner-Liste über die API und protokolliert die Rückgabe-Zähler je Banner.

A) Liefert pack/all_list bzw. pack/list alle Banner mit Pack-Zahlen? (Scrapen ohne Tabs)
B) Wie bewegen sich total_kangen / total_sendprice / total_sendcount? (Hit-Erkennung)

Aufruf auf dem VPS (im laufenden Container, nutzt dessen Tor-Proxy), mehrmals im Abstand:
    docker exec -i gtcha-discord-bot python - 24114 < scripts/diagnose_api.py
Jeder Lauf hängt eine Zeile pro Banner an ~/gtcha-discord-bot/data/kangen_log.csv an.
"""

import asyncio
import csv
import json
import os
import sys
from datetime import datetime

from playwright.async_api import async_playwright

BASE = "https://gtchaxonline.com"
PROXY = os.getenv("SCRAPER_PROXY")
LOG = "/app/data/kangen_log.csv"
TRACK = ("pack_count", "total_pack_count", "point", "total_kangen", "total_sendprice",
         "total_sendcount", "total_sendpeople", "stock_count", "buy_count")

FETCH_JS = """async (url) => {
    const r = await fetch(url, {headers: {Accept: 'application/json'}});
    const t = await r.text();
    try { return {status: r.status, json: JSON.parse(t)}; } catch (e) { return {status: r.status, text: t.slice(0, 150)}; }
}"""


def items_of(body):
    if isinstance(body, dict):
        for key in ("list", "packs", "data", "items"):
            if isinstance(body.get(key), list):
                return body[key]
    return []


async def main():
    pid = int(sys.argv[1]) if len(sys.argv) > 1 else 24114
    print(f"Proxy: {PROXY or 'keiner'}")
    async with async_playwright() as pw:
        browser = await pw.chromium.launch(
            headless=True, args=["--no-sandbox", "--disable-dev-shm-usage", "--blink-settings=imagesEnabled=false"])
        ctx = await browser.new_context(locale="de-DE", proxy={"server": PROXY} if PROXY else None)
        page = await ctx.new_page()

        seen = []
        page.on("request", lambda r: seen.append(f"{r.method} {r.url}") if "/api/user/pack" in r.url else None)
        await page.goto(BASE, wait_until="domcontentloaded", timeout=120000)
        await page.wait_for_selector(".pack_menu_list .pack_menu", timeout=90000)
        await asyncio.sleep(3)
        tabs = await page.query_selector_all(".pack_menu_list .pack_menu")
        for tab in tabs[:3]:
            try:
                await tab.click()
                await asyncio.sleep(3)
            except Exception:
                pass
        print("\n=== Pack-Anfragen der Startseite (inkl. 3 Tab-Klicks):")
        for s in dict.fromkeys(seen):
            print(f"  {s}")

        card_types = (await page.evaluate(FETCH_JS, "/api/user/card_type")).get("json", {}).get("list", [])
        print(f"\n=== Kategorien (card_type): {[(c.get('id'), c.get('name')) for c in card_types]}")

        candidates = ["/api/user/pack/list", "/api/user/pack/list/1", "/api/user/pack/all_list/1",
                      "/api/user/pack/all_list//1", "/api/user/pack/all_list/0/1"]
        candidates += [f"/api/user/pack/all_list/{c.get('id')}/1" for c in card_types if c.get('id')]
        print("\n=== Banner-Listen über die API:")
        all_items = {}
        for url in candidates:
            res = await page.evaluate(FETCH_JS, url)
            body = res.get("json")
            items = items_of(body)
            pages = body.get("page") if isinstance(body, dict) else None
            print(f"  {url} -> {res.get('status')} | {len(items)} Banner | page: {json.dumps(pages)[:120]}"
                  + ("" if body is not None else f" | {res.get('text')!r}"))
            if items and not all_items:
                print(f"     Felder: {list(items[0].keys())}")
            for it in items:
                if it.get("id"):
                    all_items[int(it["id"])] = it

        print(f"\n=== Zusammen {len(all_items)} verschiedene Banner über die API")
        if pid in all_items:
            it = all_items[pid]
            print(f"  {pid}: " + " | ".join(f"{k}={it.get(k)}" for k in TRACK))
        else:
            print(f"  {pid} nicht in den API-Listen gefunden")

        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        new_file = not os.path.exists(LOG)
        with open(LOG, "a", newline="") as f:
            w = csv.writer(f)
            if new_file:
                w.writerow(["zeit", "pack_id"] + list(TRACK))
            for bid, it in sorted(all_items.items()):
                w.writerow([now, bid] + [it.get(k) for k in TRACK])
        print(f"\n{len(all_items)} Zeilen an {LOG} angehängt (VPS: ~/gtcha-discord-bot/data/kangen_log.csv)")
        await browser.close()


asyncio.run(main())
