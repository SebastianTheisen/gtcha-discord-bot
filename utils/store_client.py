"""Abruf der Store-Packs (gtchaxonline.com/store) über denselben Browser-Aufbau wie der Bot (Tor, ohne Login).

Die Store-Seite holt ihre Packs selbst über /api/user/pack/list/<Kartenart> (z. B. 13 = Schmuck), Karten und Preise
im selben Format wie bei den normalen Bannern. Welche Kartenarten es gibt, lernt der Abruf aus den Anfragen der Seite
selbst (stündlich); dazwischen werden nur die bekannten Kartenarten direkt abgefragt. Es werden keine Adressen geraten.
"""

import re
from typing import Dict, List, Optional, Set, Tuple

from loguru import logger

from scraper.gtcha_scraper import FETCH_CARD_LIST_JS
from utils.card_pool import summarize_cards
from utils.pack_list_client import PackListClient

FETCH_STORE_LIST_JS = """async (t) => {
    const r = await fetch(`/api/user/pack/list/${t}`, {headers: {Accept: 'application/json'}});
    try { return await r.json(); } catch (e) { return null; }
}"""
LIST_URL = re.compile(r"/api/user/pack/list/(\d+)")


class StoreClient(PackListClient):
    async def _page(self):
        """Neuer Browser-Kontext mit Sitzung (wie beim normalen Abruf)."""
        await self._ensure_browser()
        context = await self._new_context()
        page = await context.new_page()
        await page.goto(f"{self.base_url}/api/user/point", wait_until="domcontentloaded", timeout=45000)
        return context, page

    async def fetch_store(self, card_types: List[int], discover: bool) -> Tuple[Dict[int, dict], Set[int], bool]:
        """(Packs je ID, gefundene Kartenarten, Abruf ok). Mit discover=True wird die Store-Seite geladen und
        mitgelesen, welche Listen sie selbst abruft."""
        context, page = await self._page()
        items: Dict[int, dict] = {}
        types: Set[int] = set()
        ok = False
        try:
            captured = []

            async def on_response(response):
                m = LIST_URL.search(response.url)
                if m and response.status == 200:
                    try:
                        captured.append((int(m.group(1)), await response.json()))
                    except Exception:
                        pass

            if discover:
                page.on("response", on_response)
                try:
                    await page.goto(f"{self.base_url}/store", wait_until="networkidle", timeout=60000)
                except Exception as e:   # Seite lädt weiter Dinge nach - was bis hierher kam, reicht
                    logger.debug(f"[STORE] Store-Seite: {type(e).__name__}")
                await page.wait_for_timeout(1500)
            for card_type, data in captured:
                types.add(card_type)
                for it in (data or {}).get("list") or []:
                    if it.get("id"):
                        items[int(it["id"])] = it
                ok = True
            for card_type in sorted(set(card_types) - types):
                data = await page.evaluate(FETCH_STORE_LIST_JS, card_type)
                if isinstance(data, dict) and isinstance(data.get("list"), list):
                    types.add(card_type)
                    for it in data["list"]:
                        if it.get("id"):
                            items[int(it["id"])] = it
                    ok = True
        finally:
            await context.close()
        return items, types, ok

    async def fetch_store_pools(self, pack_ids: List[int]) -> Dict[int, dict]:
        """Kartenpools (alle Seiten) der angegebenen Store-Packs."""
        pools: Dict[int, dict] = {}
        if not pack_ids:
            return pools
        context, page = await self._page()
        try:
            for pid in pack_ids:
                cards: Optional[list] = await page.evaluate(FETCH_CARD_LIST_JS, pid)
                if cards is None:
                    logger.warning(f"[STORE] {pid}: Kartenliste nicht abrufbar - nächster Lauf versucht es erneut")
                    continue
                pool = summarize_cards(cards)
                pools[pid] = pool or {"total_count": 0, "total_value": 0, "hits_total": 0, "top": []}
        finally:
            await context.close()
        return pools
