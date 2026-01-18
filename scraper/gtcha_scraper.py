"""
GTCHA Webseiten-Scraper - API INTERCEPTION VERSION v2
Fix: Doppeltes // und Unknown-Kategorie
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
        # FIX: Remove trailing slash from base_url
        self.base_url = base_url.rstrip('/')
        self.headless = headless
        self._playwright = None
        self._browser: Optional[Browser] = None
        self._context: Optional[BrowserContext] = None
        self._page: Optional[Page] = None
        self.debug_dir = Path("screenshots/debug")
        self.debug_dir.mkdir(parents=True, exist_ok=True)

        # API data collection
        self._api_responses: List[Dict[str, Any]] = []
        self._captured_banners: Dict[int, Dict] = {}

        # Current category during scraping
        self._current_category: str = "Unknown"

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
            ]
        )

        self._context = await self._browser.new_context(
            viewport={"width": 1920, "height": 1080},
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
            locale="ja-JP",
        )

        self._page = await self._context.new_page()

        # API Response Listener
        self._page.on("response", self._handle_response)

        logger.info("✅ Browser gestartet (API-Interception v2)")

    async def _handle_response(self, response: Response):
        """Intercepts API responses."""
        url = response.url

        # Only relevant API calls
        if not any(x in url.lower() for x in ['/api/', 'oripa', 'pack', 'product', 'item', 'gacha', 'list']):
            return

        content_type = response.headers.get('content-type', '')
        if 'application/json' not in content_type:
            return

        try:
            data = await response.json()
            logger.debug(f"📡 API: {url[:80]}")

            self._api_responses.append({
                'url': url,
                'data': data,
                'category': self._current_category,
                'timestamp': datetime.now().isoformat()
            })

            await self._extract_banners_from_api(data, url)

        except Exception as e:
            logger.debug(f"    Parse error: {e}")

    async def _extract_banners_from_api(self, data: Any, url: str):
        """Extracts banner data from API response."""

        items_to_check = []

        if isinstance(data, list):
            items_to_check = data
        elif isinstance(data, dict):
            # Search for lists
            for key in ['data', 'items', 'products', 'oripas', 'packs', 'list', 'results', 'banners', 'content']:
                if key in data and isinstance(data[key], list):
                    items_to_check = data[key]
                    break

            if not items_to_check and 'id' in data:
                items_to_check = [data]

        for item in items_to_check:
            if not isinstance(item, dict):
                continue

            # Find pack ID
            pack_id = None
            for id_key in ['id', 'packId', 'pack_id', 'productId', 'product_id', 'oripaId', 'oripa_id']:
                if id_key in item:
                    try:
                        pack_id = int(item[id_key])
                        break
                    except:
                        pass

            if not pack_id:
                continue

            # Collect data
            banner_data = {
                'pack_id': pack_id,
                'raw_data': item,
                'category': self._current_category,
            }

            # Price
            for price_key in ['price', 'coin', 'coins', 'cost', 'amount', 'point', 'points']:
                if price_key in item:
                    try:
                        banner_data['price'] = int(item[price_key])
                        break
                    except:
                        pass

            # Stock (Rückstand)
            for stock_key in ['stock', 'remaining', 'quantity', 'count', 'left', 'inventory', 'remain', 'currentStock']:
                if stock_key in item:
                    try:
                        banner_data['current_packs'] = int(item[stock_key])
                        break
                    except:
                        pass

            # Total
            for total_key in ['total', 'totalStock', 'total_stock', 'max', 'maxStock', 'limit', 'initialStock']:
                if total_key in item:
                    try:
                        banner_data['total_packs'] = int(item[total_key])
                        break
                    except:
                        pass

            # Entries per day
            for limit_key in ['dailyLimit', 'daily_limit', 'limitPerDay', 'perDay', 'dayLimit', 'purchaseLimit']:
                if limit_key in item:
                    try:
                        banner_data['entries_per_day'] = int(item[limit_key])
                        break
                    except:
                        pass

            # Title
            for name_key in ['name', 'title', 'productName', 'product_name', 'oripaName', 'packName']:
                if name_key in item and item[name_key]:
                    banner_data['title'] = str(item[name_key])
                    break

            # Image
            for img_key in ['image', 'imageUrl', 'image_url', 'thumbnail', 'thumb', 'banner', 'bannerUrl', 'mainImage']:
                if img_key in item and item[img_key]:
                    img_url = str(item[img_key])
                    if not img_url.startswith('http'):
                        img_url = f"{self.base_url}/{img_url.lstrip('/')}"
                    banner_data['image_url'] = img_url
                    break

            # Category from API data (if available)
            for cat_key in ['category', 'categoryId', 'category_id', 'categoryName', 'type', 'genre']:
                if cat_key in item and item[cat_key]:
                    banner_data['category_from_api'] = item[cat_key]
                    break

            # End date
            for date_key in ['endDate', 'end_date', 'saleEnd', 'sale_end', 'expiry', 'deadline', 'endAt', 'end_at']:
                if date_key in item and item[date_key]:
                    banner_data['sale_end_date'] = str(item[date_key])
                    break

            # Only save if not already present, or update if more data
            if pack_id not in self._captured_banners:
                self._captured_banners[pack_id] = banner_data
                logger.debug(f"   ✅ Banner: ID={pack_id}, Kategorie={self._current_category}")
            else:
                # Update with more data
                existing = self._captured_banners[pack_id]
                for key, value in banner_data.items():
                    if value and not existing.get(key):
                        existing[key] = value

    async def close(self):
        # Save API log
        if self._api_responses:
            try:
                path = self.debug_dir / f"api_log_{datetime.now().strftime('%H%M%S')}.json"
                with open(path, 'w', encoding='utf-8') as f:
                    json.dump(self._api_responses, f, indent=2, ensure_ascii=False, default=str)
                logger.info(f"📝 API-Log: {path}")
            except:
                pass

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
        except:
            pass

    async def scrape_all_banners(self) -> List[ScrapedBanner]:
        # Reset
        self._api_responses = []
        self._captured_banners = {}
        self._current_category = "Bonus"  # Start page usually shows Bonus

        logger.info(f"📄 Lade: {self.base_url}")

        try:
            await self._page.goto(self.base_url, wait_until="domcontentloaded", timeout=60000)

            try:
                await self._page.wait_for_load_state("networkidle", timeout=30000)
            except:
                pass

            logger.info("⏳ Warte auf Vue.js (15 Sekunden)...")
            await asyncio.sleep(15)

            await self._save_debug_screenshot("01_start")

            logger.info(f"📡 {len(self._api_responses)} API-Responses")
            logger.info(f"🎯 {len(self._captured_banners)} Banner bisher")

        except Exception as e:
            logger.error(f"❌ Ladefehler: {e}")

        # Go through all categories - IMPORTANT: Set _current_category BEFORE clicking!
        for category in CATEGORIES:
            try:
                logger.info(f"🔍 Kategorie: {category}")
                self._current_category = category  # IMPORTANT!

                await self._click_category_tab(category)
                await asyncio.sleep(4)  # Wait for API response

                logger.info(f"   → Jetzt {len(self._captured_banners)} Banner")

            except Exception as e:
                logger.debug(f"   Fehler: {e}")

        await self._save_debug_screenshot("02_fertig")

        # Statistics
        logger.info(f"📊 Gesamt API-Responses: {len(self._api_responses)}")
        logger.info(f"🎯 Gesamt Banner via API: {len(self._captured_banners)}")

        # Convert
        all_banners = self._convert_to_scraped_banners()

        logger.info(f"✅ Fertig: {len(all_banners)} Banner")
        return all_banners

    async def _click_category_tab(self, category: str) -> bool:
        """Clicks on category tab."""
        variants = {
            "Pokémon": ["Pokémon", "Pokemon", "ポケモン", "POKEMON"],
            "Yu-Gi-Oh!": ["Yu-Gi-Oh!", "Yu-Gi-Oh", "遊戯王", "YuGiOh"],
            "One piece": ["One piece", "One Piece", "ワンピース", "Onepiece", "ONE PIECE"],
            "Weiss Schwarz": ["Weiss Schwarz", "Weiss", "ヴァイス", "WEISS"],
            "Bonus": ["Bonus", "ボーナス", "BONUS"],
            "MIX": ["MIX", "Mix", "ミックス"],
            "Hobby": ["Hobby", "ホビー", "HOBBY"],
        }

        search_terms = variants.get(category, [category])

        for term in search_terms:
            try:
                loc = self._page.get_by_text(term, exact=True)
                if await loc.count() > 0:
                    await loc.first.click()
                    logger.debug(f"   ✅ Klick: {term}")
                    return True
            except:
                pass

            try:
                await self._page.click(f"text={term}", timeout=2000)
                return True
            except:
                pass

        return False

    def _convert_to_scraped_banners(self) -> List[ScrapedBanner]:
        """Converts to ScrapedBanner objects."""
        banners = []

        for pack_id, data in self._captured_banners.items():
            try:
                # Category: Use stored category, or try to map from API data
                category = data.get('category', 'Unknown')

                # If still Unknown, try to map from category_from_api
                if category == 'Unknown' and 'category_from_api' in data:
                    cat_raw = str(data['category_from_api']).lower()
                    cat_map = {
                        'pokemon': 'Pokémon', 'ポケモン': 'Pokémon', 'poke': 'Pokémon',
                        'yugioh': 'Yu-Gi-Oh!', '遊戯王': 'Yu-Gi-Oh!', 'ygo': 'Yu-Gi-Oh!',
                        'onepiece': 'One piece', 'ワンピース': 'One piece', 'one': 'One piece',
                        'weiss': 'Weiss Schwarz', 'ヴァイス': 'Weiss Schwarz', 'ws': 'Weiss Schwarz',
                        'bonus': 'Bonus', 'ボーナス': 'Bonus',
                        'mix': 'MIX', 'ミックス': 'MIX',
                        'hobby': 'Hobby', 'ホビー': 'Hobby',
                    }
                    for key, val in cat_map.items():
                        if key in cat_raw:
                            category = val
                            break

                # FIX: Ensure URL is correct (no double //)
                detail_url = f"{self.base_url}/pack-detail?packId={pack_id}"

                banner = ScrapedBanner(
                    pack_id=pack_id,
                    category=category,
                    title=data.get('title'),
                    price_coins=data.get('price'),
                    current_packs=data.get('current_packs'),
                    total_packs=data.get('total_packs'),
                    entries_per_day=data.get('entries_per_day'),
                    sale_end_date=data.get('sale_end_date'),
                    image_url=data.get('image_url'),
                    detail_page_url=detail_url,
                )
                banners.append(banner)

            except Exception as e:
                logger.warning(f"Fehler bei {pack_id}: {e}")

        return banners

    async def scrape_banner_details(self, pack_id: int) -> Tuple[Optional[str], Optional[bytes]]:
        """Scrapes banner details."""
        # FIX: Correct URL without double //
        url = f"{self.base_url}/pack-detail?packId={pack_id}"

        try:
            logger.debug(f"   Lade Details: {url}")
            await self._page.goto(url, wait_until="networkidle", timeout=30000)
            await asyncio.sleep(5)

            screenshot = await self._page.screenshot()

            # Search for best hit
            best_hit = None

            # From API data
            if pack_id in self._captured_banners:
                raw = self._captured_banners[pack_id].get('raw_data', {})
                for key in ['topPrize', 'bestHit', 'mainCard', 'featured', 'highlight', 'firstPrize']:
                    if key in raw and raw[key]:
                        best_hit = str(raw[key])
                        break

            # Fallback: DOM
            if not best_hit:
                best_hit = await self._page.evaluate("""
                    () => {
                        const body = document.body.innerText;
                        const m = body.match(/(PSA\\s*10?[^\\n]{0,50})/i);
                        return m ? m[1].trim() : null;
                    }
                """)

            return best_hit, screenshot

        except Exception as e:
            logger.error(f"   Detail-Fehler: {e}")
            return None, None

    async def download_image(self, url: str) -> Optional[bytes]:
        try:
            response = await self._page.request.get(url)
            if response.ok:
                return await response.body()
        except:
            pass
        return None
