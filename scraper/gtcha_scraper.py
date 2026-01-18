"""
GTCHA Webseiten-Scraper - API INTERCEPTION VERSION

Diese Version fängt die API-Requests ab die die Vue.js App macht,
anstatt das gerenderte HTML zu parsen. Das ist viel zuverlässiger!
"""

import asyncio
import json
import re
from pathlib import Path
from typing import List, Optional, Tuple, Dict, Any
from datetime import datetime

from playwright.async_api import async_playwright, Page, Browser, BrowserContext, Response
from loguru import logger

from .models import ScrapedBanner
from config import CATEGORIES


class GTCHAScraper:
    def __init__(self, base_url: str = "https://gtchaxonline.com", headless: bool = True):
        self.base_url = base_url
        self.headless = headless
        self._playwright = None
        self._browser: Optional[Browser] = None
        self._context: Optional[BrowserContext] = None
        self._page: Optional[Page] = None
        self.debug_dir = Path("screenshots/debug")
        self.debug_dir.mkdir(parents=True, exist_ok=True)

        # Store intercepted API data
        self._api_responses: List[Dict[str, Any]] = []
        self._captured_banners: Dict[int, Dict] = {}  # packId -> banner data

    async def __aenter__(self):
        await self.start()
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        await self.close()

    async def start(self):
        logger.info("🌐 Starte Browser...")
        self._playwright = await async_playwright().start()

        self._browser = await self._playwright.chromium.launch(
            headless=self.headless,
            args=[
                '--no-sandbox',
                '--disable-setuid-sandbox',
                '--disable-dev-shm-usage',
                '--disable-gpu',
                '--no-first-run',
                '--disable-background-networking',
                '--disable-default-apps',
                '--disable-extensions',
                '--disable-sync',
                '--disable-translate',
            ]
        )

        self._context = await self._browser.new_context(
            viewport={"width": 1920, "height": 1080},
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
            java_script_enabled=True,
            locale="ja-JP",
        )

        self._page = await self._context.new_page()

        # Intercept network responses
        self._page.on("response", self._handle_response)

        logger.info("✅ Browser gestartet mit API-Interception")

    async def _handle_response(self, response: Response):
        """Intercepts all network responses and looks for API data."""
        url = response.url

        # Log interesting requests
        if any(keyword in url.lower() for keyword in ['api', 'pack', 'oripa', 'gacha', 'product', 'item', 'list']):
            logger.debug(f"📡 Response: {url[:100]}")

        # Try to parse JSON responses
        try:
            content_type = response.headers.get('content-type', '')
            if 'application/json' in content_type or url.endswith('.json'):
                try:
                    data = await response.json()
                    self._process_api_response(url, data)
                except:
                    pass
        except:
            pass

    def _process_api_response(self, url: str, data: Any):
        """Processes API responses and extracts banner data."""
        logger.debug(f"📦 JSON Response von: {url[:80]}")

        # Store all API responses for debug
        self._api_responses.append({
            "url": url,
            "data": data,
            "timestamp": datetime.now().isoformat()
        })

        # Try to extract banner data
        self._extract_banners_from_json(data)

    def _extract_banners_from_json(self, data: Any, path: str = ""):
        """Recursively extract banner data from JSON."""

        if isinstance(data, dict):
            # Check if this is a banner/pack object
            has_pack_indicators = any(key in data for key in [
                'packId', 'pack_id', 'id', 'productId', 'product_id',
                'oripaid', 'oripa_id', 'gachaId', 'gacha_id'
            ])

            has_price = any(key in data for key in [
                'price', 'cost', 'coin', 'coins', 'point', 'points'
            ])

            has_stock = any(key in data for key in [
                'stock', 'remaining', 'quantity', 'count', 'left',
                'Rückstand', 'ruckstand', 'rest', 'available'
            ])

            if has_pack_indicators and (has_price or has_stock):
                pack_id = (
                    data.get('packId') or data.get('pack_id') or
                    data.get('id') or data.get('productId') or
                    data.get('oripaid') or data.get('gacha_id')
                )

                if pack_id and pack_id not in self._captured_banners:
                    logger.info(f"   🎯 Banner gefunden: ID={pack_id}")
                    self._captured_banners[pack_id] = data

            # Recursively process all values
            for key, value in data.items():
                self._extract_banners_from_json(value, f"{path}.{key}")

        elif isinstance(data, list):
            for i, item in enumerate(data):
                self._extract_banners_from_json(item, f"{path}[{i}]")

    async def close(self):
        # Debug: Save all API responses
        if self._api_responses:
            debug_file = self.debug_dir / f"api_responses_{datetime.now().strftime('%H%M%S')}.json"
            try:
                with open(debug_file, 'w', encoding='utf-8') as f:
                    json.dump(self._api_responses, f, indent=2, ensure_ascii=False, default=str)
                logger.info(f"📝 API-Responses gespeichert: {debug_file}")
            except Exception as e:
                logger.warning(f"Konnte API-Responses nicht speichern: {e}")

        if self._context:
            await self._context.close()
        if self._browser:
            await self._browser.close()
        if self._playwright:
            await self._playwright.stop()
        logger.info("🔒 Browser geschlossen")

    async def _save_debug_screenshot(self, name: str):
        try:
            timestamp = datetime.now().strftime("%H%M%S")
            path = self.debug_dir / f"{timestamp}_{name}.png"
            await self._page.screenshot(path=str(path))
            logger.debug(f"📸 Screenshot: {path}")
        except Exception as e:
            logger.warning(f"Screenshot error: {e}")

    async def scrape_all_banners(self) -> List[ScrapedBanner]:
        """Scrapes all banners via API interception."""

        # Reset
        self._api_responses = []
        self._captured_banners = {}

        logger.info(f"📄 Lade Hauptseite: {self.base_url}")

        try:
            # Load page
            response = await self._page.goto(
                self.base_url,
                wait_until="domcontentloaded",
                timeout=60000
            )

            logger.info(f"📡 Status: {response.status if response else 'None'}")

            # Wait for network idle (API requests should happen here)
            try:
                await self._page.wait_for_load_state("networkidle", timeout=30000)
            except:
                logger.warning("Network idle timeout")

            # Wait for JavaScript
            logger.info("⏳ Warte auf Vue.js App (20 Sekunden)...")
            await asyncio.sleep(20)

            # Debug screenshot
            await self._save_debug_screenshot("01_hauptseite")

            # Show what we intercepted
            logger.info(f"📊 Abgefangene API-Responses: {len(self._api_responses)}")
            logger.info(f"🎯 Gefundene Banner via API: {len(self._captured_banners)}")

        except Exception as e:
            logger.error(f"❌ Ladefehler: {e}")
            await self._save_debug_screenshot("error_load")

        # Click through categories to trigger more API requests
        for category in CATEGORIES:
            try:
                logger.info(f"🔍 Kategorie: {category}")
                await self._click_category_tab(category)
                await asyncio.sleep(5)  # Wait for API response

                logger.debug(f"   Bisher {len(self._captured_banners)} Banner via API")

            except Exception as e:
                logger.error(f"❌ Fehler bei {category}: {e}")

        # Final statistics
        logger.info(f"📊 Gesamt API-Responses: {len(self._api_responses)}")
        logger.info(f"🎯 Gesamt Banner via API: {len(self._captured_banners)}")

        # If no banners via API, try DOM fallback
        if not self._captured_banners:
            logger.warning("⚠️ Keine Banner via API - versuche DOM-Extraktion...")
            await self._fallback_dom_extraction()

        # Convert to ScrapedBanner objects
        banners = self._convert_to_scraped_banners()

        logger.info(f"✅ Fertig: {len(banners)} Banner")
        return banners

    async def _click_category_tab(self, category: str) -> bool:
        """Clicks on a category tab."""

        variants = {
            "Pokémon": ["Pokémon", "Pokemon", "POKEMON"],
            "Yu-Gi-Oh!": ["Yu-Gi-Oh!", "Yu-Gi-Oh", "YuGiOh"],
            "One piece": ["One piece", "One Piece", "ONE PIECE"],
            "Weiss Schwarz": ["Weiss Schwarz", "Weiss", "WEISS SCHWARZ"],
            "Bonus": ["Bonus", "BONUS"],
            "MIX": ["MIX", "Mix"],
            "Hobby": ["Hobby", "HOBBY"],
        }

        search_terms = variants.get(category, [category])

        for term in search_terms:
            try:
                locator = self._page.get_by_text(term, exact=True)
                if await locator.count() > 0:
                    await locator.first.click()
                    logger.debug(f"   ✅ Klick: {term}")
                    return True
            except:
                pass

            try:
                await self._page.click(f"text={term}", timeout=2000)
                logger.debug(f"   ✅ Klick (text=): {term}")
                return True
            except:
                pass

        return False

    async def _fallback_dom_extraction(self):
        """Fallback: Extract banners from DOM."""
        logger.info("🔄 DOM-Fallback Extraktion...")

        # Save complete HTML for debug
        html = await self._page.content()
        html_file = self.debug_dir / f"page_html_{datetime.now().strftime('%H%M%S')}.html"
        try:
            with open(html_file, 'w', encoding='utf-8') as f:
                f.write(html)
            logger.info(f"📝 HTML gespeichert: {html_file}")
        except:
            pass

        # Try to find packId links
        banner_data = await self._page.evaluate("""
            () => {
                const results = [];
                const seen = new Set();

                // Method 1: Links with packId
                document.querySelectorAll('a[href*="packId"], a[href*="pack-detail"]').forEach(link => {
                    const match = link.href.match(/packId[=:]?(\\d+)/i);
                    if (match && !seen.has(match[1])) {
                        seen.add(match[1]);
                        results.push({
                            packId: match[1],
                            href: link.href,
                            text: link.innerText.substring(0, 200)
                        });
                    }
                });

                // Method 2: Elements with onclick
                document.querySelectorAll('[onclick]').forEach(el => {
                    const onclick = el.getAttribute('onclick') || '';
                    const match = onclick.match(/packId[=:]?(\\d+)/i) || onclick.match(/(\\d{4,6})/);
                    if (match && !seen.has(match[1])) {
                        seen.add(match[1]);
                        results.push({
                            packId: match[1],
                            onclick: onclick.substring(0, 100),
                            text: el.innerText.substring(0, 200)
                        });
                    }
                });

                // Method 3: Data attributes
                document.querySelectorAll('[data-pack-id], [data-id], [data-product-id]').forEach(el => {
                    const packId = el.getAttribute('data-pack-id') ||
                                   el.getAttribute('data-id') ||
                                   el.getAttribute('data-product-id');
                    if (packId && !seen.has(packId)) {
                        seen.add(packId);
                        results.push({
                            packId: packId,
                            text: el.innerText.substring(0, 200)
                        });
                    }
                });

                return results;
            }
        """)

        logger.info(f"   DOM-Extraktion: {len(banner_data)} Elemente")

        for data in banner_data:
            pack_id = data.get('packId')
            if pack_id and pack_id not in self._captured_banners:
                self._captured_banners[pack_id] = {
                    'id': pack_id,
                    'source': 'dom_fallback',
                    'raw': data
                }

    def _convert_to_scraped_banners(self) -> List[ScrapedBanner]:
        """Converts collected data to ScrapedBanner objects."""
        banners = []

        for pack_id, data in self._captured_banners.items():
            try:
                # Extract fields with various possible names
                price = (
                    data.get('price') or data.get('cost') or
                    data.get('coin') or data.get('coins') or
                    data.get('point') or data.get('points')
                )

                current_packs = (
                    data.get('remaining') or data.get('stock') or
                    data.get('quantity') or data.get('left') or
                    data.get('available') or data.get('rest')
                )

                total_packs = (
                    data.get('total') or data.get('totalStock') or
                    data.get('total_stock') or data.get('max') or
                    data.get('initialStock')
                )

                entries = (
                    data.get('limit') or data.get('dailyLimit') or
                    data.get('daily_limit') or data.get('perDay') or
                    data.get('maxPerDay')
                )

                title = (
                    data.get('name') or data.get('title') or
                    data.get('productName') or data.get('packName')
                )

                image = (
                    data.get('image') or data.get('imageUrl') or
                    data.get('thumbnail') or data.get('img')
                )

                category = (
                    data.get('category') or data.get('categoryName') or
                    data.get('type') or "Unknown"
                )

                banner = ScrapedBanner(
                    pack_id=int(pack_id),
                    category=str(category),
                    title=str(title) if title else None,
                    price_coins=int(price) if price else None,
                    current_packs=int(current_packs) if current_packs else None,
                    total_packs=int(total_packs) if total_packs else None,
                    entries_per_day=int(entries) if entries else None,
                    image_url=str(image) if image else None,
                    detail_page_url=f"{self.base_url}/pack-detail?packId={pack_id}",
                )

                banners.append(banner)
                logger.debug(f"   ✅ Banner: {banner}")

            except Exception as e:
                logger.warning(f"   Konvertierung fehlgeschlagen für {pack_id}: {e}")

        return banners

    async def scrape_banner_details(self, pack_id: int) -> Tuple[Optional[str], Optional[bytes]]:
        """Scrapes banner details."""
        url = f"{self.base_url}/pack-detail?packId={pack_id}"

        try:
            await self._page.goto(url, wait_until="networkidle", timeout=30000)
            await asyncio.sleep(5)

            screenshot = await self._page.screenshot()

            # Best hit from API data or DOM
            best_hit = None

            # Try from intercepted data
            if pack_id in self._captured_banners:
                data = self._captured_banners[pack_id]
                best_hit = data.get('bestHit') or data.get('topPrize') or data.get('firstPrize')

            # Fallback: DOM
            if not best_hit:
                best_hit = await self._page.evaluate("""
                    () => {
                        const selectors = ['[class*="hit"]', '[class*="prize"]', '[class*="card"]'];
                        for (const sel of selectors) {
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
            logger.error(f"Detail-Fehler: {e}")
            return None, None

    async def download_image(self, url: str) -> Optional[bytes]:
        try:
            response = await self._page.request.get(url)
            return await response.body() if response.ok else None
        except:
            return None
