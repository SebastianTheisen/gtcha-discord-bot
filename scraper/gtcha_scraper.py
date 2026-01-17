import asyncio
from pathlib import Path
from typing import List, Optional, Tuple
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
            args=["--disable-dev-shm-usage", "--no-sandbox", "--disable-gpu"]
        )
        self._page = await self._browser.new_page(viewport={"width": 1280, "height": 900})
        logger.info("Browser gestartet")

    async def close(self):
        if self._browser:
            await self._browser.close()
        if self._playwright:
            await self._playwright.stop()

    async def scrape_all_banners(self) -> List[ScrapedBanner]:
        all_banners = []
        logger.info(f"Lade Hauptseite: {self.base_url}")
        await self._page.goto(self.base_url, wait_until="networkidle", timeout=30000)
        await asyncio.sleep(2)

        for category in CATEGORIES:
            try:
                logger.info(f"Scrape Kategorie: {category}")
                banners = await self._scrape_category(category)
                all_banners.extend(banners)
                logger.info(f"   -> {len(banners)} Banner gefunden")
            except Exception as e:
                logger.error(f"Fehler bei {category}: {e}")

        logger.info(f"Scraping abgeschlossen: {len(all_banners)} Banner")
        return all_banners

    async def _scrape_category(self, category: str) -> List[ScrapedBanner]:
        await self._click_category_tab(category)
        await asyncio.sleep(1.5)
        await self._scroll_to_load_all()
        return await self._extract_banners_from_page(category)

    async def _click_category_tab(self, category: str):
        try:
            selectors = [
                f'text="{category}"',
                f'button:has-text("{category}")',
                f'span:has-text("{category}")'
            ]
            for selector in selectors:
                try:
                    element = await self._page.wait_for_selector(selector, timeout=3000, state="visible")
                    if element:
                        await element.click()
                        return
                except:
                    continue
        except Exception as e:
            logger.warning(f"Tab '{category}' nicht gefunden: {e}")

    async def _scroll_to_load_all(self):
        for _ in range(5):
            await self._page.evaluate("window.scrollBy(0, 500)")
            await asyncio.sleep(0.3)
        await self._page.evaluate("window.scrollTo(0, 0)")

    async def _extract_banners_from_page(self, category: str) -> List[ScrapedBanner]:
        banners = []
        banner_data = await self._page.evaluate("""
            () => {
                const banners = [];
                const allDivs = document.querySelectorAll('div');
                const cards = Array.from(allDivs).filter(div => {
                    const text = div.innerText || '';
                    return text.includes('Rückstand') && text.includes('kaufen');
                });

                cards.forEach(card => {
                    try {
                        const text = card.innerText || '';
                        let packId = null;
                        const links = card.querySelectorAll('a[href*="packId"]');
                        for (const link of links) {
                            const match = link.href.match(/packId=(\\d+)/);
                            if (match) { packId = match[1]; break; }
                        }

                        const priceEl = card.querySelector('[class*="price"], [class*="coin"]');
                        let price = null;
                        if (priceEl) {
                            price = priceEl.innerText.replace(/[^\\d]/g, '');
                        }

                        const packMatch = text.match(/Rückstand\\s*(\\d+)\\s*\\/\\s*(\\d+)/i);
                        const entriesMatch = text.match(/(\\d+)\\s*(?:Mal\\s*)?pro\\s*Tag/i);
                        const dateMatch = text.match(/Verkauf bis\\s*([\\d\\/]+\\s*JST)/i);
                        const img = card.querySelector('img');

                        if (packId) {
                            banners.push({
                                packId, price,
                                currentPacks: packMatch ? packMatch[1] : null,
                                totalPacks: packMatch ? packMatch[2] : null,
                                entriesPerDay: entriesMatch ? entriesMatch[1] : null,
                                saleEndDate: dateMatch ? dateMatch[1] : null,
                                imageUrl: img ? img.src : null,
                            });
                        }
                    } catch (e) {}
                });
                return banners;
            }
        """)

        for data in banner_data:
            try:
                banners.append(ScrapedBanner(
                    pack_id=int(data.get("packId", 0)),
                    category=category,
                    price_coins=int(data["price"]) if data.get("price") else None,
                    current_packs=int(data["currentPacks"]) if data.get("currentPacks") else None,
                    total_packs=int(data["totalPacks"]) if data.get("totalPacks") else None,
                    entries_per_day=int(data["entriesPerDay"]) if data.get("entriesPerDay") else None,
                    sale_end_date=data.get("saleEndDate"),
                    image_url=data.get("imageUrl"),
                    detail_page_url=f"{self.base_url}/pack-detail?packId={data.get('packId')}",
                ))
            except Exception as e:
                logger.warning(f"Parse error: {e}")
        return banners

    async def scrape_banner_details(self, pack_id: int) -> Tuple[Optional[str], Optional[bytes]]:
        try:
            await self._page.goto(
                f"{self.base_url}/pack-detail?packId={pack_id}",
                wait_until="networkidle",
                timeout=30000
            )
            await asyncio.sleep(2)

            best_hit = await self._page.evaluate("""
                () => {
                    const hits = document.querySelectorAll('[class*="hit"], [class*="prize"], [class*="reward"]');
                    if (hits.length > 0) return hits[0].innerText.split('\\n')[0].trim().substring(0, 100);
                    const text = document.body.innerText;
                    const match = text.match(/(PSA\\s*\\d+\\s*[^\\n]+)/i);
                    return match ? match[1].trim() : null;
                }
            """)
            screenshot = await self._page.screenshot(full_page=False)
            return best_hit, screenshot
        except Exception as e:
            logger.error(f"Detail-Seite {pack_id} Fehler: {e}")
            return None, None

    async def download_image(self, url: str) -> Optional[bytes]:
        try:
            response = await self._page.request.get(url)
            return await response.body() if response.ok else None
        except:
            return None
