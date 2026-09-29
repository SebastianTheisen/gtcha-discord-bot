"""Lädt die Seite mit Playwright direkt und über WARP und vergleicht API- und DOM-Pack-Zahlen.

Aufruf auf dem VPS (im laufenden Container, kein Rebuild nötig):
    docker exec -i gtcha-discord-bot python - 24105 < scripts/diagnose_browser.py
"""

import asyncio
import json
import os
import re
import sys

from playwright.async_api import async_playwright

BASE = "https://gtchaxonline.com"
PROXY = os.getenv("SCRAPER_PROXY") or "socks5://127.0.0.1:40000"
UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
PACK_FIELDS = ["pack_count", "pack_remaining", "remaining_count", "remaining", "stock", "packs"]


def packs(item):
    for f in PACK_FIELDS:
        if item.get(f) is not None:
            return f"{item[f]} ({f})"
    return "?"


async def run(pw, label, proxy, wanted):
    print(f"\n######## {label} ########")
    browser = await pw.chromium.launch(headless=True, args=["--no-sandbox", "--disable-dev-shm-usage"])
    ctx = await browser.new_context(user_agent=UA, locale="de-DE",
                                    proxy={"server": proxy} if proxy else None)
    page = await ctx.new_page()
    shown_requests = set()

    async def on_response(resp):
        if "/api/" not in resp.url:
            return
        req = resp.request
        key = (req.method, resp.url.split("?")[0])
        if key not in shown_requests:
            shown_requests.add(key)
            post = (req.post_data or "")[:200]
            print(f"  API {req.method} {resp.url[:120]} -> {resp.status}" + (f" | POST: {post}" if post else ""))
        try:
            data = await resp.json()
        except Exception:
            return
        if isinstance(data, dict) and isinstance(data.get("list"), list):
            extra = {k: v for k, v in data.items() if k != "list"}
            if extra:
                print(f"    Weitere Felder: {json.dumps(extra, ensure_ascii=False)[:300]}")
            for it in data["list"]:
                if it.get("id") and int(it["id"]) in wanted:
                    print(f"    API {it['id']}: {packs(it)} | {json.dumps(it, ensure_ascii=False)[:500]}")

    page.on("response", on_response)

    try:
        ip = await page.goto("https://www.cloudflare.com/cdn-cgi/trace", timeout=30000)
        trace = await ip.text()
        print("  " + " | ".join(l for l in trace.splitlines() if l.startswith(("ip=", "loc="))))
    except Exception as e:
        print(f"  trace fehlgeschlagen: {e}")

    await page.goto(BASE, wait_until="domcontentloaded", timeout=90000)
    try:
        await page.wait_for_selector(".pack_menu_list .pack_menu", timeout=30000)
    except Exception:
        print("  Tab-Menü nicht gefunden")
    await asyncio.sleep(3)

    tabs = await page.query_selector_all(".pack_menu_list .pack_menu")
    for i in range(len(tabs)):
        tabs = await page.query_selector_all(".pack_menu_list .pack_menu")
        try:
            await tabs[i].click()
        except Exception:
            continue
        await asyncio.sleep(2)
        for pid in wanted:
            el = await page.query_selector(f'[data-pack-id="{pid}"]')
            if not el:
                continue
            bar = await el.query_selector(".gacha_bar")
            text = " ".join((await bar.inner_text()).split()) if bar else "kein .gacha_bar"
            m = re.search(r"(\d[\d,]*)\s*/\s*(\d[\d,]*)", text)
            print(f"    DOM {pid} (Tab {i}): {m.group(0) if m else text}")

    cookies = await ctx.cookies()
    print(f"  Cookies: {', '.join(c['name'] for c in cookies) or 'keine'}")
    await browser.close()


async def main():
    wanted = {int(a) for a in sys.argv[1:]} or {24105}
    async with async_playwright() as pw:
        await run(pw, "DIREKT", None, wanted)
        await run(pw, f"WARP ({PROXY})", PROXY, wanted)


asyncio.run(main())
