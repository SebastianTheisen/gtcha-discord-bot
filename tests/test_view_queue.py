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
