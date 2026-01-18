"""
GTCHA Webseiten-Scraper - OPTIMIERT v3

- Keine Detail-Seiten mehr laden (viel schneller!)
- Filtert zukünftige/inaktive Banner
- Zeitzone JST
"""

import asyncio
import json
import re
from pathlib import Path
from typing import List, Optional, Tuple, Dict, Any
from datetime import datetime, timezone, timedelta

from playwright.async_api import async_playwright, Page, Browser, BrowserContext, Response
from loguru import logger

from .models import ScrapedBanner
from config import CATEGORIES

# Japan Standard Time (UTC+9)
JST = timezone(timedelta(hours=9))


class GTCHAScraper:
    def __init__(self, base_url: str = "https://gtchaxonline.com", headless: bool = True):
        self.base_url = base_url.rstrip('/')
        self.headless = headless
        self._playwright = None
        self._browser: Optional[Browser] = None
        self._context: Optional[BrowserContext] = None
        self._page: Optional[Page] = None
        self.debug_dir = Path("screenshots/debug")
        self.debug_dir.mkdir(parents=True, exist_ok=True)

        self._api_responses: List[Dict[str, Any]] = []
        self._captured_banners: Dict[int, Dict] = {}
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
            args=['--no-sandbox', '--disable-setuid-sandbox', '--disable-dev-shm-usage', '--disable-gpu']
        )

        self._context = await self._browser.new_context(
            viewport={"width": 1920, "height": 1080},
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
            locale="ja-JP",
        )

        self._page = await self._context.new_page()
        self._page.on("response", self._handle_response)

        logger.info("✅ Browser gestartet (Optimiert v3 - keine Detail-Seiten)")

    async def _handle_response(self, response: Response):
        """Intercepts API responses."""
        url = response.url

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
            })

            await self._extract_banners_from_api(data)

        except Exception as e:
            logger.debug(f"    Parse error: {e}")

    def _is_banner_active(self, item: Dict) -> bool:
        """Checks if banner is currently active (not future, not expired)."""
        now_jst = datetime.now(JST)

        # Check status
        for key in ['status', 'state', 'isActive', 'is_active', 'active']:
            if key in item:
                val = item[key]
                if val in [False, 0, 'inactive', 'disabled', 'upcoming', 'scheduled', 'pending', 'hidden']:
                    return False

        # Check start date (not started yet?)
        for key in ['startDate', 'start_date', 'startAt', 'start_at', 'saleStart', 'openDate']:
            if key in item and item[key]:
                try:
                    start_str = str(item[key])
                    start_date = self._parse_date(start_str)
                    if start_date and start_date > now_jst:
                        logger.debug(f"      Skipped: starts {start_date.strftime('%Y-%m-%d %H:%M')}")
                        return False
                except:
                    pass

        # Check end date (already expired?)
        for key in ['endDate', 'end_date', 'endAt', 'end_at', 'saleEnd', 'expiry']:
            if key in item and item[key]:
                try:
                    end_str = str(item[key])
                    end_date = self._parse_date(end_str)
                    if end_date and end_date < now_jst:
                        logger.debug(f"      Skipped: expired {end_date.strftime('%Y-%m-%d %H:%M')}")
                        return False
                except:
                    pass

        # Check stock (sold out?)
        for key in ['stock', 'remaining', 'quantity', 'left']:
            if key in item:
                try:
                    if int(item[key]) <= 0:
                        return False
                except:
                    pass

        return True

    def _parse_date(self, date_str: str) -> Optional[datetime]:
        """Parses various date formats."""
        try:
            # ISO: 2026-01-18T10:00:00
            if 'T' in date_str:
                dt = datetime.fromisoformat(date_str.replace('Z', '+00:00'))
                if dt.tzinfo is None:
                    dt = dt.replace(tzinfo=JST)
                return dt

            # Format: 2026/01/18 10:00
            if '/' in date_str:
                parts = date_str.replace(' JST', '').strip()
                if ' ' in parts:
                    dt = datetime.strptime(parts, '%Y/%m/%d %H:%M')
                else:
                    dt = datetime.strptime(parts, '%Y/%m/%d')
                return dt.replace(tzinfo=JST)

            # Unix Timestamp
            if date_str.isdigit():
                ts = int(date_str)
                if ts > 1e12:  # Milliseconds
                    ts = ts / 1000
                return datetime.fromtimestamp(ts, tz=JST)
        except:
            pass
        return None

    async def _extract_banners_from_api(self, data: Any):
        """Extracts only ACTIVE banners from API."""

        items = []
        if isinstance(data, list):
            items = data
        elif isinstance(data, dict):
            for key in ['data', 'items', 'products', 'oripas', 'packs', 'list', 'results', 'banners']:
                if key in data and isinstance(data[key], list):
                    items = data[key]
                    break
            if not items and 'id' in data:
                items = [data]

        for item in items:
            if not isinstance(item, dict):
                continue

            # Find pack ID
            pack_id = None
            for key in ['id', 'packId', 'pack_id', 'productId', 'oripaId']:
                if key in item:
                    try:
                        pack_id = int(item[key])
                        break
                    except:
                        pass

            if not pack_id or pack_id in self._captured_banners:
                continue

            # Only active banners!
            if not self._is_banner_active(item):
                continue

            # Extract data
            banner = {
                'pack_id': pack_id,
                'category': self._current_category,
                'raw_data': item,
            }

            # Price
            for key in ['price', 'coin', 'coins', 'cost', 'point', 'points']:
                if key in item:
                    try:
                        banner['price'] = int(item[key])
                        break
                    except:
                        pass

            # Stock
            for key in ['stock', 'remaining', 'quantity', 'left', 'currentStock']:
                if key in item:
                    try:
                        banner['current_packs'] = int(item[key])
                        break
                    except:
                        pass

            # Total
            for key in ['total', 'totalStock', 'max', 'initialStock']:
                if key in item:
                    try:
                        banner['total_packs'] = int(item[key])
                        break
                    except:
                        pass

            # Limit per day
            for key in ['dailyLimit', 'limitPerDay', 'perDay', 'purchaseLimit']:
                if key in item:
                    try:
                        banner['entries_per_day'] = int(item[key])
                        break
                    except:
                        pass

            # Title
            for key in ['name', 'title', 'productName', 'oripaName']:
                if key in item and item[key]:
                    banner['title'] = str(item[key])
                    break

            # Image
            for key in ['image', 'imageUrl', 'thumbnail', 'banner', 'mainImage']:
                if key in item and item[key]:
                    img = str(item[key])
                    if not img.startswith('http'):
                        img = f"{self.base_url}/{img.lstrip('/')}"
                    banner['image_url'] = img
                    break

            # Best Hit
            for key in ['topPrize', 'bestHit', 'mainCard', 'firstPrize', 'highlight']:
                if key in item and item[key]:
                    banner['best_hit'] = str(item[key])
                    break

            # End date
            for key in ['endDate', 'end_date', 'saleEnd', 'expiry', 'endAt']:
                if key in item and item[key]:
                    banner['sale_end_date'] = str(item[key])
                    break

            self._captured_banners[pack_id] = banner
            logger.debug(f"   ✅ Banner: {pack_id} ({self._current_category})")

    async def close(self):
        if self._context:
            await self._context.close()
        if self._browser:
            await self._browser.close()
        if self._playwright:
            await self._playwright.stop()
        logger.info("🔒 Browser geschlossen")

    async def scrape_all_banners(self) -> List[ScrapedBanner]:
        """Scrapes all ACTIVE banners - WITHOUT detail pages!"""

        self._api_responses = []
        self._captured_banners = {}
        self._current_category = "Bonus"

        now_jst = datetime.now(JST)
        logger.info(f"📄 Lade: {self.base_url}")
        logger.info(f"🕐 JST: {now_jst.strftime('%Y-%m-%d %H:%M')}")

        try:
            await self._page.goto(self.base_url, wait_until="domcontentloaded", timeout=60000)

            try:
                await self._page.wait_for_load_state("networkidle", timeout=20000)
            except:
                pass

            logger.info("⏳ Warte auf API (10s)...")
            await asyncio.sleep(10)

        except Exception as e:
            logger.error(f"❌ Ladefehler: {e}")
            return []

        # Go through categories
        for category in CATEGORIES:
            try:
                logger.info(f"🔍 {category}")
                self._current_category = category

                await self._click_category_tab(category)
                await asyncio.sleep(3)

                logger.info(f"   → {len(self._captured_banners)} Banner")

            except Exception as e:
                logger.debug(f"   Fehler: {e}")

        logger.info(f"📊 API-Responses: {len(self._api_responses)}")
        logger.info(f"🎯 Aktive Banner: {len(self._captured_banners)}")

        # Convert - WITHOUT loading detail pages!
        banners = self._convert_to_scraped_banners()

        logger.info(f"✅ Fertig: {len(banners)} Banner")
        return banners

    async def _click_category_tab(self, category: str) -> bool:
        variants = {
            "Pokémon": ["Pokémon", "Pokemon", "ポケモン"],
            "Yu-Gi-Oh!": ["Yu-Gi-Oh!", "Yu-Gi-Oh", "遊戯王"],
            "One piece": ["One piece", "One Piece", "ワンピース"],
            "Weiss Schwarz": ["Weiss Schwarz", "Weiss", "ヴァイス"],
            "Bonus": ["Bonus", "ボーナス"],
            "MIX": ["MIX", "Mix"],
            "Hobby": ["Hobby", "ホビー"],
        }

        for term in variants.get(category, [category]):
            try:
                loc = self._page.get_by_text(term, exact=True)
                if await loc.count() > 0:
                    await loc.first.click()
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
        """Converts to ScrapedBanner - WITHOUT detail pages!"""
        banners = []

        for pack_id, data in self._captured_banners.items():
            try:
                banner = ScrapedBanner(
                    pack_id=pack_id,
                    category=data.get('category', 'Unknown'),
                    title=data.get('title'),
                    best_hit=data.get('best_hit'),
                    price_coins=data.get('price'),
                    current_packs=data.get('current_packs'),
                    total_packs=data.get('total_packs'),
                    entries_per_day=data.get('entries_per_day'),
                    sale_end_date=data.get('sale_end_date'),
                    image_url=data.get('image_url'),
                    detail_page_url=f"{self.base_url}/pack-detail?packId={pack_id}",
                )
                banners.append(banner)
            except Exception as e:
                logger.warning(f"Fehler bei {pack_id}: {e}")

        return banners

    async def scrape_banner_details(self, pack_id: int) -> Tuple[Optional[str], Optional[bytes]]:
        """NOT USED ANYMORE - Data comes from API!"""
        # We already have best_hit from API
        if pack_id in self._captured_banners:
            best_hit = self._captured_banners[pack_id].get('best_hit')
            return best_hit, None
        return None, None

    async def download_image(self, url: str) -> Optional[bytes]:
        try:
            response = await self._page.request.get(url)
            if response.ok:
                return await response.body()
        except:
            pass
        return None
