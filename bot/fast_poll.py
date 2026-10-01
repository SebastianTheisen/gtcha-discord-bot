"""Schneller Abfrager: holt pack/list alle FAST_POLL_SECONDS und postet Änderungen sofort.

Läuft als eigener Hintergrund-Task neben dem normalen Scrape. Er übernimmt das Zeitkritische
(Pack-Updates, Hit-Erkennung, neue Banner, Verkaufsstart); Kartenlisten, Hit-Listen, Titel,
Löschen und der Tab-Durchlauf bleiben beim normalen Scrape.
"""

from bot.common import *  # noqa: F401,F403
from config import SCRAPER_PROXY
from utils.pack_list_client import PackListClient

FAST_POLL_SECONDS = int(os.getenv("FAST_POLL_SECONDS") or "15")


class FastPollMixin:
    def _start_fast_poll(self):
        if FAST_POLL_SECONDS <= 0:
            logger.info("Schneller Abfrager deaktiviert (FAST_POLL_SECONDS=0)")
            return
        task = getattr(self, '_fast_poll_task', None)
        if task and not task.done():
            return  # on_ready kommt bei jedem Reconnect erneut
        self._pack_list_client = PackListClient(
            BASE_URL, SCRAPER_PROXY, fresh_browser=os.getenv("FAST_POLL_FRESH_BROWSER", "true").lower() == "true")
        self._fast_poll_task = asyncio.create_task(self._fast_poll_loop())
        logger.info(f"Schneller Abfrager: pack/list alle {FAST_POLL_SECONDS} Sekunden")

    async def _fast_poll_loop(self):
        failures = 0
        self._fast_stats = {"ok": 0, "fail": 0, "skip": 0, "changes": 0}
        last_report = asyncio.get_running_loop().time()
        while True:
            started = asyncio.get_running_loop().time()
            if self._scrape_lock.locked():  # normaler Scrape läuft gerade -> diese Runde auslassen
                self._fast_stats["skip"] += 1
            else:
                try:
                    async with self._scrape_lock:
                        ok = await asyncio.wait_for(self._fast_poll_tick(), timeout=60)
                except Exception as e:
                    ok = False
                    logger.warning(f"[SCHNELL] Fehler: {type(e).__name__}: {e}")
                if ok:
                    if self._fast_stats["ok"] == 0:
                        logger.info("[SCHNELL] Erste Abfrage erfolgreich")
                    self._fast_stats["ok"] += 1
                    failures = 0
                else:
                    self._fast_stats["fail"] += 1
                    failures += 1
                    if failures in (3, 20, 100):
                        logger.warning(f"[SCHNELL] {failures} Abfragen in Folge ohne Daten")
            if started - last_report >= 300:
                st = self._fast_stats
                logger.info(f"[SCHNELL] letzte 5 Min: {st['ok']} ok, {st['fail']} fehlgeschlagen, "
                            f"{st['skip']} ausgesetzt, {st['changes']} Pack-Änderungen")
                self._fast_stats = {"ok": 0, "fail": 0, "skip": 0, "changes": 0}
                last_report = started
            elapsed = asyncio.get_running_loop().time() - started
            await asyncio.sleep(max(1.0, FAST_POLL_SECONDS - elapsed))

    async def _fast_poll_tick(self) -> bool:
        items = await self._pack_list_client.fetch()
        if not items:
            logger.debug("[SCHNELL] pack/list ohne Daten")
            return False
        await self._create_banners_from_api(items)
        await self._announce_started_banners(items)

        rows = await self.db.get_active_banners()
        semaphore = asyncio.Semaphore(5)
        self._rises_ignored = 0
        updates = []
        for banner in await self._banners_from_api(items):
            row = rows.get(banner.pack_id)
            if row and (banner.current_packs or 0) > 0 and banner.current_packs != row.get('current_packs'):
                updates.append(self._process_banner_update(banner, row, semaphore))
        if updates:
            self._fast_stats["changes"] += len(updates)
            logger.info(f"[SCHNELL] {len(updates)} Pack-Änderung(en)")
            await asyncio.gather(*updates, return_exceptions=True)
            await self._check_pool_switch(len(rows))

        await self._detect_pulled_hits(items)
        await self._apply_site_data(items)
        return True
