"""
GTCHA Webseiten-Scraper - FINALE VERSION
Mit korrekten Browser-Argumenten für Container/Railway
"""

import asyncio
from pathlib import Path
from typing import List, Optional, Tuple
from datetime import datetime

from playwright.async_api import async_playwright, Page, Browser
from loguru import logger

from .models import ScrapedBanner
from config import CATEGORIES


class GTCHAScraper:
    def __init__(self, base_url: str = "https://gtchaxonline.com", headless: bool = True):
        self.base_url = base_url
        self.headless = headless
        self._playwright = None
        self._browser: Optional[Browser] = None
        self._page: Optional[Page] = None
        self.debug_dir = Path("screenshots/debug")
        self.debug_dir.mkdir(parents=True, exist_ok=True)

    async def __aenter__(self):
        await self.start()
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        await self.close()

    async def start(self):
        logger.info("🌐 Starte Browser...")
        self._playwright = await async_playwright().start()

        # These arguments are required for container environments
        # Based on official Playwright documentation 2025
        self._browser = await self._playwright.chromium.launch(
            headless=self.headless,
            args=[
                '--no-sandbox',
                '--disable-setuid-sandbox',
                '--disable-dev-shm-usage',
                '--disable-gpu',
                '--disable-software-rasterizer',
                '--disable-background-networking',
                '--disable-default-apps',
                '--disable-extensions',
                '--disable-sync',
                '--disable-translate',
                '--metrics-recording-only',
                '--mute-audio',
                '--no-first-run',
                '--safebrowsing-disable-auto-update',
            ]
        )

        # Browser context with all necessary settings
        context = await self._browser.new_context(
            viewport={"width": 1920, "height": 1080},
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/121.0.0.0 Safari/537.36",
            java_script_enabled=True,
            bypass_csp=True,
            ignore_https_errors=True,
        )

        self._page = await context.new_page()

        # Error logging
        self._page.on("pageerror", lambda e: logger.debug(f"Page Error: {e}"))

        logger.info("✅ Browser gestartet")

    async def close(self):
        if self._browser:
            await self._browser.close()
        if self._playwright:
            await self._playwright.stop()
        logger.info("🔒 Browser geschlossen")

    async def _save_debug_screenshot(self, name: str):
        """Speichert Debug-Screenshot."""
        try:
            timestamp = datetime.now().strftime("%H%M%S")
            path = self.debug_dir / f"{timestamp}_{name}.png"
            await self._page.screenshot(path=str(path))
            logger.debug(f"📸 Screenshot: {path}")
        except Exception as e:
            logger.debug(f"Screenshot fehlgeschlagen: {e}")

    async def scrape_all_banners(self) -> List[ScrapedBanner]:
        all_banners = []

        logger.info(f"📄 Lade Hauptseite: {self.base_url}")

        try:
            # Load page
            response = await self._page.goto(
                self.base_url,
                wait_until="domcontentloaded",
                timeout=60000
            )

            if response:
                logger.info(f"📡 Status: {response.status}")

            # Wait for network idle
            try:
                await self._page.wait_for_load_state("networkidle", timeout=30000)
            except:
                logger.warning("Network idle timeout - fahre trotzdem fort")

            # Extra wait time for Vue.js/SPA
            logger.info("⏳ Warte 8 Sekunden auf JavaScript...")
            await asyncio.sleep(8)

            # Debug screenshot
            await self._save_debug_screenshot("01_hauptseite")

            # Check if JS loaded
            body_text = await self._page.inner_text("body")

            if "JavaScript" in body_text and "enable" in body_text.lower():
                logger.error("❌ JavaScript nicht ausgeführt!")
                logger.info("🔄 Versuche nochmal mit mehr Wartezeit...")
                await asyncio.sleep(10)
                body_text = await self._page.inner_text("body")

                if "JavaScript" in body_text and "enable" in body_text.lower():
                    logger.error("❌ JavaScript immer noch nicht geladen!")
                    await self._save_debug_screenshot("error_no_js")
                    return []

            # Check for banner content
            if "Rückstand" in body_text or "kaufen" in body_text or "pro Tag" in body_text:
                logger.info("✅ Banner-Content erkannt!")
            else:
                logger.warning("⚠️ Kein typischer Banner-Content gefunden")
                logger.debug(f"Body (erste 500 Zeichen): {body_text[:500]}")

        except Exception as e:
            logger.error(f"❌ Ladefehler: {e}")
            await self._save_debug_screenshot("error_load")
            return []

        # Process all categories
        for category in CATEGORIES:
            try:
                logger.info(f"🔍 Kategorie: {category}")
                banners = await self._scrape_category(category)
                all_banners.extend(banners)
                logger.info(f"   → {len(banners)} Banner")
            except Exception as e:
                logger.error(f"   ❌ Fehler: {e}")

        logger.info(f"✅ Fertig: {len(all_banners)} Banner total")
        return all_banners

    async def _scrape_category(self, category: str) -> List[ScrapedBanner]:
        # Click tab
        await self._click_category_tab(category)
        await asyncio.sleep(2)

        # Scroll for lazy loading
        await self._scroll_to_load_all()

        # Debug screenshot
        safe_name = category.replace(" ", "_").replace("!", "").replace("-", "")
        await self._save_debug_screenshot(f"kat_{safe_name}")

        # Extract banners
        return await self._extract_banners_from_page(category)

    async def _click_category_tab(self, category: str) -> bool:
        """Klickt auf Kategorie-Tab."""

        # Different spellings
        variants = [category]
        if "Pokémon" in category:
            variants = ["Pokémon", "Pokemon", "POKEMON", "pokemon"]
        elif "Yu-Gi-Oh" in category:
            variants = ["Yu-Gi-Oh!", "Yu-Gi-Oh", "YuGiOh", "Yugioh", "yu-gi-oh"]
        elif "One" in category.lower():
            variants = ["One piece", "One Piece", "ONE PIECE", "Onepiece"]
        elif "Weiss" in category:
            variants = ["Weiss Schwarz", "Weiss", "WEISS SCHWARZ", "weiss schwarz"]
        elif "Bonus" in category:
            variants = ["Bonus", "BONUS", "bonus"]
        elif "MIX" in category:
            variants = ["MIX", "Mix", "mix"]
        elif "Hobby" in category:
            variants = ["Hobby", "HOBBY", "hobby"]

        for variant in variants:
            try:
                # Method 1: Playwright locator
                loc = self._page.get_by_text(variant, exact=True)
                if await loc.count() > 0:
                    await loc.first.click()
                    logger.debug(f"   ✅ Klick (locator): {variant}")
                    return True
            except:
                pass

            try:
                # Method 2: CSS text selector
                await self._page.click(f"text={variant}", timeout=2000)
                logger.debug(f"   ✅ Klick (text=): {variant}")
                return True
            except:
                pass

            try:
                # Method 3: JavaScript
                clicked = await self._page.evaluate(f"""
                    () => {{
                        const all = document.querySelectorAll('*');
                        for (const el of all) {{
                            const t = (el.innerText || '').trim();
                            if (t === '{variant}' || (t.includes('{variant}') && t.length < 30)) {{
                                el.click();
                                return true;
                            }}
                        }}
                        return false;
                    }}
                """)
                if clicked:
                    logger.debug(f"   ✅ Klick (JS): {variant}")
                    return True
            except:
                pass

        logger.debug(f"   ⚠️ Tab nicht gefunden: {category}")
        return False

    async def _scroll_to_load_all(self):
        """Scrollt für Lazy Loading."""
        try:
            for _ in range(5):
                await self._page.evaluate("window.scrollBy(0, 500)")
                await asyncio.sleep(0.4)
            await self._page.evaluate("window.scrollTo(0, 0)")
            await asyncio.sleep(0.5)
        except:
            pass

    async def _extract_banners_from_page(self, category: str) -> List[ScrapedBanner]:
        """Extrahiert Banner von der Seite."""
        banners = []

        banner_data = await self._page.evaluate("""
            () => {
                const banners = [];
                const seen = new Set();

                // Find all links with packId
                document.querySelectorAll('a[href*="packId"]').forEach(link => {
                    const match = link.href.match(/packId=(\\d+)/);
                    if (!match || seen.has(match[1])) return;
                    seen.add(match[1]);

                    // Find container element
                    let container = link;
                    for (let i = 0; i < 10; i++) {
                        if (!container.parentElement) break;
                        container = container.parentElement;
                        const text = container.innerText || '';
                        if (text.includes('Rückstand') || text.includes('kaufen')) break;
                    }

                    const text = container.innerText || '';
                    const img = container.querySelector('img');

                    // Extract data
                    const packs = text.match(/Rückstand\\s*(\\d+)\\s*\\/\\s*(\\d+)/i);
                    const entries = text.match(/(\\d+)\\s*(?:Mal\\s*)?pro\\s*Tag/i);
                    const date = text.match(/Verkauf bis\\s*([\\d\\/]+(?:\\s*JST)?)/i);

                    // Find price
                    let price = null;
                    const priceEl = container.querySelector('[class*="price"], [class*="coin"]');
                    if (priceEl) {
                        const m = priceEl.innerText.match(/(\\d[\\d.,]*)/);
                        if (m) price = m[1].replace(/[.,]/g, '');
                    }
                    if (!price) {
                        const m = text.match(/(\\d{2,5})(?=\\s*$|\\s*kaufen)/m);
                        if (m) price = m[1];
                    }

                    banners.push({
                        packId: match[1],
                        price,
                        currentPacks: packs ? packs[1] : null,
                        totalPacks: packs ? packs[2] : null,
                        entriesPerDay: entries ? entries[1] : null,
                        saleEndDate: date ? date[1] : null,
                        imageUrl: img ? img.src : null
                    });
                });

                return banners;
            }
        """)

        logger.debug(f"   Rohdaten: {len(banner_data)} Banner")

        for data in banner_data:
            try:
                pack_id = int(data.get("packId", 0))
                if pack_id == 0:
                    continue

                banners.append(ScrapedBanner(
                    pack_id=pack_id,
                    category=category,
                    price_coins=int(data["price"]) if data.get("price") else None,
                    current_packs=int(data["currentPacks"]) if data.get("currentPacks") else None,
                    total_packs=int(data["totalPacks"]) if data.get("totalPacks") else None,
                    entries_per_day=int(data["entriesPerDay"]) if data.get("entriesPerDay") else None,
                    sale_end_date=data.get("saleEndDate"),
                    image_url=data.get("imageUrl"),
                    detail_page_url=f"{self.base_url}/pack-detail?packId={pack_id}",
                ))
            except Exception as e:
                logger.debug(f"   Parse-Fehler: {e}")

        return banners

    async def scrape_banner_details(self, pack_id: int) -> Tuple[Optional[str], Optional[bytes]]:
        """Scrapet Banner-Details."""
        url = f"{self.base_url}/pack-detail?packId={pack_id}"

        try:
            await self._page.goto(url, wait_until="networkidle", timeout=30000)
            await asyncio.sleep(4)

            screenshot = await self._page.screenshot()

            best_hit = await self._page.evaluate("""
                () => {
                    const sels = ['[class*="hit"]', '[class*="prize"]', '[class*="card"]', '[class*="item"]'];
                    for (const sel of sels) {
                        const el = document.querySelector(sel);
                        if (el && el.innerText) {
                            const text = el.innerText.split('\\n')[0].trim();
                            if (text.length > 2 && text.length < 100) return text;
                        }
                    }
                    const m = document.body.innerText.match(/(PSA\\s*\\d+[^\\n]{0,50})/i);
                    return m ? m[1].trim() : null;
                }
            """)

            return best_hit, screenshot
        except Exception as e:
            logger.debug(f"Detail-Fehler: {e}")
            return None, None

    async def download_image(self, url: str) -> Optional[bytes]:
        try:
            resp = await self._page.request.get(url)
            return await resp.body() if resp.ok else None
        except:
            return None
