"""Discord-Ansichten (Startbeitrag, Hit-Listen) laufen über eine Warteschlange im Hintergrund, nicht im Scrape."""

import asyncio

from bot.hits import HitsMixin


class Bot(HitsMixin):
    def __init__(self):
        self.calls = []

    async def _refresh_pool_views(self, pack_id, initial_pool=False, embed=True, force=False):
        await asyncio.sleep(0)
        self.calls.append((pack_id, initial_pool, embed, force))


def test_queue_merges_and_runs_in_background():
    async def run():
        bot = Bot()
        bot._queue_pool_views(1, embed=False)
        bot._queue_pool_views(2, initial_pool=True)
        bot._queue_pool_views(1, embed=True)   # zusammengefasst: Banner 1 nur einmal, mit Startbeitrag
        assert bot.calls == []                 # der Aufrufer wartet nicht
        await bot._view_worker
        return bot.calls

    assert asyncio.run(run()) == [(1, False, True, False), (2, True, True, False)]


def test_hit_detection_per_banner_in_parallel_with_newest_items():
    """Je Banner ein Task: ein langsamer Banner hält die anderen nicht auf; neuester Stand gilt, nie doppelt."""
    class HitBot(HitsMixin):
        def __init__(self):
            self.runs = []
            self.slow = asyncio.Event()

        async def _detect_pulled_hits(self, items):
            (pid, item), = items.items()
            if pid == 1 and item == "neu":
                await self.slow.wait()          # Banner 1 rechnet lange
            self.runs.append((pid, item))

    async def run():
        bot = HitBot()
        bot._queue_hit_detection({1: "alt", 2: "b"})
        bot._queue_hit_detection({1: "neu"})   # noch nicht gestartet: neuester Stand, nur einmal
        assert bot.runs == []                   # Abruf/Scrape wartet nicht
        for _ in range(5):
            await asyncio.sleep(0)
        assert bot.runs == [(2, "b")]           # Banner 2 ist fertig, obwohl Banner 1 noch rechnet
        st = bot._worker_status()["hits"]
        assert st["running"]["banners"] == 1 and st["running"]["label"] == "1"
        bot._queue_hit_detection({1: "danach"})   # läuft gerade: danach erneut mit neuestem Stand
        bot.slow.set()
        await bot._wait_hit_detection()
        return bot.runs

    assert asyncio.run(run()) == [(2, "b"), (1, "neu"), (1, "danach")]
