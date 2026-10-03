"""Leer gezogene Banner: "0 Packs" wird übernommen, wenn vorher nur noch wenige übrig waren."""

import asyncio

import pytest


@pytest.fixture(autouse=True)
def env(monkeypatch):
    monkeypatch.setenv("DISCORD_TOKEN", "x")
    monkeypatch.setenv("GUILD_ID", "1")


def test_sold_out_rule():
    from bot.common import sold_out
    assert sold_out(2, 0) and sold_out(20, 0)
    assert not sold_out(300, 0)       # mitten im Banner plötzlich 0: Fehlwert der Seite
    assert not sold_out(None, 0) and not sold_out(0, 0) and not sold_out(2, 1)


def test_fast_poll_takes_over_zero_only_after_few_packs():
    from bot.fast_poll import FastPollMixin
    from bot.scraping import ScrapingMixin

    rows = {24152: {"pack_id": 24152, "category": "One Piece", "current_packs": 2, "total_packs": 500},
            24200: {"pack_id": 24200, "category": "MIX", "current_packs": 300, "total_packs": 500}}
    items = {24152: {"pack_count": 0, "point": 1200}, 24200: {"pack_count": 0, "point": 500}}

    class DB:
        async def get_active_banners(self):
            return rows

    class Client:
        async def fetch(self):
            return items

    class Bot(FastPollMixin, ScrapingMixin):
        def __init__(self):
            self.db, self._pack_list_client, self.updated = DB(), Client(), []
            self._fields_logged, self._fast_stats = True, {"changes": 0}

        async def _create_banners_from_api(self, items): pass
        async def _announce_started_banners(self, items): pass
        async def _check_pool_switch(self, n): pass
        async def _detect_pulled_hits(self, items): pass
        async def _apply_site_data(self, items): pass

        async def _process_banner_update(self, banner, row, semaphore):
            self.updated.append((banner.pack_id, banner.current_packs))

    bot = Bot()
    assert asyncio.run(bot._fast_poll_tick())
    assert bot.updated == [(24152, 0)]


def test_store_pack_records_zero_when_sold_out(tmp_path):
    from bot.store import StoreMixin
    from database.db import Database

    class Bot(StoreMixin):
        def __init__(self, db):
            self.db = db

    async def run():
        db = Database(str(tmp_path / "b.db"))
        await db.init()
        await db.upsert_store_pack(24126, "Pack", 5000, 200, 200, None, None, None, "x")
        await db.update_banner_packs(24126, 2)
        await Bot(db)._save_store_items({24126: {"pack_count": 0}})
        assert (await db.get_banner(24126))["current_packs"] == 0
        await Bot(db)._save_store_items({24126: {"pack_count": 0}})   # kein zweiter Verlaufseintrag
        import aiosqlite
        async with aiosqlite.connect(db.db_path) as conn:
            rows = await (await conn.execute(
                "SELECT old_count, new_count FROM pack_history WHERE banner_id = 24126 ORDER BY id")).fetchall()
        assert rows[-1] == (2, 0) and rows.count((0, 0)) == 0

    asyncio.run(run())
