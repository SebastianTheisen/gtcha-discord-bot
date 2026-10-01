"""Speichert den kompletten Code der Seite und alle API-Antworten für eine Offline-Analyse.

Nur lesend und ohne Login. Lädt Startseite (alle Tabs) und eine Detailseite über den Proxy
des Bots und schreibt alles in ~/gtcha-discord-bot/data/site_dump.tar.gz.

Aufruf auf dem VPS (im laufenden Container):
    docker exec -i gtcha-discord-bot python - 24114 < scripts/dump_site.py
"""

import asyncio
import json
import os
import re
import shutil
import sys
import tarfile

from playwright.async_api import async_playwright

BASE = "https://gtchaxonline.com"
PROXY = os.getenv("SCRAPER_PROXY")
OUT = "/app/data/site_dump"
ARCHIVE = "/app/data/site_dump.tar.gz"

FETCH_JS = """async (url) => {
    const r = await fetch(url, {headers: {Accept: 'application/json'}});
    return {status: r.status, body: await r.text()};
}"""


def safe_name(url):
    return re.sub(r"[^A-Za-z0-9._-]+", "_", url.split("://", 1)[-1])[:150]


async def main():
    pid = int(sys.argv[1]) if len(sys.argv) > 1 else 24114
    shutil.rmtree(OUT, ignore_errors=True)
    for sub in ("js", "css", "api", "html"):
        os.makedirs(f"{OUT}/{sub}", exist_ok=True)
    print(f"Proxy: {PROXY or 'keiner'}")

    async with async_playwright() as pw:
        browser = await pw.chromium.launch(
            headless=True, args=["--no-sandbox", "--disable-dev-shm-usage", "--blink-settings=imagesEnabled=false"])
        ctx = await browser.new_context(locale="de-DE", proxy={"server": PROXY} if PROXY else None)
        page = await ctx.new_page()
        api_log, counter = [], {"n": 0}

        async def on_response(resp):
            url, rtype = resp.url, resp.request.resource_type
            try:
                if rtype == "script" and "gtchaxonline" in url:
                    with open(f"{OUT}/js/{safe_name(url.split('?')[0])}", "w") as f:
                        f.write(await resp.text())
                elif rtype == "stylesheet" and "gtchaxonline" in url:
                    with open(f"{OUT}/css/{safe_name(url.split('?')[0])}", "w") as f:
                        f.write(await resp.text())
                elif "/api/" in url:
                    counter["n"] += 1
                    body = await resp.text()
                    name = f"{counter['n']:03d}_{resp.request.method}_{safe_name(url)}.json"
                    with open(f"{OUT}/api/{name}", "w") as f:
                        f.write(body)
                    api_log.append({"file": name, "method": resp.request.method, "url": url,
                                    "status": resp.status, "post_data": resp.request.post_data,
                                    "request_headers": await resp.request.all_headers()})
            except Exception as e:
                print(f"  Speicherfehler {url[:80]}: {e}")

        page.on("response", on_response)

        print("Startseite...")
        await page.goto(BASE, wait_until="domcontentloaded", timeout=120000)
        await page.wait_for_selector(".pack_menu_list .pack_menu", timeout=90000)
        await asyncio.sleep(4)
        with open(f"{OUT}/html/home.html", "w") as f:
            f.write(await page.content())
        tabs = await page.query_selector_all(".pack_menu_list .pack_menu")
        for i in range(len(tabs)):
            tabs = await page.query_selector_all(".pack_menu_list .pack_menu")
            try:
                await tabs[i].click()
                await asyncio.sleep(3)
            except Exception:
                pass

        print(f"Detailseite {pid}...")
        await page.goto(f"{BASE}/pack-detail?packId={pid}", wait_until="domcontentloaded", timeout=120000)
        await asyncio.sleep(8)
        for _ in range(6):
            await page.mouse.wheel(0, 5000)
            await asyncio.sleep(1)
        with open(f"{OUT}/html/detail.html", "w") as f:
            f.write(await page.content())

        print("Komplette Kartenliste...")
        first = json.loads((await page.evaluate(FETCH_JS, f"/api/user/pack/card_list/{pid}/1"))["body"])
        pages = int((first.get("page") or {}).get("all_page") or 1)
        cards = list(first.get("list") or [])
        for n in range(2, pages + 1):
            body = (await page.evaluate(FETCH_JS, f"/api/user/pack/card_list/{pid}/{n}"))["body"]
            cards += json.loads(body).get("list") or []
        with open(f"{OUT}/card_list_{pid}_komplett.json", "w") as f:
            json.dump(cards, f, ensure_ascii=False, indent=1)

        with open(f"{OUT}/api_log.json", "w") as f:
            json.dump(api_log, f, ensure_ascii=False, indent=1)
        cookies = [{k: c[k] for k in ("name", "domain", "path", "httpOnly")} for c in await ctx.cookies()]
        with open(f"{OUT}/cookies_ohne_werte.json", "w") as f:
            json.dump(cookies, f, indent=1)
        await browser.close()

    with tarfile.open(ARCHIVE, "w:gz") as tar:
        tar.add(OUT, arcname="site_dump")
    js_count = len(os.listdir(f"{OUT}/js"))
    print(f"\nFertig: {js_count} Skripte, {len(api_log)} API-Antworten, {len(cards)} Karten")
    print(f"Archiv: {ARCHIVE} ({os.path.getsize(ARCHIVE) // 1024} KB)  ->  VPS: ~/gtcha-discord-bot/data/site_dump.tar.gz")


asyncio.run(main())
