"""Store-Packs (gtchaxonline.com/store) - wie normale Banner (Hits, Medaillen, Abhaken), aber nichts in Discord.

Die Packs liegen in der Tabelle banners mit is_active = 2 (siehe database/db.py): Threads, Top 10, "nicht gefunden"-
Zähler und alle anderen Discord-Funktionen sehen sie nicht. Der Lauf holt alle 5 Minuten die Pack-Zahlen (damit der
Pack-Verlauf stimmt), lädt fehlende Kartenpools nach und beendet Packs, die zweimal in Folge fehlen.
"""

import json
import time

from bot.common import *  # noqa: F401,F403
from utils.banner_info import banner_conditions, is_upcoming, jst_timestamp, shipping_stats
from utils.store_client import StoreClient

DISCOVER_EVERY_SECONDS = 3600
MISSING_BEFORE_END = 2


class StoreMixin:
    def _store_client_get(self) -> StoreClient:
        if not getattr(self, "_store_client", None):
            from config import SCRAPER_PROXY
            self._store_client = StoreClient(BASE_URL, SCRAPER_PROXY, fresh_browser=False)
        return self._store_client

    async def _scrape_store(self):
        """Ein Lauf: Packs holen, speichern, fehlende Kartenpools laden, beendete Packs abschließen."""
        if getattr(self, "_store_running", False):
            return
        self._store_running = True
        try:
            client = self._store_client_get()
            types = json.loads(await self.db.get_meta("store_card_types") or "[]")
            discovered = float(await self.db.get_meta("store_discovered_at") or 0)
            discover = not types or time.time() - discovered > DISCOVER_EVERY_SECONDS
            items, found, ok = await client.fetch_store(types, discover)
            if not ok:
                logger.debug("[STORE] Abruf ohne Daten - nächster Lauf")
                return
            if discover:
                await self.db.set_meta("store_card_types", json.dumps(sorted(set(types) | found)))
                await self.db.set_meta("store_discovered_at", str(time.time()))
            await self._save_store_items(items)
            await self._load_store_pools(client)
            # gezogene Hits erkennen wie bei normalen Bannern (Versand/Umwandlung) - ohne Discord
            await self._detect_pulled_hits({pid: it for pid, it in items.items() if _int(it.get('pack_count')) > 0})
        except Exception as e:
            logger.warning(f"[STORE] Lauf fehlgeschlagen: {type(e).__name__}: {e}")
        finally:
            self._store_running = False

    async def _save_store_items(self, items: dict):
        known = await self.db.get_store_banners()
        present = []
        for pid, item in items.items():
            packs = _int(item.get('pack_count'))
            if packs <= 0:      # ausverkauft (oder Fehlwert der Seite): zählt wie "fehlt"
                continue
            image = (item.get('image') or [None])[0]
            is_new = await self.db.upsert_store_pack(
                pid, item.get('name'), _int(item.get('point')) or None, packs,
                _int(item.get('total_pack_count')) or None, _int(item.get('max_buy_count')) or None,
                item.get('end_date'), f"{BASE_URL}{image.split('?')[0]}" if image else None,
                f"{BASE_URL}/store-pack-detail?packId={pid}")
            await self.db.set_start(pid, jst_timestamp(item.get('start_date')), announced=not is_upcoming(item))
            await self.db.update_conditions(pid, banner_conditions(item))
            await self.db.update_site_stats(pid, shipping_stats(item))
            if item.get('total_kangen') is not None:
                await self.db.update_converted(pid, _int(item.get('total_kangen')))
            present.append(pid)
            if is_new:
                logger.info(f"[STORE] Neuer Pack {pid}: {item.get('name')} ({_int(item.get('point'))} Coins)")
        if present:
            await self.db.batch_reset_not_found_count(present)
        gone = [pid for pid in known if pid not in present]
        if gone:
            for pid in await self.db.batch_increment_not_found_count(gone, threshold=MISSING_BEFORE_END):
                await self.db.mark_banner_inactive(pid)
                logger.info(f"[STORE] Pack {pid} beendet")

    async def _load_store_pools(self, client: StoreClient):
        missing = [pid for pid, row in (await self.db.get_store_banners()).items() if not row.get('card_pool')]
        if not missing:
            return
        for pid, pool in (await client.fetch_store_pools(missing[:10])).items():
            await self.db.save_card_pool(pid, pool)
            logger.info(f"[STORE] {pid}: Kartenpool mit {pool.get('total_count', 0)} Packs gespeichert")
