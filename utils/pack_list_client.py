"""Schneller Abruf von pack/list über einen dauerhaft offenen Browser (über Tor).

Eine einfache HTTP-Anfrage (curl) bekam von der Seite veraltete Pack-Zahlen, auch mit frischer
Sitzung; der Browser bekommt nachweislich aktuelle. Deshalb macht dieser Abruf dasselbe wie der
normale Scrape (scraper.fetch_pack_list): neuer Browser-Kontext = neue Sitzung, dann pack/list.
Nur der Browser selbst bleibt offen, damit eine Abfrage wenige Sekunden statt ~10 dauert.
"""

import random
import time
from typing import Dict, Optional

from loguru import logger
from playwright.async_api import async_playwright

from scraper.gtcha_scraper import FETCH_PACK_LIST_JS, USER_AGENTS

BROWSER_MAX_AGE = 60 * 60  # Browser stündlich neu starten (Speicher)
GEO_HEADERS = {  # wie beim normalen Scrape
    "Accept-Language": "de-DE,de;q=0.9,en;q=0.8",
    "X-Forwarded-For": "217.237.150.100",
    "X-Real-IP": "217.237.150.100",
    "CF-Connecting-IP": "217.237.150.100",
    "X-Country": "DE",
    "X-Country-Code": "DE",
}


class PackListClient:
    def __init__(self, base_url: str, proxy: Optional[str]):
        self.base_url = base_url.rstrip("/")
        self.proxy = proxy
        self._playwright = None
        self._browser = None
        self._started = 0.0

    async def _ensure_browser(self):
        if self._browser and self._browser.is_connected() and time.time() - self._started < BROWSER_MAX_AGE:
            return
        await self.close()
        self._playwright = await async_playwright().start()
        self._browser = await self._playwright.chromium.launch(
            headless=True,
            args=['--no-sandbox', '--disable-setuid-sandbox', '--disable-dev-shm-usage', '--disable-gpu',
                  '--blink-settings=imagesEnabled=false'],
        )
        self._started = time.time()
        logger.debug("[SCHNELL] Browser gestartet")

    async def close(self):
        try:
            if self._browser:
                await self._browser.close()
            if self._playwright:
                await self._playwright.stop()
        except Exception:
            pass
        self._browser = self._playwright = None

    async def fetch(self) -> Dict[int, dict]:
        """Alle Banner aus pack/list (leer bei Fehler)."""
        await self._ensure_browser()
        context = await self._browser.new_context(
            user_agent=random.choice(USER_AGENTS),
            extra_http_headers=GEO_HEADERS,
            proxy={"server": self.proxy} if self.proxy else None,
        )
        try:
            page = await context.new_page()
            await page.goto(f"{self.base_url}/api/user/point", wait_until="domcontentloaded", timeout=30000)
            data = await page.evaluate(FETCH_PACK_LIST_JS)
            items = (data or {}).get("list") if isinstance(data, dict) else None
            return {int(it["id"]): it for it in items or [] if it.get("id")}
        except Exception:
            # z.B. abgestürzter Browser: beim nächsten Abruf neu starten
            await self.close()
            raise
        finally:
            try:
                await context.close()
            except Exception:
                pass
