"""
GTCHA Webseiten-Scraper - VERSION v5

- Kategorien werden über DOM-Sichtbarkeit zugewiesen
- API gibt keine Kategorie zurück, daher Tab-basierte Zuordnung
- Keine Detail-Seiten (schnell!)
- Nur aktive Banner
"""

import asyncio
import json
import re
from pathlib import Path
from typing import List, Optional, Tuple, Dict, Any, Set
from datetime import datetime, timezone, timedelta

from playwright.async_api import async_playwright, Page, Browser, BrowserContext, Response
from loguru import logger

from .models import ScrapedBanner
from config import CATEGORIES

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

        self._api_responses: List[Dict] = []
        self._captured_banners: Dict[int, Dict] = {}
        self._current_category: str = "Unknown"

        # Speichere welche Banner zu welcher Kategorie gehoeren
        self._category_banners: Dict[str, Set[int]] = {cat: set() for cat in CATEGORIES}

    async def __aenter__(self):
        await self.start()
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        await self.close()

    async def start(self):
        logger.info("Starte Browser...")
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

        logger.info("Browser gestartet (v5 - DOM-basierte Kategorien)")

    async def _handle_response(self, response: Response):
        """Faengt API-Responses ab und speichert Banner-Daten."""
        url = response.url

        # Logge ALLE API-Aufrufe (nicht nur /api/user/)
        content_type = response.headers.get('content-type', '')
        if 'application/json' not in content_type:
            return

        # Zeige alle JSON-Responses von der Domain
        if 'gtchaxonline.com' not in url and 'gtcha' not in url.lower():
            return

        # Logge die URL immer
        logger.info(f"API Response: {url}")

        try:
            data = await response.json()
            logger.debug(f"API: {url}")

            # Logge erste Item-Keys um Struktur zu verstehen
            if isinstance(data, list) and len(data) > 0 and isinstance(data[0], dict):
                keys = list(data[0].keys())
                logger.debug(f"   List[0] keys: {keys}")
            elif isinstance(data, dict):
                keys = list(data.keys())
                logger.debug(f"   Dict keys: {keys}")
                # Wenn data.list, data.data oder data.items existiert
                for k in ['list', 'data', 'items', 'oripas', 'packs', 'banners']:
                    if k in data and isinstance(data[k], list) and len(data[k]) > 0:
                        if isinstance(data[k][0], dict):
                            item_keys = list(data[k][0].keys())
                            logger.debug(f"   {k}[0] keys: {item_keys}")
                            # Bei pack/list API: Zeige auch erstes Item komplett
                            if 'pack' in url.lower() and k == 'list':
                                first_item = data[k][0]
                                logger.info(f"   Pack list[0]: {first_item}")
                        break

            self._api_responses.append({
                'url': url,
                'data': data,
            })

            # Extrahiere Banner-Daten (ohne Kategorie)
            await self._extract_banners_from_api(data)

        except Exception as e:
            logger.debug(f"    Parse error: {e}")

    def _is_banner_active(self, item: Dict) -> bool:
        """Prueft ob Banner JETZT aktiv ist."""
        now_jst = datetime.now(JST)

        # Status pruefen
        for key in ['status', 'state', 'isActive', 'is_active', 'active', 'selling']:
            if key in item:
                val = item[key]
                if val in [False, 0, 'inactive', 'disabled', 'upcoming', 'scheduled', 'pending', 'hidden', 'draft']:
                    return False

        # Start-Datum
        for key in ['startDate', 'start_date', 'startAt', 'start_at', 'saleStart', 'openDate', 'releaseDate']:
            if key in item and item[key]:
                try:
                    start = self._parse_date(str(item[key]))
                    if start and start > now_jst:
                        return False
                except:
                    pass

        # End-Datum
        for key in ['endDate', 'end_date', 'endAt', 'end_at', 'saleEnd', 'expiry', 'closeDate']:
            if key in item and item[key]:
                try:
                    end = self._parse_date(str(item[key]))
                    if end and end < now_jst:
                        return False
                except:
                    pass

        return True

    def _parse_date(self, s: str) -> Optional[datetime]:
        try:
            if 'T' in s:
                dt = datetime.fromisoformat(s.replace('Z', '+00:00'))
                return dt if dt.tzinfo else dt.replace(tzinfo=JST)
            if '/' in s:
                p = s.replace(' JST', '').strip()
                fmt = '%Y/%m/%d %H:%M' if ' ' in p else '%Y/%m/%d'
                return datetime.strptime(p, fmt).replace(tzinfo=JST)
            if s.isdigit():
                ts = int(s)
                if ts > 1e12:
                    ts /= 1000
                return datetime.fromtimestamp(ts, tz=JST)
        except:
            pass
        return None

    async def _extract_banners_from_api(self, data: Any):
        """Extrahiert Banner-Daten aus API (ohne Kategorie-Zuweisung)."""

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

            # Pack ID
            pack_id = None
            for key in ['id', 'packId', 'pack_id', 'productId', 'oripaId']:
                if key in item:
                    try:
                        pack_id = int(item[key])
                        break
                    except:
                        pass

            if not pack_id:
                continue

            # Aktiv?
            if not self._is_banner_active(item):
                continue

            # Wenn Banner schon existiert, ueberspringen
            if pack_id in self._captured_banners:
                continue

            # Neuer Banner (noch ohne Kategorie!)
            banner = {
                'pack_id': pack_id,
                'category': None,  # Wird spaeter via DOM zugewiesen
                'raw_data': item,
            }

            # Preis
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

            # Limit
            for key in ['dailyLimit', 'limitPerDay', 'perDay', 'purchaseLimit']:
                if key in item:
                    try:
                        banner['entries_per_day'] = int(item[key])
                        break
                    except:
                        pass

            # Titel
            for key in ['name', 'title', 'productName', 'oripaName']:
                if key in item and item[key]:
                    banner['title'] = str(item[key])
                    break

            # Bild
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

            # End-Datum
            for key in ['endDate', 'end_date', 'saleEnd', 'expiry', 'endAt']:
                if key in item and item[key]:
                    banner['sale_end_date'] = str(item[key])
                    break

            self._captured_banners[pack_id] = banner

    async def _get_visible_pack_ids_from_dom(self) -> Set[int]:
        """Extrahiert Pack-IDs aus den sichtbaren DOM-Elementen."""
        pack_ids = set()

        try:
            # Primaer: Banner mit data-pack-id Attribut (wie im DOM gesehen)
            # class="banner_wrap banner" data-pack-id="15311"
            banner_elements = await self._page.query_selector_all('.banner_wrap[data-pack-id], .banner[data-pack-id], [data-pack-id]')
            logger.debug(f"   Gefundene [data-pack-id] Elemente: {len(banner_elements)}")

            for el in banner_elements:
                try:
                    pack_id_str = await el.get_attribute('data-pack-id')
                    if pack_id_str and pack_id_str.isdigit():
                        # Pruefe Sichtbarkeit - aber nicht zu streng
                        try:
                            is_visible = await el.is_visible()
                            if is_visible:
                                pack_ids.add(int(pack_id_str))
                        except:
                            # Bei Fehler trotzdem hinzufuegen
                            pack_ids.add(int(pack_id_str))
                except Exception as e:
                    logger.debug(f"   Element error: {e}")

            logger.debug(f"   Pack-IDs aus data-pack-id: {len(pack_ids)}")

            # Sekundaer: Links mit packId Parameter
            if len(pack_ids) < 10:  # Falls wenig gefunden
                links = await self._page.query_selector_all('a[href*="packId="], a[href*="pack-detail"]')
                for link in links:
                    try:
                        is_visible = await link.is_visible()
                        if not is_visible:
                            continue
                        href = await link.get_attribute('href')
                        if href:
                            match = re.search(r'packId[=:](\d+)', href)
                            if match:
                                pack_ids.add(int(match.group(1)))
                    except:
                        pass

            # Tertiaer: Bilder mit /pack/ID/ im src
            if len(pack_ids) < 10:
                images = await self._page.query_selector_all('img[src*="/pack/"]')
                for img in images:
                    try:
                        src = await img.get_attribute('src')
                        if src:
                            # src="/pack/15311/1.webp"
                            match = re.search(r'/pack/(\d+)/', src)
                            if match:
                                pack_ids.add(int(match.group(1)))
                    except:
                        pass

        except Exception as e:
            logger.warning(f"DOM-Extraktion Fehler: {e}")

        logger.debug(f"   Gesamt Pack-IDs aus DOM: {len(pack_ids)}")
        return pack_ids

    async def close(self):
        if self._context:
            await self._context.close()
        if self._browser:
            await self._browser.close()
        if self._playwright:
            await self._playwright.stop()
        logger.info("Browser geschlossen")

    async def scrape_all_banners(self) -> List[ScrapedBanner]:
        """Scrapet alle aktiven Banner mit korrekten Kategorien via DOM."""

        self._api_responses = []
        self._captured_banners = {}
        self._category_banners = {cat: set() for cat in CATEGORIES}

        now_jst = datetime.now(JST)
        logger.info(f"Lade: {self.base_url}")
        logger.info(f"JST: {now_jst.strftime('%Y-%m-%d %H:%M')}")

        try:
            await self._page.goto(self.base_url, wait_until="domcontentloaded", timeout=60000)

            try:
                await self._page.wait_for_load_state("networkidle", timeout=20000)
            except:
                pass

            logger.info("Warte auf API (8s)...")
            await asyncio.sleep(8)

            logger.info(f"   -> {len(self._captured_banners)} Banner aus API geladen")

        except Exception as e:
            logger.error(f"Ladefehler: {e}")
            return []

        # WICHTIG: Durch alle Kategorien klicken und sichtbare Banner erfassen
        for category in CATEGORIES:
            try:
                logger.info(f"Wechsle zu: {category}")

                # Tab klicken
                clicked = await self._click_category_tab(category)
                if not clicked:
                    logger.warning(f"   Tab nicht gefunden: {category}")
                    continue

                # Warte auf DOM-Update
                await asyncio.sleep(2)

                # Extrahiere sichtbare Pack-IDs aus dem DOM
                visible_ids = await self._get_visible_pack_ids_from_dom()

                # Weise Kategorie zu
                for pack_id in visible_ids:
                    if pack_id in self._captured_banners:
                        # Nur zuweisen wenn noch keine Kategorie
                        if self._captured_banners[pack_id].get('category') is None:
                            self._captured_banners[pack_id]['category'] = category
                        self._category_banners[category].add(pack_id)

                count = len(visible_ids)
                logger.info(f"   -> {count} Banner sichtbar in {category}")

            except Exception as e:
                logger.warning(f"   Fehler bei {category}: {e}")

        # Banner ohne Kategorie auf "Bonus" setzen (Fallback)
        uncategorized = 0
        for pack_id, banner in self._captured_banners.items():
            if banner.get('category') is None:
                banner['category'] = "Bonus"
                self._category_banners["Bonus"].add(pack_id)
                uncategorized += 1

        if uncategorized > 0:
            logger.warning(f"   {uncategorized} Banner ohne Kategorie -> Bonus")

        # Statistik
        logger.info(f"Gesamt API-Responses: {len(self._api_responses)}")
        logger.info(f"Gesamt aktive Banner: {len(self._captured_banners)}")

        for cat in CATEGORIES:
            count = len(self._category_banners.get(cat, set()))
            if count > 0:
                logger.info(f"   {cat}: {count} Banner")

        # Konvertieren
        banners = self._convert_to_scraped_banners()

        logger.info(f"Fertig: {len(banners)} Banner")
        return banners

    async def _click_category_tab(self, category: str) -> bool:
        """Klickt auf einen Kategorie-Tab im Menü."""
        variants = {
            "Pokémon": ["Pokémon", "Pokemon", "Pocketmonster", "ポケモン"],
            "Yu-Gi-Oh!": ["Yu-Gi-Oh!", "Yu-Gi-Oh", "遊戯王"],
            "One piece": ["One piece", "One Piece", "ワンピース"],
            "Weiss Schwarz": ["Weiss Schwarz", "ヴァイスシュヴァルツ"],
            "Bonus": ["Bonus", "ボーナス"],
            "MIX": ["MIX", "Mix"],
            "Hobby": ["Hobby", "ホビー"],
        }

        search_terms = variants.get(category, [category])

        # Methode 1: Direkt auf .menu-item klicken (wie im DOM gesehen)
        try:
            menu_items = await self._page.query_selector_all('.menu-item, .menu_item, [class*="menu"]')
            for item in menu_items:
                try:
                    text = await item.inner_text()
                    text = text.strip()
                    if text in search_terms or any(term in text for term in search_terms):
                        await item.click()
                        logger.debug(f"   Klick (menu-item): {text}")
                        return True
                except:
                    pass
        except Exception as e:
            logger.debug(f"   menu-item Fehler: {e}")

        # Methode 2: get_by_text mit kurzem Timeout
        for term in search_terms:
            try:
                loc = self._page.get_by_text(term, exact=True)
                if await loc.count() > 0:
                    await loc.first.click(timeout=3000)
                    logger.debug(f"   Klick (text): {term}")
                    return True
            except:
                pass

        # Methode 3: CSS Selector mit Text
        for term in search_terms:
            try:
                await self._page.click(f"text={term}", timeout=3000)
                logger.debug(f"   Klick (selector): {term}")
                return True
            except:
                pass

        return False

    def _convert_to_scraped_banners(self) -> List[ScrapedBanner]:
        """Konvertiert zu ScrapedBanner Objekten."""
        banners = []

        for pack_id, data in self._captured_banners.items():
            try:
                banner = ScrapedBanner(
                    pack_id=pack_id,
                    category=data.get('category', 'Bonus'),
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
        """Gibt best_hit aus Cache zurueck (keine Seiten-Ladung!)"""
        if pack_id in self._captured_banners:
            return self._captured_banners[pack_id].get('best_hit'), None
        return None, None

    async def download_image(self, url: str) -> Optional[bytes]:
        try:
            response = await self._page.request.get(url)
            if response.ok:
                return await response.body()
        except:
            pass
        return None
