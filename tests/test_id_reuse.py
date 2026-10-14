"""GTCHA vergibt Pack-IDs neu (24260): der neue Banner erscheint und startet ohne die Daten des alten."""

import asyncio
from datetime import datetime, timedelta

import aiosqlite

from bot.scraping import ScrapingMixin
from database.db import Database, store_thread_id
from scraper.models import ScrapedBanner
from utils.banner_info import jst_timestamp


class Bridge:
    def __init__(self):
        self.forgotten = []

    async def forget_pack(self, pid):
        self.forgotten.append(pid)


def make_bot(db):
    class Bot(ScrapingMixin):
        def __init__(self):
            self.db, self.app_bridge, self.posted, self.deleted = db, Bridge(), [], []

        async def _post_banner_to_discord(self, banner, starts_at=None):
            self.posted.append(banner.pack_id)

        async def _delete_banner_thread(self, pid):
            self.deleted.append(pid)
            return True

    return Bot()


async def _old_banner(db, pid=24260, start="2026-09-01 12:00:00", active=False, name="alt_name"):
    await db.save_banner(ScrapedBanner(pack_id=pid, category="Pokemon", title="Alter Banner", price_coins=500,
                                       current_packs=0, total_packs=100))
    await db.set_start(pid, jst_timestamp(start), announced=True)
    await db.set_site_name(pid, name)
    await db.save_thread(pid, 555, 1, 2)
    await db.save_medal(store_thread_id(pid), "T1", 42)
    async with aiosqlite.connect(db.db_path) as conn:
        now = datetime.now().isoformat()
        await conn.execute("INSERT INTO pack_history (banner_id, old_count, new_count, changed_at) VALUES (?, 5, 0, ?)",
                           (pid, now))
        await conn.execute("INSERT INTO shipment_history (banner_id, old_cards, new_cards, old_coins, new_coins, "
                           "changed_at) VALUES (?, 0, 3, 0, 90000, ?)", (pid, now))
        await conn.execute("INSERT INTO convert_history (banner_id, old_coins, new_coins, changed_at) VALUES (?, 0, 500, ?)",
                           (pid, now))
        await conn.commit()
    if not active:
        await db.mark_banner_inactive(pid)
        await db.mark_thread_expired(pid)


def _item(start, name="neu_name", packs=200):
    return {"id": 24260, "card_type": "1", "pack_count": packs, "total_pack_count": 200, "point": 1000,
            "start_date": start, "name": name}


async def _counts(db, pid=24260):
    async with aiosqlite.connect(db.db_path) as conn:
        out = {}
        for table in ("pack_history", "shipment_history", "convert_history"):
            out[table] = (await (await conn.execute(f"SELECT count(*) FROM {table} WHERE banner_id = ?",
                                                    (pid,))).fetchone())[0]
        out["archive"] = (await (await conn.execute("SELECT title FROM banner_archive WHERE pack_id = ?",
                                                    (pid,))).fetchone())
        out["active_threads"] = (await (await conn.execute(
            "SELECT count(*) FROM discord_threads WHERE banner_id = ? AND is_expired = 0", (pid,))).fetchone())[0]
    return out


def test_reused_id_creates_new_banner_without_old_data(tmp_path):
    async def run():
        db = Database(str(tmp_path / "b.db"))
        await db.init()
        await _old_banner(db)
        bot = make_bot(db)
        created = await bot._create_banners_from_api({24260: _item("2026-10-14 12:00:00")})
        row = await db.get_banner(24260)
        return created, row, await _counts(db), await db.get_medals(store_thread_id(24260)), bot

    created, row, counts, store_medals, bot = asyncio.run(run())
    assert created == [24260] and bot.posted == [24260]
    assert row["is_active"] == 1 and row["starts_at"] == jst_timestamp("2026-10-14 12:00:00")
    assert counts == {"pack_history": 0, "shipment_history": 0, "convert_history": 0, "archive": ("Alter Banner",),
                      "active_threads": 0}
    assert store_medals == {} and bot.app_bridge.forgotten == [24260]


def test_same_banner_reappearing_keeps_its_data(tmp_path):
    """Gleicher Verkaufsstart: derselbe Banner taucht wieder auf (z. B. fälschlich beendet) - nichts löschen."""
    async def run():
        db = Database(str(tmp_path / "b.db"))
        await db.init()
        await _old_banner(db)
        bot = make_bot(db)
        created = await bot._create_banners_from_api({24260: _item("2026-09-01 12:00:00", name="alt_name")})
        return created, await _counts(db), bot

    created, counts, bot = asyncio.run(run())
    assert created == [24260]
    assert counts["shipment_history"] == 1 and counts["pack_history"] == 1 and counts["archive"] is None
    assert bot.app_bridge.forgotten == []


def test_reused_id_while_old_banner_still_counts_as_running(tmp_path):
    """Die Seite meldet zur ID einen späteren Start und anderen Namen, der Bot hält den alten noch für aktiv."""
    async def run():
        db = Database(str(tmp_path / "b.db"))
        await db.init()
        old_start = (datetime.now() - timedelta(days=20)).strftime("%Y-%m-%d %H:%M:%S")
        await _old_banner(db, start=old_start, active=True)
        new_start = (datetime.now() - timedelta(hours=1)).strftime("%Y-%m-%d %H:%M:%S")
        bot = make_bot(db)
        created = await bot._create_banners_from_api({24260: _item(new_start)})
        return created, await db.get_banner(24260), await _counts(db), bot, new_start

    created, row, counts, bot, new_start = asyncio.run(run())
    assert bot.deleted == [24260] and created == [24260]
    assert row["is_active"] == 1 and row["starts_at"] == jst_timestamp(new_start)
    assert counts["shipment_history"] == 0 and counts["archive"] == ("Alter Banner",)


def test_history_before_start_of_running_banner_is_dropped(tmp_path):
    """24260 wurde schon mit altem Verlauf angelegt: beim Start wird alles über 1 Tag vor dem Verkaufsstart gelöst."""
    async def run():
        db = Database(str(tmp_path / "b.db"))
        await db.init()
        start = datetime.now() - timedelta(hours=5)
        await db.save_banner(ScrapedBanner(pack_id=24260, category="Pokemon", title="Neu", price_coins=500,
                                           current_packs=150, total_packs=200))
        await db.set_start(24260, int(start.timestamp()), announced=True)
        async with aiosqlite.connect(db.db_path) as conn:
            for t, cards in ((start - timedelta(days=20), 3), (start + timedelta(hours=1), 1)):   # alt / neu
                await conn.execute("INSERT INTO shipment_history (banner_id, old_cards, new_cards, old_coins, new_coins, "
                                   "changed_at) VALUES (24260, 0, ?, 0, 1000, ?)", (cards, t.isoformat()))
            await conn.commit()
        dropped = await db.drop_history_before_start()
        async with aiosqlite.connect(db.db_path) as conn:
            left = await (await conn.execute("SELECT new_cards FROM shipment_history WHERE banner_id = 24260")).fetchall()
        return dropped, left

    dropped, left = asyncio.run(run())
    assert dropped == {24260: 1} and left == [(1,)]


def test_missing_start_is_filled_from_site_and_old_history_dropped(tmp_path):
    """Über den Tab-Scrape reaktiviert: kein Verkaufsstart - wird aus pack/list nachgetragen, alter Verlauf gelöst."""
    async def run():
        db = Database(str(tmp_path / "b.db"))
        await db.init()
        await db.save_banner(ScrapedBanner(pack_id=24260, category="Bonus", title=None, price_coins=150000,
                                           current_packs=14, total_packs=20))
        start = datetime.now() - timedelta(days=1)
        async with aiosqlite.connect(db.db_path) as conn:
            await conn.execute("INSERT INTO shipment_history (banner_id, old_cards, new_cards, old_coins, new_coins, "
                               "changed_at) VALUES (24260, 0, 3, 0, 9000, ?)", ((start - timedelta(days=30)).isoformat(),))
            await conn.commit()
        filled = await db.fill_start(24260, int(start.timestamp()))
        again = await db.fill_start(24260, int(start.timestamp()) + 999)   # schon gesetzt: bleibt
        dropped = await db.drop_history_before_start(24260)
        return filled, again, dropped, (await db.get_banner(24260))["starts_at"], int(start.timestamp())

    filled, again, dropped, stored, start = asyncio.run(run())
    assert filled and not again and dropped == {24260: 1} and stored == start
