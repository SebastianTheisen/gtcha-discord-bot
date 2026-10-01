"""Tiefenanalyse eines Banners: Antwort-Header, Parameter-Varianten und alle Felder.

Sucht versteckte Daten, die die normale Abfrage nicht zeigt: zusätzliche Felder bei anderen
Parametern, Status-Felder pro Karte, Hinweise in HTTP-Headern. Rein lesend, über Tor.

Aufruf auf dem VPS:
    docker exec -i gtcha-discord-bot python - 24111 < tools/deep_probe.py
"""

import asyncio
import json
import os
import sys

sys.path.insert(0, "/app")
from playwright.async_api import async_playwright  # noqa: E402

BASE = "https://gtchaxonline.com"
HINT = ("send", "ship", "draw", "pull", "won", "win", "hit", "sold", "stat", "user", "rank",
        "date", "time", "remain", "left", "owner", "history", "log", "name", "flag")

# Adressen mit Parameter-Varianten, die wir noch nicht alle durch haben
FETCH_JS = """async (url, opts) => {
    const r = await fetch(url, opts || {headers: {Accept: 'application/json'}});
    const h = {}; r.headers.forEach((v, k) => h[k] = v);
    const t = await r.text();
    let body = null; try { body = JSON.parse(t); } catch (e) {}
    return {status: r.status, headers: h, body: body, text: body ? null : t.slice(0, 150)};
}"""


def all_keys(obj, prefix=""):
    """Alle (verschachtelten) Feldnamen einer Antwort sammeln."""
    found = set()
    if isinstance(obj, dict):
        for k, v in obj.items():
            found.add(prefix + k)
            found |= all_keys(v, prefix + k + ".")
    elif isinstance(obj, list) and obj:
        found |= all_keys(obj[0], prefix)
    return found


async def probe(page, label, url, opts=None):
    res = await page.evaluate(FETCH_JS, [url, opts])
    status = res["status"]
    if res["body"] is None:
        print(f"\n{label}: {status} | {res['text']!r}")
        return set()
    keys = all_keys(res["body"])
    interesting = sorted(k for k in keys if any(h in k.lower() for h in HINT))
    print(f"\n{label}: {status} | {len(keys)} Felder")
    if interesting:
        print(f"   interessant: {interesting}")
    hdr = {k: v for k, v in res["headers"].items()
           if k.lower() in ("x-total-count", "x-count", "link", "x-data", "x-debug", "cache-control", "vary")}
    if hdr:
        print(f"   Header: {hdr}")
    return keys


async def main():
    pid = int(sys.argv[1]) if len(sys.argv) > 1 else 24111
    proxy = os.getenv("SCRAPER_PROXY")
    async with async_playwright() as pw:
        b = await pw.chromium.launch(headless=True, args=["--no-sandbox", "--disable-dev-shm-usage"])
        ctx = await b.new_context(locale="de-DE", proxy={"server": proxy} if proxy else None)
        page = await ctx.new_page()
        await page.goto(f"{BASE}/pack-detail?packId={pid}", wait_until="domcontentloaded", timeout=90000)
        await asyncio.sleep(3)

        base = await probe(page, "card_list Seite 1 (normal)", f"/api/user/pack/card_list/{pid}/1")
        # Parameter-Varianten: Sortierung, Status, Filter, Detail
        for suffix in ("?sort=desc", "?order=sale_price+desc", "?type=all", "?status=1", "?all=1",
                       "?is_drawn=1", "?include=history", "?detail=1", "?with=owner", "/all"):
            keys = await probe(page, f"card_list{suffix}", f"/api/user/pack/card_list/{pid}/1{suffix}")
            extra = keys - base
            if extra:
                print(f"   >>> NEUE Felder gegenüber normal: {sorted(extra)}")

        # POST wie bei rewards (die einzige POST-Liste im Code)
        await probe(page, "card_list POST json", f"/api/user/pack/card_list/{pid}/1",
                    {"method": "POST", "headers": {"Content-Type": "application/json"},
                     "body": json.dumps({"type": "all", "order": "sale_price desc"})})

        # pack/detail voll ausgeben
        res = await page.evaluate(FETCH_JS, [f"/api/user/pack/detail/{pid}", None])
        print(f"\npack/detail ALLE Felder: {sorted(all_keys(res['body'] or {}))}")

        # Erste Karte komplett zeigen (sieht man alle Felder einer Karte?)
        first = await page.evaluate(FETCH_JS, [f"/api/user/pack/card_list/{pid}/1", None])
        lst = (first["body"] or {}).get("list") or []
        if lst:
            print(f"\nErste Karte komplett: {json.dumps(lst[0], ensure_ascii=False)}")
        await b.close()


asyncio.run(main())
