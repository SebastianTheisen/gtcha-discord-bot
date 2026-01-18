"""
GTCHA Webseiten-Scraper mit Playwright - VERBESSERTE VERSION

Änderungen:
- Bessere Selektoren für die SPA
- Debug-Screenshots
- Mehr Logging
- Robustere Tab-Erkennung
"""

import asyncio
import re
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
        self._browser = await self._playwright.chromium.launch(
            headless=self.headless,
            args=["--disable-dev-shm-usage", "--no-sandbox", "--disable-gpu", "--disable-setuid-sandbox"]
        )
        self._page = await self._browser.new_page(
            viewport={"width": 1920, "height": 1080},
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
        )
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
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            path = self.debug_dir / f"{timestamp}_{name}.png"
            await self._page.screenshot(path=str(path), full_page=True)
            logger.debug(f"📸 Debug-Screenshot: {path}")
        except Exception as e:
            logger.warning(f"Screenshot fehlgeschlagen: {e}")

    async def scrape_all_banners(self) -> List[ScrapedBanner]:
        all_banners = []

        logger.info(f"📄 Lade Hauptseite: {self.base_url}")

        try:
            await self._page.goto(self.base_url, wait_until="networkidle", timeout=60000)
            logger.info("✅ Seite geladen")

            await asyncio.sleep(5)

            await self._save_debug_screenshot("01_hauptseite")

            page_content = await self._page.content()
            if "JavaScript" in page_content and "enable" in page_content.lower():
                logger.error("❌ JavaScript nicht geladen! Seite zeigt JS-Warnung.")
                return []

            logger.debug(f"📝 Seiten-Länge: {len(page_content)} Zeichen")

            await self._log_page_structure()

        except Exception as e:
            logger.error(f"❌ Fehler beim Laden der Hauptseite: {e}")
            await self._save_debug_screenshot("error_hauptseite")
            return []

        for category in CATEGORIES:
            try:
                logger.info(f"🔍 Scrape Kategorie: {category}")
                banners = await self._scrape_category(category)
                all_banners.extend(banners)
                logger.info(f"   → {len(banners)} Banner gefunden")
            except Exception as e:
                logger.error(f"❌ Fehler bei {category}: {e}")
                await self._save_debug_screenshot(f"error_{category.replace(' ', '_')}")

        logger.info(f"✅ Scraping abgeschlossen: {len(all_banners)} Banner total")
        return all_banners

    async def _log_page_structure(self):
        """Loggt die Seitenstruktur für Debug-Zwecke."""
        try:
            tab_info = await self._page.evaluate("""
                () => {
                    const info = {
                        buttons: [],
                        tabs: [],
                        clickables: [],
                        textContent: []
                    };

                    document.querySelectorAll('button').forEach(el => {
                        info.buttons.push(el.innerText.substring(0, 50));
                    });

                    document.querySelectorAll('[class*="tab"], [role="tab"]').forEach(el => {
                        info.tabs.push(el.innerText.substring(0, 50));
                    });

                    const categories = ['Bonus', 'MIX', 'Yu-Gi-Oh', 'Pokémon', 'Pokemon', 'Weiss', 'One piece', 'Hobby'];
                    categories.forEach(cat => {
                        const els = document.querySelectorAll(`*:not(script):not(style)`);
                        els.forEach(el => {
                            if (el.innerText && el.innerText.includes(cat) && el.innerText.length < 100) {
                                info.clickables.push({
                                    tag: el.tagName,
                                    text: el.innerText.substring(0, 50),
                                    classes: el.className.substring(0, 100)
                                });
                            }
                        });
                    });

                    const body = document.body.innerText;
                    if (body.includes('Rückstand')) info.textContent.push('Rückstand gefunden');
                    if (body.includes('kaufen')) info.textContent.push('kaufen gefunden');
                    if (body.includes('pro Tag')) info.textContent.push('pro Tag gefunden');
                    if (body.includes('Verkauf bis')) info.textContent.push('Verkauf bis gefunden');

                    return info;
                }
            """)

            logger.debug(f"📊 Seiten-Struktur:")
            logger.debug(f"   Buttons: {tab_info.get('buttons', [])[:10]}")
            logger.debug(f"   Tabs: {tab_info.get('tabs', [])[:10]}")
            logger.debug(f"   Kategorie-Elemente: {len(tab_info.get('clickables', []))}")
            logger.debug(f"   Text-Inhalte: {tab_info.get('textContent', [])}")

            if tab_info.get('clickables'):
                for item in tab_info['clickables'][:5]:
                    logger.debug(f"      → {item}")

        except Exception as e:
            logger.warning(f"Struktur-Analyse fehlgeschlagen: {e}")

    async def _scrape_category(self, category: str) -> List[ScrapedBanner]:
        """Scrapet alle Banner einer Kategorie."""

        clicked = await self._click_category_tab(category)
        if not clicked:
            logger.warning(f"   ⚠️ Tab '{category}' konnte nicht geklickt werden")

        await asyncio.sleep(3)

        await self._scroll_to_load_all()

        await self._save_debug_screenshot(f"02_kategorie_{category.replace(' ', '_').replace('!', '')}")

        return await self._extract_banners_from_page(category)

    async def _click_category_tab(self, category: str) -> bool:
        """Klickt auf einen Kategorie-Tab."""

        category_variants = [category]
        if category == "Pokémon":
            category_variants.extend(["Pokemon", "POKEMON", "Pokémon"])
        elif category == "Yu-Gi-Oh!":
            category_variants.extend(["Yu-Gi-Oh", "YuGiOh", "Yugioh", "Yu Gi Oh"])
        elif category == "One piece":
            category_variants.extend(["One Piece", "ONE PIECE", "OnePiece", "Onepiece"])
        elif category == "Weiss Schwarz":
            category_variants.extend(["Weiss", "WEISS SCHWARZ", "WeissSchwarz"])

        for variant in category_variants:
            try:
                element = await self._page.query_selector(f'text="{variant}"')
                if element:
                    await element.click()
                    logger.debug(f"   ✅ Tab geklickt (text=): {variant}")
                    return True
            except:
                pass

            try:
                element = await self._page.query_selector(f'text={variant}')
                if element:
                    await element.click()
                    logger.debug(f"   ✅ Tab geklickt (text contains): {variant}")
                    return True
            except:
                pass

            try:
                elements = await self._page.query_selector_all(f'//*[contains(text(), "{variant}")]')
                for el in elements:
                    tag = await el.evaluate("el => el.tagName")
                    if tag.lower() in ['button', 'div', 'span', 'a', 'li']:
                        await el.click()
                        logger.debug(f"   ✅ Tab geklickt (xpath): {variant}")
                        return True
            except:
                pass

            try:
                clicked = await self._page.evaluate(f"""
                    () => {{
                        const elements = document.querySelectorAll('*');
                        for (const el of elements) {{
                            if (el.innerText && el.innerText.trim() === '{variant}') {{
                                el.click();
                                return true;
                            }}
                        }}
                        for (const el of elements) {{
                            if (el.innerText && el.innerText.includes('{variant}') && el.innerText.length < 50) {{
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

        logger.debug(f"   ❌ Kein Tab gefunden für: {category}")
        return False

    async def _scroll_to_load_all(self):
        """Scrollt um lazy-loaded Content zu laden."""
        try:
            for i in range(8):
                await self._page.evaluate(f"window.scrollBy(0, 300)")
                await asyncio.sleep(0.4)
            await self._page.evaluate("window.scrollTo(0, 0)")
            await asyncio.sleep(0.5)
        except:
            pass

    async def _extract_banners_from_page(self, category: str) -> List[ScrapedBanner]:
        """Extrahiert Banner mit verbesserter Logik."""
        banners = []

        banner_data = await self._page.evaluate("""
            () => {
                const banners = [];
                const processedIds = new Set();

                // METHODE 1: Suche nach Links mit packId
                document.querySelectorAll('a[href*="packId"]').forEach(link => {
                    try {
                        const match = link.href.match(/packId=(\\d+)/);
                        if (match && !processedIds.has(match[1])) {
                            processedIds.add(match[1]);

                            let container = link;
                            for (let i = 0; i < 10; i++) {
                                if (container.parentElement) {
                                    container = container.parentElement;
                                    const text = container.innerText || '';
                                    if (text.includes('Rückstand') || text.includes('kaufen')) {
                                        break;
                                    }
                                }
                            }

                            const text = container.innerText || '';
                            const img = container.querySelector('img');

                            const packMatch = text.match(/Rückstand\\s*(\\d+)\\s*\\/\\s*(\\d+)/i);
                            const priceMatch = text.match(/(\\d{1,3}(?:[.,]\\d{3})*)\\s*$/m);
                            const entriesMatch = text.match(/(\\d+)\\s*(?:Mal\\s*)?pro\\s*Tag/i);
                            const dateMatch = text.match(/Verkauf bis\\s*([\\d\\/]+(?:\\s*JST)?)/i);

                            let price = null;
                            const priceMatch2 = text.match(/(\\d{1,3}(?:[.,]\\d{3})*|\\d+)(?:\\s*kaufen|\\s*$)/m);
                            if (priceMatch2) {
                                price = priceMatch2[1].replace(/[.,]/g, '');
                            }

                            banners.push({
                                packId: match[1],
                                currentPacks: packMatch ? packMatch[1] : null,
                                totalPacks: packMatch ? packMatch[2] : null,
                                entriesPerDay: entriesMatch ? entriesMatch[1] : null,
                                saleEndDate: dateMatch ? dateMatch[1] : null,
                                price: price,
                                imageUrl: img ? img.src : null,
                                rawText: text.substring(0, 500)
                            });
                        }
                    } catch (e) {}
                });

                // METHODE 2: Suche nach Elementen mit "Rückstand"
                if (banners.length === 0) {
                    const allElements = document.querySelectorAll('*');
                    allElements.forEach(el => {
                        try {
                            const text = el.innerText || '';
                            if (text.includes('Rückstand') && text.includes('kaufen') && text.length < 1000) {
                                const links = el.querySelectorAll('a[href*="packId"]');
                                links.forEach(link => {
                                    const match = link.href.match(/packId=(\\d+)/);
                                    if (match && !processedIds.has(match[1])) {
                                        processedIds.add(match[1]);

                                        const packMatch = text.match(/Rückstand\\s*(\\d+)\\s*\\/\\s*(\\d+)/i);
                                        const entriesMatch = text.match(/(\\d+)\\s*(?:Mal\\s*)?pro\\s*Tag/i);
                                        const img = el.querySelector('img');

                                        banners.push({
                                            packId: match[1],
                                            currentPacks: packMatch ? packMatch[1] : null,
                                            totalPacks: packMatch ? packMatch[2] : null,
                                            entriesPerDay: entriesMatch ? entriesMatch[1] : null,
                                            imageUrl: img ? img.src : null,
                                            rawText: text.substring(0, 500)
                                        });
                                    }
                                });
                            }
                        } catch (e) {}
                    });
                }

                // METHODE 3: Suche über onclick Handler
                if (banners.length === 0) {
                    document.querySelectorAll('[onclick*="packId"], [data-pack-id]').forEach(el => {
                        try {
                            const onclick = el.getAttribute('onclick') || '';
                            const dataId = el.getAttribute('data-pack-id');
                            const match = onclick.match(/packId[=:]\\s*(\\d+)/) || (dataId ? [null, dataId] : null);

                            if (match && !processedIds.has(match[1])) {
                                processedIds.add(match[1]);
                                const text = el.innerText || '';
                                const packMatch = text.match(/Rückstand\\s*(\\d+)\\s*\\/\\s*(\\d+)/i);

                                banners.push({
                                    packId: match[1],
                                    currentPacks: packMatch ? packMatch[1] : null,
                                    totalPacks: packMatch ? packMatch[2] : null,
                                    rawText: text.substring(0, 500)
                                });
                            }
                        } catch (e) {}
                    });
                }

                return banners;
            }
        """)

        logger.debug(f"   📊 Rohdaten: {len(banner_data)} gefunden")

        for data in banner_data:
            try:
                pack_id = int(data.get("packId", 0))
                if pack_id == 0:
                    continue

                if len(banners) < 2:
                    logger.debug(f"   📝 Banner {pack_id} Raw: {data.get('rawText', '')[:200]}")

                banner = ScrapedBanner(
                    pack_id=pack_id,
                    category=category,
                    price_coins=int(data["price"]) if data.get("price") else None,
                    current_packs=int(data["currentPacks"]) if data.get("currentPacks") else None,
                    total_packs=int(data["totalPacks"]) if data.get("totalPacks") else None,
                    entries_per_day=int(data["entriesPerDay"]) if data.get("entriesPerDay") else None,
                    sale_end_date=data.get("saleEndDate"),
                    image_url=data.get("imageUrl"),
                    detail_page_url=f"{self.base_url}/pack-detail?packId={pack_id}",
                )
                banners.append(banner)
                logger.debug(f"   ✅ Banner: {banner}")

            except Exception as e:
                logger.warning(f"   ⚠️ Parse-Fehler: {e}")

        return banners

    async def scrape_banner_details(self, pack_id: int) -> Tuple[Optional[str], Optional[bytes]]:
        """Scrapet Details von einer Banner Detail-Seite."""
        detail_url = f"{self.base_url}/pack-detail?packId={pack_id}"

        try:
            logger.debug(f"   📄 Lade Detail-Seite: {detail_url}")
            await self._page.goto(detail_url, wait_until="networkidle", timeout=30000)
            await asyncio.sleep(3)

            screenshot = await self._page.screenshot(full_page=False)

            best_hit = await self._page.evaluate("""
                () => {
                    const selectors = [
                        '[class*="hit"]',
                        '[class*="prize"]',
                        '[class*="reward"]',
                        '[class*="card-item"]',
                        '[class*="item"]'
                    ];

                    for (const sel of selectors) {
                        const els = document.querySelectorAll(sel);
                        if (els.length > 0) {
                            const text = els[0].innerText || els[0].textContent || '';
                            if (text.trim()) {
                                return text.split('\\n')[0].trim().substring(0, 100);
                            }
                        }
                    }

                    const body = document.body.innerText;
                    const patterns = [
                        /(PSA\\s*\\d+[^\\n]{0,50})/i,
                        /(BGS\\s*\\d+[^\\n]{0,50})/i,
                        /([A-Z][a-z]+\\s+(?:EX|GX|V|VMAX|VSTAR)[^\\n]{0,30})/
                    ];

                    for (const pattern of patterns) {
                        const match = body.match(pattern);
                        if (match) return match[1].trim();
                    }

                    return null;
                }
            """)

            logger.debug(f"   🏆 Best Hit: {best_hit}")
            return best_hit, screenshot

        except Exception as e:
            logger.error(f"   ❌ Detail-Seite Fehler: {e}")
            return None, None

    async def download_image(self, url: str) -> Optional[bytes]:
        """Lädt ein Bild herunter."""
        try:
            response = await self._page.request.get(url)
            if response.ok:
                return await response.body()
        except Exception as e:
            logger.warning(f"   Bild-Download fehlgeschlagen: {e}")
        return None
