"""
GTCHA Webseiten-Scraper - VERSION 3
Mit Fix für JavaScript-Ausführung
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

        # Special browser arguments for container environment
        self._browser = await self._playwright.chromium.launch(
            headless=self.headless,
            args=[
                '--no-sandbox',
                '--disable-setuid-sandbox',
                '--disable-dev-shm-usage',
                '--disable-accelerated-2d-canvas',
                '--no-first-run',
                '--no-zygote',
                '--single-process',
                '--disable-gpu',
                '--disable-background-networking',
                '--disable-default-apps',
                '--disable-extensions',
                '--disable-sync',
                '--disable-translate',
                '--hide-scrollbars',
                '--metrics-recording-only',
                '--mute-audio',
                '--safebrowsing-disable-auto-update',
                '--ignore-certificate-errors',
                '--ignore-ssl-errors',
                '--ignore-certificate-errors-spki-list',
            ]
        )

        # Browser context with JavaScript enabled
        context = await self._browser.new_context(
            viewport={"width": 1920, "height": 1080},
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
            java_script_enabled=True,
            bypass_csp=True,
            ignore_https_errors=True,
        )

        self._page = await context.new_page()

        # Log JavaScript errors
        self._page.on("pageerror", lambda err: logger.warning(f"JS Error: {err}"))
        self._page.on("console", lambda msg: logger.debug(f"Console: {msg.text}") if msg.type == "error" else None)

        logger.info("✅ Browser gestartet")

    async def close(self):
        if self._browser:
            await self._browser.close()
        if self._playwright:
            await self._playwright.stop()
        logger.info("🔒 Browser geschlossen")

    async def _save_debug_screenshot(self, name: str):
        """Speichert einen Debug-Screenshot."""
        try:
            timestamp = datetime.now().strftime("%H%M%S")
            path = self.debug_dir / f"{timestamp}_{name}.png"
            await self._page.screenshot(path=str(path))
            logger.debug(f"📸 Screenshot: {path}")
        except Exception as e:
            logger.warning(f"Screenshot fehlgeschlagen: {e}")

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

            logger.info(f"📡 Response Status: {response.status if response else 'None'}")

            # Wait for networkidle
            await self._page.wait_for_load_state("networkidle", timeout=30000)
            logger.info("✅ Network idle erreicht")

            # Extra wait time for SPA/JavaScript
            logger.info("⏳ Warte auf JavaScript-Ausführung (10 Sekunden)...")
            await asyncio.sleep(10)

            # Debug screenshot
            await self._save_debug_screenshot("01_nach_warten")

            # Check page content
            content = await self._page.content()
            body_text = await self._page.inner_text("body")

            logger.info(f"📝 Seiten-Länge: {len(content)} Zeichen")
            logger.info(f"📝 Body-Text Länge: {len(body_text)} Zeichen")
            logger.debug(f"📝 Body-Text Anfang: {body_text[:500]}")

            # Check for JS warning
            if "JavaScript" in body_text and "enable" in body_text.lower():
                logger.error("❌ JavaScript nicht geladen! Seite zeigt JS-Warnung")
                logger.error(f"   Body: {body_text[:200]}")

                # Retry with more wait time
                logger.info("🔄 Versuche erneut mit mehr Wartezeit...")
                await asyncio.sleep(15)

                body_text = await self._page.inner_text("body")
                if "JavaScript" in body_text and "enable" in body_text.lower():
                    logger.error("❌ JavaScript immer noch nicht geladen")
                    await self._save_debug_screenshot("error_no_js")
                    return []

            # Check for banner content
            if "Rückstand" in body_text or "kaufen" in body_text:
                logger.info("✅ Banner-Content gefunden!")
            else:
                logger.warning("⚠️ Kein Banner-Content gefunden")
                logger.debug(f"Body: {body_text[:1000]}")

        except Exception as e:
            logger.error(f"❌ Fehler beim Laden: {e}")
            await self._save_debug_screenshot("error_load")
            return []

        # Scrape categories
        for category in CATEGORIES:
            try:
                logger.info(f"🔍 Scrape Kategorie: {category}")
                banners = await self._scrape_category(category)
                all_banners.extend(banners)
                logger.info(f"   → {len(banners)} Banner gefunden")
            except Exception as e:
                logger.error(f"❌ Fehler bei {category}: {e}")

        logger.info(f"✅ Scraping fertig: {len(all_banners)} Banner total")
        return all_banners

    async def _scrape_category(self, category: str) -> List[ScrapedBanner]:
        # Click tab
        clicked = await self._click_category_tab(category)

        # Wait
        await asyncio.sleep(3)

        # Scroll for lazy loading
        await self._scroll_to_load_all()

        # Screenshot
        await self._save_debug_screenshot(f"kat_{category.replace(' ', '_').replace('!', '')}")

        # Extract banners
        return await self._extract_banners_from_page(category)

    async def _click_category_tab(self, category: str) -> bool:
        """Klickt auf Kategorie-Tab mit verschiedenen Methoden."""

        variants = [category]
        if category == "Pokémon":
            variants = ["Pokémon", "Pokemon", "POKEMON"]
        elif category == "Yu-Gi-Oh!":
            variants = ["Yu-Gi-Oh!", "Yu-Gi-Oh", "YuGiOh", "Yugioh"]
        elif category == "One piece":
            variants = ["One piece", "One Piece", "ONE PIECE"]
        elif category == "Weiss Schwarz":
            variants = ["Weiss Schwarz", "Weiss", "WEISS SCHWARZ"]

        for variant in variants:
            # Method 1: getByText
            try:
                locator = self._page.get_by_text(variant, exact=True)
                if await locator.count() > 0:
                    await locator.first.click()
                    logger.debug(f"   ✅ Tab geklickt (getByText): {variant}")
                    return True
            except:
                pass

            # Method 2: getByRole
            try:
                locator = self._page.get_by_role("tab", name=variant)
                if await locator.count() > 0:
                    await locator.first.click()
                    logger.debug(f"   ✅ Tab geklickt (getByRole): {variant}")
                    return True
            except:
                pass

            # Method 3: CSS text selector
            try:
                await self._page.click(f"text={variant}", timeout=2000)
                logger.debug(f"   ✅ Tab geklickt (text=): {variant}")
                return True
            except:
                pass

            # Method 4: JavaScript
            try:
                clicked = await self._page.evaluate(f"""
                    () => {{
                        const els = [...document.querySelectorAll('*')];
                        for (const el of els) {{
                            const text = el.innerText || '';
                            if (text.trim() === '{variant}' ||
                                (text.includes('{variant}') && text.length < 30)) {{
                                el.click();
                                return true;
                            }}
                        }}
                        return false;
                    }}
                """)
                if clicked:
                    logger.debug(f"   ✅ Tab geklickt (JS): {variant}")
                    return True
            except:
                pass

        return False

    async def _scroll_to_load_all(self):
        """Scrollt für lazy loading."""
        try:
            for _ in range(6):
                await self._page.evaluate("window.scrollBy(0, 400)")
                await asyncio.sleep(0.5)
            await self._page.evaluate("window.scrollTo(0, 0)")
            await asyncio.sleep(1)
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

                    // Find container
                    let container = link;
                    for (let i = 0; i < 8; i++) {
                        if (container.parentElement) {
                            container = container.parentElement;
                            if ((container.innerText || '').includes('Rückstand')) break;
                        }
                    }

                    const text = container.innerText || '';
                    const img = container.querySelector('img');

                    // Parse data
                    const packs = text.match(/Rückstand\\s*(\\d+)\\s*\\/\\s*(\\d+)/i);
                    const entries = text.match(/(\\d+)\\s*(?:Mal\\s*)?pro\\s*Tag/i);
                    const date = text.match(/Verkauf bis\\s*([\\d\\/]+)/i);

                    // Price
                    let price = null;
                    const priceEl = container.querySelector('[class*="price"], [class*="coin"]');
                    if (priceEl) {
                        const m = priceEl.innerText.match(/(\\d[\\d.,]*)/);
                        if (m) price = m[1].replace(/[.,]/g, '');
                    }
                    if (!price) {
                        const m = text.match(/(\\d{2,5})(?:\\s*(?:kaufen|$))/m);
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

        logger.debug(f"   Raw data: {len(banner_data)} Banner")

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
                logger.warning(f"   Parse error: {e}")

        return banners

    async def scrape_banner_details(self, pack_id: int) -> Tuple[Optional[str], Optional[bytes]]:
        """Scrapet Details einer Banner-Seite."""
        url = f"{self.base_url}/pack-detail?packId={pack_id}"

        try:
            await self._page.goto(url, wait_until="networkidle", timeout=30000)
            await asyncio.sleep(5)

            screenshot = await self._page.screenshot()

            best_hit = await self._page.evaluate("""
                () => {
                    const sels = ['[class*="hit"]', '[class*="prize"]', '[class*="item"]'];
                    for (const sel of sels) {
                        const el = document.querySelector(sel);
                        if (el && el.innerText) {
                            return el.innerText.split('\\n')[0].substring(0, 100);
                        }
                    }
                    const m = document.body.innerText.match(/(PSA\\s*\\d+[^\\n]{0,50})/i);
                    return m ? m[1] : null;
                }
            """)

            return best_hit, screenshot
        except Exception as e:
            logger.error(f"Detail error: {e}")
            return None, None

    async def download_image(self, url: str) -> Optional[bytes]:
        try:
            resp = await self._page.request.get(url)
            return await resp.body() if resp.ok else None
        except:
            return None
