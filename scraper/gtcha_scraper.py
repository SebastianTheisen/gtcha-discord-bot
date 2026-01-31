"""
GTCHA Webseiten-Scraper - VERSION v6 (Pure DOM)

- Keine API-Abfragen mehr
- Alle Daten direkt aus dem DOM
- Pro Kategorie-Tab die Banner auslesen
"""

import asyncio
import re
import random
from pathlib import Path
from typing import List, Optional, Tuple, Dict, Set
from datetime import datetime, timezone, timedelta

from playwright.async_api import async_playwright, Page, Browser, BrowserContext, ElementHandle
from loguru import logger

from .models import ScrapedBanner
from config import CATEGORIES, PARALLEL_SCRAPING

JST = timezone(timedelta(hours=9))

# User-Agent Pool für Rotation
USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/119.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:121.0) Gecko/20100101 Firefox/121.0",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.1 Safari/605.1.15",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36 Edg/120.0.0.0",
]


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

        # Banner-Daten
        self._captured_banners: Dict[int, Dict] = {}
        self._category_banners: Dict[str, Set[int]] = {cat: set() for cat in CATEGORIES}

    async def __aenter__(self):
        await self.start()
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        await self.close()

    async def _setup_route_interception(self):
        """Blockiert unnötige Requests die das Rendering verzögern."""
        async def handle_user_api(route):
            logger.debug(f"   Route abgefangen: {route.request.url}")
            await route.fulfill(status=200, content_type="application/json", body="{}")

        try:
            await self._page.route("**/api/user/**", handle_user_api)
            await self._page.route("**/accounts.google.com/**", lambda route: route.abort())
            logger.info("Route-Interception aktiviert")
        except Exception as e:
            logger.debug(f"Route-Interception fehlgeschlagen: {e}")

    async def start(self):
        logger.info("Starte Browser (WebKit)...")
        self._playwright = await async_playwright().start()

        self._browser = await self._playwright.webkit.launch(
            headless=self.headless,
        )
        user_agent = random.choice(USER_AGENTS)
        logger.debug(f"User-Agent: {user_agent[:50]}...")
        self._context = await self._browser.new_context(
            viewport={"width": 1920, "height": 1080},
            user_agent=user_agent,
            locale="ja-JP",
        )
        self._page = await self._context.new_page()
        await self._setup_route_interception()
        logger.info("Browser gestartet (WebKit)")

    async def close(self):
        if self._context:
            await self._context.close()
        if self._browser:
            await self._browser.close()
        if self._playwright:
            await self._playwright.stop()
        self._browser = None
        self._context = None
        self._page = None
        self._playwright = None
        logger.info("Browser geschlossen")

    async def _random_delay(self, min_sec: float = 1.0, max_sec: float = 3.0):
        """Zufällige Verzögerung um menschliches Verhalten zu simulieren."""
        delay = random.uniform(min_sec, max_sec)
        await asyncio.sleep(delay)

    async def _heartbeat(self, start_time: datetime):
        """Heartbeat-Task der alle 30 Sekunden den Status loggt."""
        while True:
            await asyncio.sleep(30)
            elapsed = (datetime.now(JST) - start_time).total_seconds()
            status = getattr(self, '_current_status', 'unbekannt')
            banner_count = len(self._captured_banners)
            logger.info(f"[HEARTBEAT] {elapsed:.0f}s - Status: {status} - Banner bisher: {banner_count}")

    async def scrape_all_banners(self) -> List[ScrapedBanner]:
        """Scrapet alle aktiven Banner aus dem DOM."""

        # Parallel-Modus wenn aktiviert
        if PARALLEL_SCRAPING:
            logger.info("Paralleles Scraping aktiviert")
            return await self.scrape_all_banners_parallel()

        self._captured_banners = {}
        self._category_banners = {cat: set() for cat in CATEGORIES}
        self._current_status = "Initialisierung"

        now_jst = datetime.now(JST)
        start_time = now_jst
        logger.info(f"Lade: {self.base_url}")
        logger.info(f"JST: {now_jst.strftime('%Y-%m-%d %H:%M')}")

        # Heartbeat-Task starten
        heartbeat_task = asyncio.create_task(self._heartbeat(start_time))

        try:
            # === HAUPTLOGIK ===
            try:
                self._current_status = "Seite laden"
                await self._page.goto(self.base_url, wait_until="domcontentloaded", timeout=60000)
                logger.info("Seite geladen, warte auf JS...")
                # Warte auf networkidle um sicherzustellen dass XHR-Requests abgeschlossen sind
                try:
                    await self._page.wait_for_load_state("networkidle", timeout=15000)
                    logger.info("Networkidle erreicht")
                except Exception:
                    logger.info("Networkidle Timeout - fahre fort")
                await asyncio.sleep(3)

            except asyncio.CancelledError:
                # Extern abgebrochen (z.B. durch Timeout) - weiterleiten
                raise
            except Exception as e:
                logger.error(f"Ladefehler: {e}")
                return []

            # Durch alle Kategorien klicken und Banner aus DOM lesen
            # Graceful Degradation: Fehler in einer Kategorie stoppen nicht die anderen
            failed_categories = []
            successful_categories = []

            for category in CATEGORIES:
                try:
                    self._current_status = f"Kategorie: {category}"
                    logger.info(f"Kategorie: {category}")

                    # Tab klicken
                    clicked = await self._click_category_tab(category)
                    if not clicked:
                        logger.warning(f"   Tab nicht gefunden: {category}")
                        failed_categories.append((category, "Tab nicht gefunden"))
                        continue

                    # Warte auf DOM-Update und Stabilisierung (zufällige Verzögerung)
                    await self._random_delay(2.0, 4.0)
                    try:
                        await self._page.wait_for_load_state("domcontentloaded", timeout=5000)
                    except asyncio.CancelledError:
                        raise
                    except:
                        pass

                    # Banner aus DOM extrahieren
                    self._current_status = f"Extrahiere: {category}"
                    count = await self._extract_banners_from_dom(category)
                    logger.info(f"   -> {count} Banner in {category}")
                    successful_categories.append((category, count))

                except asyncio.CancelledError:
                    # Extern abgebrochen - weiterleiten
                    raise
                except Exception as e:
                    logger.warning(f"   Fehler bei {category}: {e}")
                    failed_categories.append((category, str(e)))
                    # Wichtig: Weiter zur nächsten Kategorie!
                    continue

            # Zusammenfassung der Ergebnisse
            if failed_categories:
                logger.warning(f"Fehlgeschlagene Kategorien: {len(failed_categories)}/{len(CATEGORIES)}")
                for cat, reason in failed_categories:
                    logger.warning(f"   - {cat}: {reason}")

            if successful_categories:
                logger.info(f"Erfolgreiche Kategorien: {len(successful_categories)}/{len(CATEGORIES)}")

            # Statistik
            self._current_status = "Abschluss"
            logger.info(f"Gesamt aktive Banner: {len(self._captured_banners)}")

            for cat in CATEGORIES:
                count = len(self._category_banners.get(cat, set()))
                if count > 0:
                    logger.info(f"   {cat}: {count} Banner")

            # Konvertieren
            banners = self._convert_to_scraped_banners()

            logger.info(f"Fertig: {len(banners)} Banner")
            return banners

        finally:
            # WICHTIG: Heartbeat IMMER stoppen, auch bei Timeout/Cancel!
            heartbeat_task.cancel()
            try:
                await heartbeat_task
            except asyncio.CancelledError:
                pass
            logger.debug("Heartbeat gestoppt")

    async def scrape_all_banners_parallel(self) -> List[ScrapedBanner]:
        """Scrapet alle Kategorien parallel mit mehreren Browser-Tabs."""

        self._captured_banners = {}
        self._category_banners = {cat: set() for cat in CATEGORIES}
        self._current_status = "Parallel-Scraping"

        now_jst = datetime.now(JST)
        start_time = now_jst
        logger.info(f"PARALLEL SCRAPING: {self.base_url}")
        logger.info(f"JST: {now_jst.strftime('%Y-%m-%d %H:%M')}")

        # Heartbeat-Task starten
        heartbeat_task = asyncio.create_task(self._heartbeat(start_time))

        try:
            # Kategorien in Gruppen aufteilen (max 3 parallel für kleinen Server)
            MAX_PARALLEL = 3
            category_groups = [CATEGORIES[i:i+MAX_PARALLEL] for i in range(0, len(CATEGORIES), MAX_PARALLEL)]

            failed_categories = []
            successful_categories = []

            for group_idx, category_group in enumerate(category_groups):
                logger.info(f"Gruppe {group_idx + 1}/{len(category_groups)}: {', '.join(category_group)}")

                # Für jede Kategorie eine eigene Page erstellen und parallel scrapen
                tasks = []
                pages = []

                for category in category_group:
                    page = await self._context.new_page()
                    pages.append(page)
                    task = self._scrape_single_category_parallel(page, category)
                    tasks.append(task)

                # Parallel ausführen
                results = await asyncio.gather(*tasks, return_exceptions=True)

                # Pages schließen
                for page in pages:
                    try:
                        await page.close()
                    except:
                        pass

                # Ergebnisse verarbeiten
                for category, result in zip(category_group, results):
                    if isinstance(result, Exception):
                        logger.warning(f"   Fehler bei {category}: {result}")
                        failed_categories.append((category, str(result)))
                    elif result is not None:
                        count, banners_data = result
                        # Banner-Daten mergen
                        for pack_id, data in banners_data.items():
                            if pack_id not in self._captured_banners:
                                self._captured_banners[pack_id] = data
                            self._category_banners[category].add(pack_id)
                        successful_categories.append((category, count))
                        logger.info(f"   -> {count} Banner in {category}")

                # Kurze Pause zwischen Gruppen
                if group_idx < len(category_groups) - 1:
                    await self._random_delay(1.0, 2.0)

            # Zusammenfassung
            if failed_categories:
                logger.warning(f"Fehlgeschlagene Kategorien: {len(failed_categories)}/{len(CATEGORIES)}")
                for cat, reason in failed_categories:
                    logger.warning(f"   - {cat}: {reason}")

            if successful_categories:
                logger.info(f"Erfolgreiche Kategorien: {len(successful_categories)}/{len(CATEGORIES)}")

            # Statistik
            self._current_status = "Abschluss"
            logger.info(f"Gesamt aktive Banner: {len(self._captured_banners)}")

            for cat in CATEGORIES:
                count = len(self._category_banners.get(cat, set()))
                if count > 0:
                    logger.info(f"   {cat}: {count} Banner")

            # Konvertieren
            banners = self._convert_to_scraped_banners()
            logger.info(f"Fertig: {len(banners)} Banner")
            return banners

        finally:
            heartbeat_task.cancel()
            try:
                await heartbeat_task
            except asyncio.CancelledError:
                pass
            logger.debug("Heartbeat gestoppt")

    async def _scrape_single_category_parallel(self, page: Page, category: str) -> Tuple[int, Dict[int, Dict]]:
        """Scrapet eine einzelne Kategorie auf einer eigenen Page."""
        banners_data = {}

        try:
            # Seite laden
            await page.goto(self.base_url, wait_until="domcontentloaded", timeout=60000)
            await asyncio.sleep(3)

            # Tab klicken
            clicked = await self._click_category_tab_on_page(page, category)
            if not clicked:
                return (0, {})

            # Warten auf DOM-Update
            await self._random_delay(2.0, 3.0)

            # Banner extrahieren
            count = await self._extract_banners_from_page(page, category, banners_data)
            return (count, banners_data)

        except asyncio.CancelledError:
            raise
        except Exception as e:
            logger.debug(f"Parallel-Scrape Fehler für {category}: {e}")
            raise

    async def _click_category_tab_on_page(self, page: Page, category: str) -> bool:
        """Klickt auf einen Kategorie-Tab auf einer spezifischen Page."""
        category_keywords = {
            "Bonus": ["bonus", "ボーナス"],
            "MIX": ["mix"],
            "Yu-Gi-Oh!": ["yu-gi-oh", "yugioh", "遊戯王"],
            "Pokémon": ["pokemon", "poke", "ポケモン"],
            "Weiss Schwarz": ["weiss", "schwarz", "ヴァイスシュヴァルツ", "ヴァイスシュバルツ"],
            "One piece": ["one piece", "onepiece", "ワンピース"],
            "Hobby": ["hobby", "ホビー"],
        }

        keywords = category_keywords.get(category, [category.lower()])

        for attempt in range(3):
            try:
                tabs = await page.query_selector_all('.pack_menu, .tab-item, .category-tab, [role="tab"], .nav-item, .menu-item')

                for tab in tabs:
                    try:
                        text = await tab.inner_text()
                        text_lower = text.lower().strip()

                        for keyword in keywords:
                            if keyword in text_lower:
                                await tab.click()
                                return True
                    except:
                        continue

            except asyncio.CancelledError:
                raise
            except Exception as e:
                if "crashed" in str(e).lower():
                    try:
                        await page.reload(wait_until="domcontentloaded", timeout=30000)
                        await self._random_delay(2.0, 4.0)
                    except:
                        pass

            if attempt < 2:
                await asyncio.sleep(2)

        return False

    async def _extract_banners_from_page(self, page: Page, category: str, banners_data: Dict[int, Dict]) -> int:
        """Extrahiert Banner aus einer spezifischen Page."""
        count = 0

        try:
            banner_elements = await page.query_selector_all('[data-pack-id]')

            for el in banner_elements:
                try:
                    is_visible = await el.is_visible()
                    if not is_visible:
                        continue

                    pack_id_str = await el.get_attribute('data-pack-id')
                    if not pack_id_str or not pack_id_str.isdigit():
                        continue

                    pack_id = int(pack_id_str)

                    if pack_id in banners_data:
                        count += 1
                        continue

                    banner = await self._parse_banner_element(el, pack_id, category)
                    if banner:
                        banners_data[pack_id] = banner
                        count += 1

                except Exception as e:
                    logger.debug(f"   Banner-Element Fehler: {e}")

        except Exception as e:
            logger.warning(f"   DOM-Extraktion Fehler: {e}")

        return count

    async def _click_category_tab(self, category: str) -> bool:
        """Klickt auf einen Kategorie-Tab im Menü."""
        # Mapping: Config-Name -> mögliche DOM-Texte (lowercase für Vergleich)
        # Japanische Tab-Namen von der Webseite:
        # ボーナス, MIX, 遊戯王, ポケモン, ヴァイスシュヴァルツ, ワンピース, ホビー
        category_keywords = {
            "Bonus": ["bonus", "ボーナス"],
            "MIX": ["mix"],
            "Yu-Gi-Oh!": ["yu-gi-oh", "yugioh", "遊戯王"],
            "Pokémon": ["pokemon", "poke", "ポケモン"],
            "Weiss Schwarz": ["weiss", "schwarz", "ヴァイスシュヴァルツ", "ヴァイスシュバルツ"],
            "One piece": ["one piece", "onepiece", "ワンピース"],
            "Hobby": ["hobby", "ホビー"],
        }

        keywords = category_keywords.get(category, [category.lower()])

        # CSS selectors to try - broad set for resilience against site changes
        tab_selectors = [
            '.pack_menu, .tab-item, .category-tab, [role="tab"], .nav-item, .menu-item',
            '.pack-menu, .packMenu, .pack_tab, .pack-tab',
            'a[href*="category"], a[href*="pack"], button[class*="tab"], button[class*="menu"]',
            'li[class*="tab"], li[class*="menu"], div[class*="tab"], div[class*="menu"]',
        ]

        # Retry-Mechanismus
        for attempt in range(3):
            try:
                # Warte kurz damit die Seite stabil ist
                await asyncio.sleep(0.5)

                # Finde alle menu-items mit mehreren Selector-Gruppen
                menu_items = []
                for selector in tab_selectors:
                    try:
                        items = await self._page.query_selector_all(selector)
                        if items:
                            menu_items.extend(items)
                    except Exception:
                        continue

                if attempt == 0:
                    # Log alle gefundenen Tabs beim ersten Versuch
                    all_tabs = []
                    for item in menu_items:
                        try:
                            t = await item.inner_text()
                            all_tabs.append(t.strip())
                        except:
                            pass
                    logger.info(f"   Gefundene Tabs ({len(menu_items)}): {all_tabs}")

                    # Wenn keine Tabs gefunden, logge einen Ausschnitt des DOM
                    if not menu_items:
                        try:
                            body_html = await self._page.evaluate("() => document.body ? document.body.innerHTML.substring(0, 2000) : 'no body'")
                            logger.warning(f"   Keine Tabs gefunden! DOM-Ausschnitt: {body_html[:500]}")
                        except Exception:
                            pass

                for item in menu_items:
                    try:
                        text = await item.inner_text()
                        text_clean = text.strip()
                        text_lower = text_clean.lower()

                        # Prüfe ob einer der Keywords im Tab-Text vorkommt
                        for keyword in keywords:
                            if keyword in text_lower:
                                await item.click()
                                logger.debug(f"   Klick: '{text_clean}' (keyword: {keyword})")
                                await asyncio.sleep(1)
                                return True
                    except Exception as inner_e:
                        logger.debug(f"   Item-Fehler: {inner_e}")
                        continue

                # Fallback: JS-basierte Textsuche über alle klickbaren Elemente
                if not menu_items or attempt > 0:
                    for keyword in keywords:
                        try:
                            clicked = await self._page.evaluate("""(keyword) => {
                                const elements = document.querySelectorAll('a, button, li, div, span');
                                for (const el of elements) {
                                    const text = (el.textContent || '').trim().toLowerCase();
                                    if (text === keyword || (text.length < 30 && text.includes(keyword))) {
                                        el.click();
                                        return true;
                                    }
                                }
                                return false;
                            }""", keyword)
                            if clicked:
                                logger.debug(f"   JS-Fallback Klick (keyword: {keyword})")
                                await asyncio.sleep(1)
                                return True
                        except Exception:
                            continue

            except Exception as e:
                logger.debug(f"   Versuch {attempt+1} fehlgeschlagen: {e}")
                # Bei Crash: Seite neu laden
                if "crashed" in str(e).lower():
                    try:
                        logger.warning(f"   Seite crasht - lade neu...")
                        await self._page.reload(wait_until="domcontentloaded", timeout=30000)
                        await self._random_delay(2.0, 4.0)
                    except:
                        pass

            # Warten vor nächstem Versuch
            if attempt < 2:
                await asyncio.sleep(2)

        logger.warning(f"   Tab nicht gefunden: {category}")
        return False

    async def _extract_banners_from_dom(self, category: str) -> int:
        """Extrahiert alle sichtbaren Banner aus dem DOM."""
        count = 0

        try:
            # Finde alle Banner-Elemente
            banner_elements = await self._page.query_selector_all('[data-pack-id]')
            logger.debug(f"   Gefundene [data-pack-id] Elemente: {len(banner_elements)}")

            for el in banner_elements:
                try:
                    # Prüfe Sichtbarkeit
                    is_visible = await el.is_visible()
                    if not is_visible:
                        continue

                    # Pack ID
                    pack_id_str = await el.get_attribute('data-pack-id')
                    if not pack_id_str or not pack_id_str.isdigit():
                        continue

                    pack_id = int(pack_id_str)

                    # Wenn Banner schon existiert, nur Kategorie hinzufügen
                    if pack_id in self._captured_banners:
                        self._category_banners[category].add(pack_id)
                        count += 1
                        continue

                    # Neuen Banner aus DOM extrahieren
                    banner = await self._parse_banner_element(el, pack_id, category)
                    if banner:
                        self._captured_banners[pack_id] = banner
                        self._category_banners[category].add(pack_id)
                        count += 1

                except Exception as e:
                    logger.debug(f"   Banner-Element Fehler: {e}")

        except Exception as e:
            logger.warning(f"   DOM-Extraktion Fehler: {e}")

        return count

    async def _parse_banner_element(self, el: ElementHandle, pack_id: int, category: str) -> Optional[Dict]:
        """Parst ein Banner-Element und extrahiert alle Daten."""
        banner = {
            'pack_id': pack_id,
            'category': category,
        }

        try:
            # Titel/Name aus verschiedenen möglichen Elementen
            title_selectors = [
                '.gacha_name',
                '.gacha-name',
                '.title',
                '.name',
                '.pack-name',
                '.gacha_title',
                'h3',
                'h4',
                '.header .text',
            ]
            for sel in title_selectors:
                try:
                    title_el = await el.query_selector(sel)
                    if title_el:
                        title_text = await title_el.inner_text()
                        title_text = title_text.strip()
                        if title_text and len(title_text) > 1:
                            banner['title'] = title_text
                            break
                except:
                    pass

            # Preis aus .gacha_pay
            # <div class="gacha_pay"><img ...><div>1.111</div></div>
            price_el = await el.query_selector('.gacha_pay div:not(:has(img))')
            if not price_el:
                price_el = await el.query_selector('.gacha_pay')
            if price_el:
                price_text = await price_el.inner_text()
                price_text = price_text.strip().replace('.', '').replace(',', '').replace(' ', '')
                # Extrahiere Zahl
                price_match = re.search(r'(\d+)', price_text)
                if price_match:
                    banner['price'] = int(price_match.group(1))

            # Entries per day aus .limit_detail
            # Deutsch: "Beschränkt auf 10 Mal" oder "Beschränkt auf 10 Mal pro Tag"
            # Japanisch: "1日50回限定" (50 mal pro Tag limitiert)
            # Erst .limit_detail versuchen (spezifischer), dann .buy_limit
            limit_el = await el.query_selector('.limit_detail')
            if not limit_el:
                limit_el = await el.query_selector('.buy_limit .limit_detail')
            if not limit_el:
                limit_el = await el.query_selector('.buy_limit')
            if limit_el:
                limit_text = await limit_el.inner_text()
                logger.debug(f"   limit_detail Text für {pack_id}: '{limit_text}'")

                # Japanisches Format: "1日50回限定" -> 50 (Zahl vor 回)
                jp_match = re.search(r'(\d+)回', limit_text)
                if jp_match:
                    banner['entries_per_day'] = int(jp_match.group(1))
                    logger.debug(f"   Entries für {pack_id}: {banner['entries_per_day']} (JP)")
                else:
                    # Deutsches Format: "Beschränkt auf 10 Mal" -> 10
                    de_match = re.search(r'(\d+)\s*Mal', limit_text, re.IGNORECASE)
                    if de_match:
                        banner['entries_per_day'] = int(de_match.group(1))
                        logger.debug(f"   Entries für {pack_id}: {banner['entries_per_day']} (DE)")
                    else:
                        # Fallback: letzte Zahl im Text
                        all_numbers = re.findall(r'(\d+)', limit_text)
                        if all_numbers:
                            banner['entries_per_day'] = int(all_numbers[-1])
                            logger.debug(f"   Entries für {pack_id}: {banner['entries_per_day']} (Fallback)")
                        else:
                            logger.warning(f"   Entries-Pattern nicht gefunden für {pack_id}: '{limit_text}'")
            else:
                logger.debug(f"   Kein .limit_detail/.buy_limit für {pack_id}")

            # Packs aus .gacha_bar
            # "Rückstand 100 / 2.000" oder "0 / 2,000"
            bar_el = await el.query_selector('.gacha_bar')
            if bar_el:
                bar_text = await bar_el.inner_text()
                logger.debug(f"   gacha_bar Text für {pack_id}: '{bar_text}'")
                # Entferne Tausender-Trennzeichen (. und ,) aus Zahlen
                # "0 / 2.000" -> "0 / 2000"
                bar_text_clean = re.sub(r'(\d)[.,](\d{3})', r'\1\2', bar_text)
                # Wiederhole für mehrere Tausender (z.B. 1.000.000)
                bar_text_clean = re.sub(r'(\d)[.,](\d{3})', r'\1\2', bar_text_clean)
                # Suche nach "X / Y" Pattern
                packs_match = re.search(r'(\d+)\s*/\s*(\d+)', bar_text_clean)
                if packs_match:
                    banner['current_packs'] = int(packs_match.group(1))
                    banner['total_packs'] = int(packs_match.group(2))
                    logger.debug(f"   Packs für {pack_id}: {banner['current_packs']}/{banner['total_packs']}")
                else:
                    logger.warning(f"   Packs-Pattern nicht gefunden für {pack_id}: '{bar_text_clean}'")
            else:
                logger.debug(f"   Kein .gacha_bar für {pack_id}")

            # End-Datum aus .end-date
            # "Verkauf bis 2026/01/21 JST"
            end_el = await el.query_selector('.end-date')
            if end_el:
                end_text = await end_el.inner_text()
                banner['sale_end_date'] = end_text.strip()

            # Bild-URL aus img.current
            img_el = await el.query_selector('img.current, .image img')
            if img_el:
                img_src = await img_el.get_attribute('src')
                if img_src:
                    if not img_src.startswith('http'):
                        img_src = f"{self.base_url}{img_src}"
                    # Entferne Query-Parameter für saubere URL
                    img_src = img_src.split('?')[0]
                    banner['image_url'] = img_src

            # Prüfe ob Banner aktiv ist (kein Countdown = aktiv)
            # Wenn "Bis zum Verkaufsbeginn" sichtbar ist oder Timer > 0, ist der Banner noch nicht aktiv
            countdown_el = await el.query_selector('.countdown')
            if countdown_el:
                # Prüfe auf Timer-Wert
                timer_el = await countdown_el.query_selector('.num.timer-font, .num, .timer-font')
                if timer_el:
                    timer_text = await timer_el.inner_text()
                    timer_text = timer_text.strip()
                    # Wenn Timer nicht leer und nicht "00.00.00" oder ähnlich
                    if timer_text and not all(c in '0.: ' for c in timer_text):
                        logger.debug(f"   Banner {pack_id} noch nicht aktiv (Timer: {timer_text})")
                        return None

                # Fallback: Prüfe auf "Verkaufsbeginn" Text
                countdown_text = await countdown_el.inner_text()
                if 'Verkaufsbeginn' in countdown_text or 'start' in countdown_text.lower():
                    logger.debug(f"   Banner {pack_id} noch nicht aktiv (Countdown)")
                    return None

            # Detail-URL
            banner['detail_page_url'] = f"{self.base_url}/pack-detail?packId={pack_id}"

            logger.debug(f"   Banner {pack_id}: {banner.get('price', '?')} Coins, {banner.get('current_packs', '?')}/{banner.get('total_packs', '?')} Packs")

            return banner

        except Exception as e:
            logger.debug(f"   Parse Fehler für {pack_id}: {e}")
            return None

    async def scrape_banner_details(self, pack_id: int) -> Tuple[Optional[str], Optional[bytes]]:
        """Holt den Best Hit (erste Karte) von der Detail-Seite."""
        detail_url = f"{self.base_url}/pack-detail?packId={pack_id}"

        try:
            logger.debug(f"   Lade Detail-Seite: {detail_url}")
            await self._page.goto(detail_url, wait_until="domcontentloaded", timeout=30000)
            await self._random_delay(2.0, 4.0)

            # Suche nach der ersten Karte (Rang 1)
            # Die erste .card-container hat rank-icon-1
            # Name ist in .card-info .name .text

            # Methode 1: Erste Karte mit rank-icon-1
            first_card = await self._page.query_selector('.card-container:has(.rank-icon-1)')
            if first_card:
                name_el = await first_card.query_selector('.name .text, .name span')
                if name_el:
                    text = await name_el.inner_text()
                    if text and len(text.strip()) > 2:
                        logger.debug(f"   Best Hit: {text.strip()}")
                        return text.strip(), None

            # Methode 2: Erste .card-container
            first_card = await self._page.query_selector('.card-container')
            if first_card:
                name_el = await first_card.query_selector('.name .text, .name span, .name')
                if name_el:
                    text = await name_el.inner_text()
                    if text and len(text.strip()) > 2:
                        logger.debug(f"   Best Hit: {text.strip()}")
                        return text.strip(), None

            # Methode 3: Direkt .name .text suchen
            name_el = await self._page.query_selector('.card-info .name .text, .name .text')
            if name_el:
                text = await name_el.inner_text()
                if text and len(text.strip()) > 2:
                    logger.debug(f"   Best Hit: {text.strip()}")
                    return text.strip(), None

            logger.debug(f"   Kein Best Hit gefunden für {pack_id}")
            return None, None

        except Exception as e:
            logger.debug(f"   Detail-Seite Fehler: {e}")
            return None, None

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
                    detail_page_url=data.get('detail_page_url', f"{self.base_url}/pack-detail?packId={pack_id}"),
                )
                banners.append(banner)
            except Exception as e:
                logger.warning(f"Fehler bei {pack_id}: {e}")

        return banners

    async def download_image(self, url: str) -> Optional[bytes]:
        try:
            response = await self._page.request.get(url)
            if response.ok:
                return await response.body()
        except:
            pass
        return None
