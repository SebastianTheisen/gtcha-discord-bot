"""Lädt die Seite in mehreren Browser-Varianten und vergleicht die Pack-Zahl eines Banners.

Aufruf auf dem VPS (im laufenden Container, kein Rebuild nötig):
    docker exec -i gtcha-discord-bot python - 24125 < scripts/diagnose_browser.py
"""

import asyncio
import os
import re
import sys

from playwright.async_api import async_playwright

BASE = "https://gtchaxonline.com"
PROXY = os.getenv("SCRAPER_PROXY") or "socks5://127.0.0.1:40000"
DESKTOP_UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
BOT_GEO_HEADERS = {
    "Accept-Language": "de-DE,de;q=0.9,en;q=0.8",
    "X-Forwarded-For": "217.237.150.100",
    "X-Real-IP": "217.237.150.100",
    "CF-Connecting-IP": "217.237.150.100",
    "X-Country": "DE",
    "X-Country-Code": "DE",
}


async def run(pw, label, proxy, device, headers, pid):
    browser = await pw.chromium.launch(headless=True, args=["--no-sandbox", "--disable-dev-shm-usage"])
    opts = dict(pw.devices["iPhone 13"]) if device == "iphone" else {"user_agent": DESKTOP_UA}
    opts.pop("default_browser_type", None)
    ctx = await browser.new_context(**opts, locale="de-DE", extra_http_headers=headers or {},
                                    proxy={"server": proxy} if proxy else None)
    page = await ctx.new_page()
    api_value = []

    async def on_response(resp):
        if "pack/list" not in resp.url:
            return
        try:
            data = await resp.json()
        except Exception:
            return
        for it in data.get("list", []):
            if str(it.get("id")) == str(pid):
                api_value.append(it.get("pack_count"))

    page.on("response", on_response)
    result = f"{label:<34}"
    try:
        await page.goto(f"{BASE}/pack-detail?packId={pid}", wait_until="domcontentloaded", timeout=90000)
        await asyncio.sleep(5)
        body = " ".join((await page.inner_text("body")).split())
        dom = sorted({m for m in re.findall(r"\d[\d,]*\s*/\s*\d[\d,]*", body) if not m.startswith("31")})
        await page.goto(BASE, wait_until="domcontentloaded", timeout=90000)
        await asyncio.sleep(5)
        result += f" Detailseite: {', '.join(dom) or '-':<14} API-Liste: {api_value[-1] if api_value else '-'}"
    except Exception as e:
        result += f" FEHLER: {e}"
    print(result, flush=True)
    await browser.close()


async def main():
    pid = int(sys.argv[1]) if len(sys.argv) > 1 else 24125
    variants = [
        ("Desktop direkt", None, "desktop", None),
        ("Desktop WARP", PROXY, "desktop", None),
        ("Desktop WARP + Bot-Header", PROXY, "desktop", BOT_GEO_HEADERS),
        ("iPhone direkt", None, "iphone", None),
        ("iPhone WARP", PROXY, "iphone", None),
    ]
    print(f"Banner {pid}")
    async with async_playwright() as pw:
        for label, proxy, device, headers in variants:
            await run(pw, label, proxy, device, headers, pid)


asyncio.run(main())
