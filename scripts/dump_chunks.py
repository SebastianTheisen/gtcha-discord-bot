"""Lädt alle öffentlichen JavaScript-Dateien (/_nuxt/*.js) der Seite für eine lückenlose Code-Analyse.

Nur statische Code-Dateien, keine Daten, kein Login. Ergebnis: ~/gtcha-discord-bot/data/site_chunks.tar.gz

Aufruf auf dem VPS (im laufenden Container):
    docker exec -i gtcha-discord-bot python - < scripts/dump_chunks.py
"""

import asyncio
import os
import re
import shutil
import tarfile

from playwright.async_api import async_playwright

BASE = "https://gtchaxonline.com"
PROXY = os.getenv("SCRAPER_PROXY")
OUT = "/app/data/site_chunks"
ARCHIVE = "/app/data/site_chunks.tar.gz"
CHUNK_RE = re.compile(r"""["'`]\./([A-Za-z0-9_.-]+\.js)["'`]""")

FETCH_JS = """async (url) => { const r = await fetch(url); return {status: r.status, body: await r.text()}; }"""


async def main():
    shutil.rmtree(OUT, ignore_errors=True)
    os.makedirs(OUT)
    async with async_playwright() as pw:
        browser = await pw.chromium.launch(
            headless=True, args=["--no-sandbox", "--disable-dev-shm-usage", "--blink-settings=imagesEnabled=false"])
        ctx = await browser.new_context(locale="de-DE", proxy={"server": PROXY} if PROXY else None)
        page = await ctx.new_page()
        loaded = {}

        async def on_response(resp):
            if resp.request.resource_type == "script" and "/_nuxt/" in resp.url:
                try:
                    loaded[resp.url.split("/_nuxt/")[1].split("?")[0]] = await resp.text()
                except Exception:
                    pass

        page.on("response", on_response)
        await page.goto(BASE, wait_until="domcontentloaded", timeout=120000)
        await asyncio.sleep(8)
        print(f"Startseite: {len(loaded)} Dateien geladen")

        files = dict(loaded)
        queue = [n for code in files.values() for n in CHUNK_RE.findall(code) if n not in files]
        failed = []
        while queue:
            name = queue.pop()
            if name in files or name in failed:
                continue
            res = await page.evaluate(FETCH_JS, f"/_nuxt/{name}")
            if res["status"] != 200:
                failed.append(name)
                continue
            files[name] = res["body"]
            queue += [n for n in CHUNK_RE.findall(res["body"]) if n not in files]
            if len(files) % 20 == 0:
                print(f"  {len(files)} Dateien...")
        await browser.close()

    for name, code in files.items():
        with open(f"{OUT}/{name}", "w") as f:
            f.write(code)
    with tarfile.open(ARCHIVE, "w:gz") as tar:
        tar.add(OUT, arcname="site_chunks")
    print(f"Fertig: {len(files)} Dateien, {len(failed)} nicht abrufbar")
    print(f"Archiv: {ARCHIVE} ({os.path.getsize(ARCHIVE) // 1024} KB)  ->  VPS: ~/gtcha-discord-bot/data/site_chunks.tar.gz")


asyncio.run(main())
