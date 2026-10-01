"""
GTCHA Webseiten-Scraper - VERSION v6 (Pure DOM)

- Keine API-Abfragen mehr
- Alle Daten direkt aus dem DOM
- Pro Kategorie-Tab die Banner auslesen
"""

import asyncio
import re
import random
import time
import unicodedata
from pathlib import Path
from typing import List, Optional, Tuple, Dict, Set
from datetime import datetime, timezone, timedelta

from playwright.async_api import async_playwright, Page, Browser, BrowserContext
from loguru import logger

from .models import ScrapedBanner
from config import CATEGORIES, PARALLEL_SCRAPING, PARALLEL_TABS, SCRAPER_PROXY
from utils.card_pool import summarize_cards

JST = timezone(timedelta(hours=9))

FETCH_PACK_LIST_JS = """async () => {
    const r = await fetch('/api/user/pack/list', {headers: {Accept: 'application/json'}});
    try { return await r.json(); } catch (e) { return null; }
}"""

# Lädt Seite 1 der Kartenliste, dann alle weiteren Seiten gleichzeitig, und gibt alle Karten zurück.
FETCH_CARD_LIST_JS = """async (pid) => {
    const get = async (n) => {
        const r = await fetch(`/api/user/pack/card_list/${pid}/${n}`, {headers: {Accept: 'application/json'}});
        try { return await r.json(); } catch (e) { return {}; }
    };
    const first = await get(1);
    if (!Array.isArray(first.list)) return null;
    const pages = Math.min(parseInt((first.page || {}).all_page) || 1, 60);
    const rest = await Promise.all(Array.from({length: pages - 1}, (_, i) => get(i + 2)));
    return [first, ...rest].flatMap(d => d.list || []);
}"""

TITLE_SELECTORS = ['.gacha_name', '.gacha-name', '.title', '.name', '.pack-name',
                   '.gacha_title', 'h3', 'h4', '.header .text']

# Liest pro Banner-Element dieselben Felder wie früher die Einzelabfragen, aber alle in einem
# Browser-Aufruf. innerText entspricht Playwrights inner_text(), die Sichtbarkeit is_visible().
BANNER_EXTRACT_JS = """(els, titleSelectors) => els.map(el => {
    const q = s => { try { return el.querySelector(s); } catch (e) { return null; } };
    const text = e => (e ? e.innerText : null);
    const rect = el.getBoundingClientRect();
    const visible = rect.width > 0 && rect.height > 0 && getComputedStyle(el).visibility !== 'hidden';
    let title = null;
    for (const s of titleSelectors) {
        const t = text(q(s));
        if (t && t.trim().length > 1) { title = t.trim(); break; }
    }
    const priceEl = q('.gacha_pay div:not(:has(img))') || q('.gacha_pay');
    const limitEl = q('.limit_detail') || q('.buy_limit .limit_detail') || q('.buy_limit');
    const img = q('img.current, .image img');
    const cd = q('.countdown');
    let timer = null;
    if (cd) { try { timer = cd.querySelector('.num.timer-font, .num, .timer-font'); } catch (e) {} }
    return {
        id: el.getAttribute('data-pack-id'),
        visible,
        title,
        price: text(priceEl),
        limit: text(limitEl),
        bar: text(q('.gacha_bar')),
        end: text(q('.end-date')),
        has_img: !!img,
        img: img ? img.getAttribute('src') : null,
        has_countdown: !!cd,
        timer: text(timer),
        countdown: text(cd),
    };
})"""


def _normalize(text: str) -> str:
    """Akzente entfernen und in Kleinbuchstaben – damit 'pokemon' auf 'Pokémon' matcht."""
    nfkd = unicodedata.normalize('NFKD', text)
    return ''.join(c for c in nfkd if not unicodedata.combining(c)).lower()


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
        # Banner-Daten
        self._captured_banners: Dict[int, Dict] = {}
        self._category_banners: Dict[str, Set[int]] = {cat: set() for cat in CATEGORIES}

    async def __aenter__(self):
        await self.start()
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        await self.close()

    async def start(self):
        logger.info("Starte Browser...")
        self._playwright = await async_playwright().start()

        # Der ganze Browser läuft über SCRAPER_PROXY (Tor), weil die Seite den VPS- und
        # WARP-IPs einen falschen Pack-Zähler liefert. Fällt der Proxy aus, schlägt der Scrape
        # bewusst fehl, statt direkt (mit falschem Zähler) zu laden.
        self._browser = await self._playwright.chromium.launch(
            headless=self.headless,
            # Bilder per Browser-Einstellung statt page.route() blockieren: page.route() schaltet
            # den HTTP-Cache ab, dann lädt jeder Tab alle Skripte erneut über Tor.
            args=['--no-sandbox', '--disable-setuid-sandbox', '--disable-dev-shm-usage', '--disable-gpu',
                  '--blink-settings=imagesEnabled=false'],
        )
        if SCRAPER_PROXY:
            logger.info(f"Browser-Proxy: {SCRAPER_PROXY.split('@')[-1]}")
        else:
            logger.info("Kein Proxy konfiguriert (SCRAPER_PROXY nicht gesetzt)")

        # Zufälligen User-Agent auswählen
        user_agent = random.choice(USER_AGENTS)
        logger.debug(f"User-Agent: {user_agent[:50]}...")

        # Manche Server nutzen X-Forwarded-For / X-Real-IP für Geolocation statt der echten IP.
        # Wir senden eine deutsche Telekom-IP damit der Server Deutschland als Herkunftsland erkennt.
        # Kein Cache-Control/Pragma: die Seite liefert HTML und API ohnehin mit no-store,
        # und die Header würden den Browser-Cache für die (unveränderlichen) Skripte umgehen.
        geo_headers = {
            "Accept-Language": "de-DE,de;q=0.9,en;q=0.8",
            "X-Forwarded-For": "217.237.150.100",   # Deutsche Telekom (T-Online)
            "X-Real-IP": "217.237.150.100",
            "CF-Connecting-IP": "217.237.150.100",
            "X-Country": "DE",
            "X-Country-Code": "DE",
        }

        proxy_cfg = {"server": SCRAPER_PROXY} if SCRAPER_PROXY else None
        self._context = await self._browser.new_context(
            viewport={"width": 1920, "height": 1080},
            user_agent=user_agent,
            extra_http_headers=geo_headers,
            proxy=proxy_cfg,
        )

        self._page = await self._context.new_page()


        # API-Response abfangen: /api/user/pack/list enthält echte Pack-Zahlen.
        # Listener am Kontext-Level → gilt für alle Pages (main + parallel).
        self._api_pack_data: Dict[int, dict] = {}

        async def _capture_pack_api(response):
            try:
                if response.status != 200:
                    return
                ct = response.headers.get('content-type', '')
                if 'json' not in ct:
                    return
                if 'pack/list' in response.url:
                    data = await response.json()
                    items = data.get('list', [])
                    for item in items:
                        pid = item.get('id')
                        if pid:
                            self._api_pack_data[int(pid)] = item
                    if items:
                        logger.debug(f"[PACK-API] {len(items)} Pack-Zahlen via Browser+Proxy geladen")
            except Exception as e:
                logger.debug(f"[PACK-API] Fehler: {e}")

        self._context.on('response', _capture_pack_api)

        logger.info("Browser gestartet (v6 - Pure DOM, Bilder aus)")

    async def close(self):
        if self._context:
            await self._context.close()
        if self._browser:
            await self._browser.close()
        if self._playwright:
            await self._playwright.stop()
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
        self._api_pack_data = {}
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
                # Cache-Busting: Timestamp-Parameter verhindert CDN-Cache-Treffer
                cache_bust_url = f"{self.base_url}?_={int(time.time())}"
                await self._page.goto(cache_bust_url, wait_until="domcontentloaded", timeout=90000)
                logger.info("Seite geladen, warte auf Tabs...")
                # Warte auf Tab-Menü
                try:
                    await self._page.wait_for_selector('.pack_menu_list .pack_menu', timeout=60000)
                    await asyncio.sleep(1)
                except Exception as e:
                    logger.warning(f"Tab-Menü nicht gefunden: {e}")
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

                    # Warte auf AJAX-Update der Pack-Zahlen.
                    # Die Seite rendert erst alte Werte (SSR-Cache), dann lädt JS die echten Zahlen.
                    # networkidle würde durch WebSockets nie enden → kurzes Timeout akzeptieren.
                    try:
                        await self._page.wait_for_load_state("networkidle", timeout=4000)
                    except asyncio.CancelledError:
                        raise
                    except:
                        # Timeout erwartet wegen WebSockets – trotzdem 4s gewartet, reicht für AJAX
                        pass
                    # Zusätzlicher Buffer damit DOM komplett gerendert ist
                    await asyncio.sleep(1.5)

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

    async def fetch_pack_list(self) -> Dict[int, Dict]:
        """Alle Banner mit Pack-Zahlen in einer Anfrage (pack/list) über die Browser-Sitzung (Tor).

        Ersetzt bei normalen Scrapes das Durchklicken der Tabs. Leeres Ergebnis = Fehler.
        """
        page = await self._context.new_page()
        try:
            for url in (f"{self.base_url}/api/user/point", self.base_url):
                # Erst ein leichtes Dokument der Seite (setzt die Sitzung), notfalls die Startseite
                await page.goto(url, wait_until="domcontentloaded", timeout=90000)
                data = await page.evaluate(FETCH_PACK_LIST_JS)
                items = (data or {}).get("list") if isinstance(data, dict) else None
                if items:
                    result = {int(it["id"]): it for it in items if it.get("id")}
                    self._api_pack_data = dict(result)
                    logger.info(f"[API] pack/list: {len(result)} Banner")
                    return result
            logger.warning("[API] pack/list lieferte keine Banner")
            return {}
        finally:
            await page.close()

    async def fetch_card_pools(self, pack_ids: List[int]) -> Dict[int, Dict]:
        """Holt die komplette Kartenliste (alle Seiten) je Banner über die Browser-Sitzung (Tor)."""
        pools: Dict[int, Dict] = {}
        if not pack_ids:
            return pools
        page = await self._context.new_page()
        try:
            # Ein Dokument auf der Seiten-Domain, damit fetch() Cookies und Proxy des Browsers nutzt
            await page.goto(f"{self.base_url}/api/user/point", wait_until="domcontentloaded", timeout=60000)
            for pid in pack_ids:
                try:
                    cards = await page.evaluate(FETCH_CARD_LIST_JS, pid)
                    if cards is None:
                        logger.warning(f"[POOL] {pid}: Kartenliste nicht abrufbar - nächster Scrape versucht es erneut")
                        continue
                    pool = summarize_cards(cards)
                    if pool:
                        logger.info(f"[POOL] {pid}: {len(cards)} Karten, {pool['total_count']} Packs, "
                                    f"{pool['hits_total']} Hits")
                    else:
                        # Banner ohne Karten merken, damit er nicht bei jedem Scrape neu geladen wird
                        pool = {"total_count": 0, "total_value": 0, "hits_total": 0, "top": []}
                        logger.info(f"[POOL] {pid}: Banner hat keine Kartenliste")
                    pools[pid] = pool
                except Exception as e:
                    logger.warning(f"[POOL] {pid}: Fehler beim Laden der Kartenliste: {e}")
        finally:
            await page.close()
        return pools

    async def scrape_all_banners_parallel(self) -> List[ScrapedBanner]:
        """Scrapet alle Kategorien parallel mit mehreren Browser-Tabs."""

        self._captured_banners = {}
        self._category_banners = {cat: set() for cat in CATEGORIES}
        self._api_pack_data = {}
        self._current_status = "Parallel-Scraping"

        now_jst = datetime.now(JST)
        start_time = now_jst
        logger.info(f"PARALLEL SCRAPING: {self.base_url}")
        logger.info(f"JST: {now_jst.strftime('%Y-%m-%d %H:%M')}")

        # Heartbeat-Task starten
        heartbeat_task = asyncio.create_task(self._heartbeat(start_time))

        try:
            # Kategorien in Gruppen aufteilen (konfigurierbar via PARALLEL_TABS)
            MAX_PARALLEL = PARALLEL_TABS
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

                # Erster Tab allein füllt den Browser-Cache (Skripte), die übrigen laden
                # danach parallel aus dem Cache statt alle gleichzeitig über Tor.
                results = await asyncio.gather(tasks[0], return_exceptions=True)
                results += await asyncio.gather(*tasks[1:], return_exceptions=True)

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
                        successful_categories.append((category, result[0]))

                # Kurze Pause zwischen Gruppen
                if group_idx < len(category_groups) - 1:
                    await self._random_delay(0.5, 1.0)

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
            # Seite laden - Cache-Busting via Timestamp-Parameter
            cache_bust_url = f"{self.base_url}?_={int(time.time())}"
            await page.goto(cache_bust_url, wait_until="domcontentloaded", timeout=90000)

            # Warte auf Tab-Menü (JavaScript lädt die Tabs)
            try:
                await page.wait_for_selector('.pack_menu_list .pack_menu', timeout=60000)
                await asyncio.sleep(1)
            except Exception as e:
                logger.debug(f"   [{category}] wait_for_selector fehlgeschlagen: {e}")
                await asyncio.sleep(3)

            # Tab klicken (mit Retry)
            clicked = await self._click_category_tab_on_page(page, category)
            if not clicked:
                # Retry: Seite neu laden und nochmal versuchen
                logger.debug(f"   [{category}] Retry nach Tab-Fehler...")
                retry_url = f"{self.base_url}?_={int(time.time())}"
                await page.goto(retry_url, wait_until="domcontentloaded", timeout=90000)
                try:
                    await page.wait_for_selector('.pack_menu_list .pack_menu', timeout=60000)
                    await asyncio.sleep(1)
                except Exception:
                    await asyncio.sleep(3)
                clicked = await self._click_category_tab_on_page(page, category)
                if not clicked:
                    return (0, {})

            # Warte auf AJAX-Update der Pack-Zahlen
            try:
                await page.wait_for_load_state("networkidle", timeout=4000)
            except asyncio.CancelledError:
                raise
            except:
                pass
            await asyncio.sleep(1.5)

            # Banner extrahieren
            count = await self._extract_banners_from_page(page, category, banners_data)
            for pack_id, data in banners_data.items():
                self._captured_banners.setdefault(pack_id, data)
                self._category_banners[category].add(pack_id)
            logger.info(f"   -> {count} Banner in {category}")
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
            "Pokémon": ["pokemon", "pokémon", "poke", "ポケモン"],
            "One piece": ["one piece", "onepiece", "ワンピース"],
            "Dragon Ball": ["dragon ball", "dragonball", "ドラゴンボール"],
        }

        keywords = [_normalize(k) for k in category_keywords.get(category, [category.lower()])]

        for attempt in range(2):
            try:
                await asyncio.sleep(0.3)
                tabs = await page.query_selector_all('.pack_menu_list .pack_menu')

                for tab in tabs:
                    try:
                        text = await tab.inner_text()
                        if not text.strip():
                            text = await tab.text_content() or ''
                        if not text.strip():
                            text = (await tab.get_attribute('aria-label') or
                                    await tab.get_attribute('title') or
                                    await tab.get_attribute('data-category') or '')

                        text_norm = _normalize(text.strip())
                        if not text_norm:
                            continue

                        for keyword in keywords:
                            if keyword in text_norm:
                                await tab.click()
                                logger.debug(f"   [{category}] Klick: '{text.strip()}' (keyword: {keyword})")
                                await asyncio.sleep(0.3)
                                return True
                    except:
                        continue

            except asyncio.CancelledError:
                raise
            except Exception as e:
                logger.debug(f"   [{category}] Versuch {attempt+1} fehlgeschlagen: {e}")
                if "crashed" in str(e).lower():
                    try:
                        await page.reload(wait_until="domcontentloaded", timeout=90000)
                        await self._random_delay(2.0, 4.0)
                    except:
                        pass

            if attempt < 1:
                await asyncio.sleep(1)

        # Zeige verfügbare Tabs für Diagnose
        try:
            all_tabs = await page.query_selector_all('.pack_menu_list .pack_menu')
            tab_texts = []
            for t in all_tabs:
                try:
                    txt = (await t.inner_text()).strip() or (await t.text_content() or '').strip()
                    tab_texts.append(repr(txt))
                except:
                    pass
            logger.warning(f"   Tab nicht gefunden: {category} | Verfügbare Tabs: {tab_texts}")
        except:
            logger.warning(f"   Tab nicht gefunden: {category}")
        return False

    async def _read_banner_elements(self, page: Page) -> List[Dict]:
        """Liest alle Banner-Rohtexte in einem einzigen Browser-Aufruf (statt ~25 Abfragen pro Banner)."""
        return await page.eval_on_selector_all('[data-pack-id]', BANNER_EXTRACT_JS, TITLE_SELECTORS)

    async def _extract_banners_from_page(self, page: Page, category: str, banners_data: Dict[int, Dict]) -> int:
        """Extrahiert Banner aus einer spezifischen Page."""
        count = 0

        try:
            for raw in await self._read_banner_elements(page):
                try:
                    if not raw.get('visible'):
                        continue
                    pack_id_str = raw.get('id') or ''
                    if not pack_id_str.isdigit():
                        continue
                    pack_id = int(pack_id_str)

                    if pack_id in banners_data:
                        count += 1
                        continue

                    banner = self._parse_banner_raw(raw, pack_id, category)
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
        category_keywords = {
            "Bonus": ["bonus", "ボーナス"],
            "MIX": ["mix"],
            "Pokémon": ["pokemon", "pokémon", "poke", "ポケモン"],
            "One piece": ["one piece", "onepiece", "ワンピース"],
            "Dragon Ball": ["dragon ball", "dragonball", "ドラゴンボール"],
        }

        keywords = [_normalize(k) for k in category_keywords.get(category, [category.lower()])]

        # Retry-Mechanismus (2 Versuche reichen normalerweise)
        for attempt in range(2):
            try:
                await asyncio.sleep(0.3)
                menu_items = await self._page.query_selector_all('.pack_menu_list .pack_menu')

                for item in menu_items:
                    try:
                        # inner_text() für sichtbaren Text, text_content() als Fallback
                        text = await item.inner_text()
                        if not text.strip():
                            text = await item.text_content() or ''
                        if not text.strip():
                            # Letzter Versuch: aria-label oder title Attribut
                            text = (await item.get_attribute('aria-label') or
                                    await item.get_attribute('title') or
                                    await item.get_attribute('data-category') or '')

                        text_norm = _normalize(text.strip())
                        if not text_norm:
                            continue

                        for keyword in keywords:
                            if keyword in text_norm:
                                await item.click()
                                logger.debug(f"   Klick: '{text.strip()}' (keyword: {keyword})")
                                await asyncio.sleep(0.3)
                                return True
                    except Exception as inner_e:
                        logger.debug(f"   Item-Fehler: {inner_e}")
                        continue

            except Exception as e:
                logger.debug(f"   Versuch {attempt+1} fehlgeschlagen: {e}")
                if "crashed" in str(e).lower():
                    try:
                        logger.warning(f"   Seite crasht - lade neu...")
                        await self._page.reload(wait_until="domcontentloaded", timeout=90000)
                        await self._random_delay(2.0, 4.0)
                    except:
                        pass

            if attempt < 1:
                await asyncio.sleep(1)

        # Zeige verfügbare Tabs für Diagnose
        try:
            all_tabs = await self._page.query_selector_all('.pack_menu_list .pack_menu')
            tab_texts = []
            for t in all_tabs:
                try:
                    txt = (await t.inner_text()).strip() or (await t.text_content() or '').strip()
                    tab_texts.append(repr(txt))
                except:
                    pass
            logger.warning(f"   Tab nicht gefunden: {category} | Verfügbare Tabs: {tab_texts}")
        except:
            logger.warning(f"   Tab nicht gefunden: {category}")
        return False

    async def _extract_banners_from_dom(self, category: str) -> int:
        """Extrahiert alle sichtbaren Banner aus dem DOM."""
        count = 0

        try:
            raw_banners = await self._read_banner_elements(self._page)
            logger.debug(f"   Gefundene [data-pack-id] Elemente: {len(raw_banners)}")

            for raw in raw_banners:
                try:
                    if not raw.get('visible'):
                        continue
                    pack_id_str = raw.get('id') or ''
                    if not pack_id_str.isdigit():
                        continue
                    pack_id = int(pack_id_str)

                    # Wenn Banner schon existiert, nur Kategorie hinzufügen
                    if pack_id in self._captured_banners:
                        self._category_banners[category].add(pack_id)
                        count += 1
                        continue

                    # Neuen Banner aus DOM extrahieren
                    banner = self._parse_banner_raw(raw, pack_id, category)
                    if banner:
                        self._captured_banners[pack_id] = banner
                        self._category_banners[category].add(pack_id)
                        count += 1

                except Exception as e:
                    logger.debug(f"   Banner-Element Fehler: {e}")

        except Exception as e:
            logger.warning(f"   DOM-Extraktion Fehler: {e}")

        return count

    def _parse_banner_raw(self, raw: Dict, pack_id: int, category: str) -> Optional[Dict]:
        """Wertet die Rohtexte eines Banner-Elements aus (siehe BANNER_EXTRACT_JS)."""
        banner = {
            'pack_id': pack_id,
            'category': category,
        }

        try:
            if raw.get('title'):
                banner['title'] = raw['title']

            # Preis aus .gacha_pay
            # <div class="gacha_pay"><img ...><div>1.111</div></div>
            price_text = raw.get('price')
            if price_text:
                price_text = price_text.strip().replace('.', '').replace(',', '').replace(' ', '')
                # Extrahiere Zahl
                price_match = re.search(r'(\d+)', price_text)
                if price_match:
                    banner['price'] = int(price_match.group(1))

            # Entries per day aus .limit_detail
            # Deutsch: "Beschränkt auf 10 Mal" oder "Beschränkt auf 10 Mal pro Tag"
            # Japanisch: "1日50回限定" (50 mal pro Tag limitiert)
            # Erst .limit_detail versuchen (spezifischer), dann .buy_limit
            limit_text = raw.get('limit')
            if limit_text is not None:
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
                            logger.debug(f"   Entries-Pattern nicht gefunden für {pack_id}: '{limit_text}'")
            else:
                logger.debug(f"   Kein .limit_detail/.buy_limit für {pack_id}")

            # Packs: API bevorzugen (immer aktuell), DOM als Fallback (CDN-gecacht)
            # WICHTIG: DE-Proxy kann 0 zurückgeben für Banner die nur im JP-Pool verfügbar sind.
            # Wenn API=0 aber DOM>0 → Banner noch aktiv → DOM-Wert verwenden.
            api_item = self._api_pack_data.get(pack_id, {})
            api_pack_count = None
            if api_item:
                pack_fields = ['pack_count', 'pack_remaining', 'remaining_count', 'remaining',
                               'stock', 'packs', 'pack_num', 'pack_stock', 'count']
                for field in pack_fields:
                    val = api_item.get(field)
                    if val is not None:
                        api_pack_count = int(val)
                        logger.debug(f"   [PACK-API] {pack_id}: {val} (Feld: {field})")
                        break
                else:
                    logger.debug(f"   [PACK-API] {pack_id}: unbekannte Felder {list(api_item.keys())}")

                # total_packs aus API
                total_fields = ['total_pack_count', 'total_pack', 'total_count', 'pack_total', 'total', 'pack_limit']
                for field in total_fields:
                    val = api_item.get(field)
                    if val is not None:
                        banner['total_packs'] = int(val)
                        break

            # DOM immer lesen – als Validierung wenn API 0 zurückgibt
            dom_pack_count = None
            dom_total_packs = None
            bar_text = raw.get('bar')
            if bar_text is not None:
                bar_text_clean = re.sub(r'(\d)[.,](\d{3})', r'\1\2', bar_text)
                bar_text_clean = re.sub(r'(\d)[.,](\d{3})', r'\1\2', bar_text_clean)
                packs_match = re.search(r'(\d+)\s*/\s*(\d+)', bar_text_clean)
                if packs_match:
                    dom_pack_count = int(packs_match.group(1))
                    dom_total_packs = int(packs_match.group(2))
                else:
                    logger.debug(f"   [PACK-DOM] Pattern nicht gefunden für {pack_id}: '{bar_text_clean}'")
            else:
                logger.debug(f"   [PACK-DOM] Kein .gacha_bar für {pack_id}")

            # Priorität: API>0 gewinnt; wenn API=0/fehlend, DOM als Fallback
            if api_pack_count is not None and api_pack_count > 0:
                banner['current_packs'] = api_pack_count
            elif dom_pack_count is not None and dom_pack_count > 0:
                banner['current_packs'] = dom_pack_count
                if dom_total_packs is not None and 'total_packs' not in banner:
                    banner['total_packs'] = dom_total_packs
                logger.debug(f"   [PACK] {pack_id}: DOM-Fallback ({dom_pack_count}) da API={api_pack_count}")
            elif api_pack_count is not None:
                banner['current_packs'] = api_pack_count  # Beide zeigen 0 – wirklich leer

            # End-Datum aus .end-date
            # "Verkauf bis 2026/01/21 JST"
            if raw.get('end') is not None:
                banner['sale_end_date'] = raw['end'].strip()

            # Bild-URL aus img.current
            if raw.get('has_img'):
                img_src = raw.get('img')
                if img_src:
                    if not img_src.startswith('http'):
                        img_src = f"{self.base_url}{img_src}"
                    # Entferne Query-Parameter für saubere URL
                    img_src = img_src.split('?')[0]
                    banner['image_url'] = img_src

            # Prüfe ob Banner aktiv ist (kein Countdown = aktiv)
            # Wenn "Bis zum Verkaufsbeginn" sichtbar ist oder Timer > 0, ist der Banner noch nicht aktiv
            if raw.get('has_countdown'):
                # Prüfe auf Timer-Wert
                if raw.get('timer') is not None:
                    timer_text = raw['timer'].strip()
                    # Wenn Timer nicht leer und nicht "00.00.00" oder ähnlich
                    if timer_text and not all(c in '0.: ' for c in timer_text):
                        logger.debug(f"   Banner {pack_id} noch nicht aktiv (Timer: {timer_text})")
                        return None

                # Fallback: Prüfe auf "Verkaufsbeginn" Text
                countdown_text = raw.get('countdown') or ''
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
