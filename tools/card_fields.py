"""Zeigt alle Felder, die GTCHA in der Kartenliste eines Banners mitliefert (teuerste Karten zuerst).

Zum Prüfen, ob es z. B. Set, Kartennummer oder Seltenheit gibt (für genaue Cardmarket-Links).
Nutzt dieselbe öffentliche Kartenliste, die der Bot ohnehin lädt.

Aufruf auf dem VPS:
    docker exec -i gtcha-discord-bot python - 24093 < tools/card_fields.py
    docker exec -i gtcha-discord-bot python - 24093 10 < tools/card_fields.py      # 10 Karten statt 3
"""

import asyncio
import json
import os
import sys

sys.path.insert(0, "/app")
from playwright.async_api import async_playwright  # noqa: E402

from scraper.gtcha_scraper import FETCH_CARD_LIST_JS  # noqa: E402


async def main():
    pid = int(sys.argv[1])
    count = int(sys.argv[2]) if len(sys.argv) > 2 else 3
    proxy = os.getenv("SCRAPER_PROXY")
    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=True, args=["--no-sandbox", "--disable-dev-shm-usage"])
        ctx = await browser.new_context(locale="de-DE", proxy={"server": proxy} if proxy else None)
        page = await ctx.new_page()
        await page.goto("https://gtchaxonline.com/api/user/point", wait_until="domcontentloaded", timeout=60000)
        cards = await page.evaluate(FETCH_CARD_LIST_JS, pid) or []
        await browser.close()
    print(f"Banner {pid}: {len(cards)} Karten")
    keys = sorted({k for c in cards for k in c})
    print("Felder:", ", ".join(keys))
    for c in sorted(cards, key=lambda c: -float(c.get("buy_point") or 0))[:count]:
        print("\n" + json.dumps(c, ensure_ascii=False, indent=1))


asyncio.run(main())
