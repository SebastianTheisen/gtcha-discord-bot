"""Zeigt, wie die öffentliche Seite /store aufgebaut ist (nur lesen, ohne Login, wie der normale Scrape).

Lädt die Seite im selben Browser-Aufbau wie der Bot (über Tor) und gibt aus:
  - alle JSON-Antworten, die die Seite selbst abruft (Adresse, Felder, erstes Beispiel)
  - die ersten Zeilen des sichtbaren Textes
  - Links zu Bannern/Packs
Damit lässt sich der Store-Abruf bauen. Es werden keine versteckten Adressen geraten.

Aufruf auf dem VPS:
    docker exec -i gtcha-discord-bot python - < tools/store_probe.py
"""

import asyncio
import json
import random
import re

from playwright.async_api import async_playwright

from config import BASE_URL, SCRAPER_PROXY
from scraper.gtcha_scraper import USER_AGENTS
from utils.pack_list_client import GEO_HEADERS

PATH = "/store"


def short(value, limit=160):
    text = json.dumps(value, ensure_ascii=False) if not isinstance(value, str) else value
    return text if len(text) <= limit else text[:limit] + "…"


async def main():
    seen = []
    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=True, args=["--no-sandbox", "--disable-dev-shm-usage", "--disable-gpu"])
        context = await browser.new_context(user_agent=random.choice(USER_AGENTS), extra_http_headers=GEO_HEADERS,
                                            proxy={"server": SCRAPER_PROXY} if SCRAPER_PROXY else None)
        page = await context.new_page()

        async def on_response(response):
            if "json" in (response.headers.get("content-type") or ""):
                try:
                    seen.append((response.status, response.url, await response.json()))
                except Exception:
                    pass

        page.on("response", on_response)
        await page.goto(f"{BASE_URL}/api/user/point", wait_until="domcontentloaded", timeout=60000)   # Sitzung wie der Bot
        await page.goto(f"{BASE_URL}{PATH}", wait_until="networkidle", timeout=90000)
        await page.wait_for_timeout(3000)
        print(f"== Adresse nach dem Laden: {page.url}")
        print(f"== Titel: {await page.title()}")
        print("\n== JSON-Antworten der Seite:")
        for status, url, data in seen:
            if "/api/user/point" in url:
                continue
            print(f"- {status} {url.replace(BASE_URL, '')}")
            if isinstance(data, dict):
                print("  Felder:", ", ".join(list(data)[:15]))
                items = data.get("list") or data.get("data") or data.get("items")
                if isinstance(items, list) and items:
                    print(f"  {len(items)} Einträge, Felder: {', '.join(list(items[0])[:25])}")
                    print("  Beispiel:", short(items[0], 400))
                else:
                    print("  Beispiel:", short(data, 300))
            elif isinstance(data, list) and data:
                print(f"  {len(data)} Einträge, Beispiel: {short(data[0], 300)}")
        text = await page.inner_text("body")
        print("\n== Sichtbarer Text (Anfang):")
        print("\n".join(line for line in text.splitlines() if line.strip())[:1500])
        links = await page.eval_on_selector_all("a[href]", "els => els.map(e => e.getAttribute('href'))")
        pack_links = sorted({l for l in links if re.search(r"pack|store|gacha|detail", l or "")})[:25]
        print("\n== Links:", ", ".join(pack_links) or "keine")
        classes = await page.evaluate("""() => {
            const c = {};
            document.querySelectorAll('[class]').forEach(e => String(e.className).split(/\\s+/).forEach(k => { if (k) c[k] = (c[k] || 0) + 1; }));
            return Object.entries(c).filter(([k, n]) => n >= 3).sort((a, b) => b[1] - a[1]).slice(0, 25);
        }""")
        print("\n== Häufige CSS-Klassen:", ", ".join(f"{k}×{n}" for k, n in classes))
        await browser.close()


asyncio.run(main())
