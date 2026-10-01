"""Fragt alle bekannten öffentlichen Adressen nach einem Banner ab und zeigt Zähler-Felder.

Sucht eine Quelle, die Versand-/Umwandlungs-Summen aktueller liefert als pack/list.
Neuer Browser über Tor, frische Sitzung.

Aufruf auf dem VPS:
    docker exec -i gtcha-discord-bot python - 24149 < tools/probe_endpoints.py
"""

import asyncio
import json
import os
import sys

sys.path.insert(0, "/app")
from playwright.async_api import async_playwright  # noqa: E402

BASE = "https://gtchaxonline.com"
HINTS = ("send", "kangen", "count", "stock", "remain", "total", "sold", "num", "pay")
FETCH_JS = """async (url) => {
    const r = await fetch(url, {headers: {Accept: 'application/json'}});
    const t = await r.text();
    try { return {status: r.status, json: JSON.parse(t)}; } catch (e) { return {status: r.status, text: t.slice(0, 120)}; }
}"""


def find_banner(data, pid):
    """Den Eintrag des Banners in einer Antwort finden (Liste oder Detail)."""
    if isinstance(data, dict):
        if str(data.get("id")) == str(pid):
            return data
        for value in data.values():
            found = find_banner(value, pid)
            if found:
                return found
    elif isinstance(data, list):
        for value in data:
            found = find_banner(value, pid)
            if found:
                return found
    return None


async def main():
    pid = int(sys.argv[1]) if len(sys.argv) > 1 else 24149
    proxy = os.getenv("SCRAPER_PROXY")
    paths = ["/api/user/pack/list", f"/api/user/pack/detail/{pid}", "/api/user/pack/list/13",
             "/api/user/pack/list/1", "/api/user/pack/all_list/1", "/api/user/pack/all_list//1"]
    for card_type in ("2", "5", "9", "999996", ""):
        for page in ("1",):
            paths.append(f"/api/user/pack/all_list/{card_type}/{page}")
            paths.append(f"/api/user/pack/all_list/{page}?index={card_type}")
    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=True, args=["--no-sandbox", "--disable-dev-shm-usage"])
        ctx = await browser.new_context(locale="de-DE", proxy={"server": proxy} if proxy else None)
        page = await ctx.new_page()
        await page.goto(f"{BASE}/pack-detail?packId={pid}", wait_until="domcontentloaded", timeout=90000)
        await asyncio.sleep(3)
        for path in paths:
            res = await page.evaluate(FETCH_JS, path)
            body = res.get("json")
            item = find_banner(body, pid) if body is not None else None
            if item:
                fields = {k: v for k, v in item.items() if any(h in k.lower() for h in HINTS)}
                print(f"{path} -> {res['status']} | {json.dumps(fields, ensure_ascii=False)}")
            else:
                info = res.get("text") or (json.dumps(body, ensure_ascii=False)[:120] if body is not None else "")
                print(f"{path} -> {res['status']} | Banner nicht enthalten | {info}")
        await browser.close()


asyncio.run(main())
