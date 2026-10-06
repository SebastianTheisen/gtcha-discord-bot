"""Archiv: beendete Banner bleiben 30 Tage komplett erhalten (App: Kategorie Archiv)."""

import asyncio
from datetime import datetime, timedelta

import aiosqlite

from database.db import ARCHIVE_DAYS, Database, store_thread_id
from scraper.models import ScrapedBanner
from utils.card_pool import summarize_cards

CARDS = [{"id": 1, "name": "Lugia", "buy_point": 90000, "duplication": 1, "action_type": 2},
         {"id": 2, "name": "Normal", "buy_point": 300, "duplication": 99, "action_type": 0}]


async def _setup(tmp_path):
    db = Database(str(tmp_path / "b.db"))
    await db.init()
    await db.save_banner(ScrapedBanner(pack_id=24152, category="One piece", title="Luffy", price_coins=1200,
                                       current_packs=0, total_packs=100))
    await db.save_card_pool(24152, summarize_cards(CARDS))
    await db.save_thread(24152, 555, 1, 2)
    await db.save_medal(555, "T1", 42)
    await db.upsert_store_pack(24126, "宝石ガチャ BtoB", 5000, 200, 200, None, None, None, "x")
    await db.save_card_pool(24126, summarize_cards(CARDS))
    await db.save_medal(store_thread_id(24126), "T1", 42, source="app")
    for pid in (24152, 24126):
        await db.mark_banner_inactive(pid)
    await db.mark_thread_expired(24152)
    return db


async def _age(db, days):
    old = (datetime.now() - timedelta(days=days)).isoformat()
    async with aiosqlite.connect(db.db_path) as conn:
        await conn.execute("UPDATE banners SET updated_at = ? WHERE is_active = 0", (old,))
        await conn.commit()


def test_ended_banners_stay_30_days_with_medals(tmp_path):
    async def run():
        db = await _setup(tmp_path)
        await _age(db, 2)
        assert await db.purge_archived_data(max_age_hours=ARCHIVE_DAYS * 24) == 0
        assert set(await db.get_ended_banners()) == {24152, 24126}
        assert await db.get_medals(555) and await db.get_medals(store_thread_id(24126))

        from webapp.view import BannerView
        view = BannerView(db)
        banners = {b["id"]: b for b in await view.archived_banners()}
        lugia = banners[24152]
        assert lugia["archived"] and lugia["status"] == "ended" and lugia["ended_at"]
        assert [h["name"] for h in lugia["out"]] == ["Lugia"]        # Medaille gilt weiter
        assert banners[24126]["store"] and [h["name"] for h in banners[24126]["out"]] == ["Lugia"]
        detail = await view.detail(24152)
        assert detail["archived"] and detail["cards"]

        await _age(db, ARCHIVE_DAYS + 1)
        assert await db.purge_archived_data(max_age_hours=ARCHIVE_DAYS * 24) == 2
        assert await db.get_ended_banners(days=365) == {}
        assert not await db.get_medals(555) and not await db.get_medals(store_thread_id(24126))
        async with aiosqlite.connect(db.db_path) as conn:
            kept = await (await conn.execute("SELECT pack_id FROM banner_archive ORDER BY pack_id")).fetchall()
        assert kept == [(24126,), (24152,)]                           # Mini-Archiv für die Bilanz bleibt

    asyncio.run(run())


def test_discord_thread_deleted_once_after_an_hour(tmp_path):
    async def run():
        db = Database(str(tmp_path / "b.db"))
        await db.init()
        await db.save_banner(ScrapedBanner(pack_id=1, category="MIX", current_packs=0, total_packs=10))
        await db.save_thread(1, 777, 1, 2)
        await db.mark_banner_inactive(1)
        await _age(db, 1)
        assert await db.get_archived_thread_ids(max_age_hours=1) == [777]
        await db.mark_threads_expired([777])
        assert await db.get_archived_thread_ids(max_age_hours=1) == []   # nicht bei jedem Lauf erneut

    asyncio.run(run())


def test_ended_banner_with_last_zero_from_site_is_set_to_zero(tmp_path):
    async def run():
        db = Database(str(tmp_path / "b.db"))
        await db.init()
        for pid in (24152, 24153, 24154, 24155):
            await db.save_banner(ScrapedBanner(pack_id=pid, category="One piece", current_packs=2, total_packs=500,
                                               sale_end_date="2026-10-31 23:59:00" if pid != 24155 else "2020-01-01"))
        await db.log_api_values(24152, {"pack_count": 2})
        await db.log_api_values(24152, {"pack_count": 0})     # Seite: leer gezogen
        await db.log_api_values(24153, {"pack_count": 2})     # einfach verschwunden: bleibt bei 2
        await db.update_banner_packs(24154, 30)   # verschwunden mit 30 Packs: nicht sicher leer gezogen
        for pid in (24152, 24153, 24154, 24155):
            await db.mark_banner_inactive(pid)
        ended = (await db.get_banner(24152))["updated_at"]
        # 24153: verschwand mit 2 Packs lange vor dem Verkaufsende (wie 24152 auf der Seite) -> leer gezogen
        # 24155: Verkaufsende vorbei -> abgelaufen, bleibt bei 2
        assert sorted(await db.fix_sold_out_counts()) == [24152, 24153]
        assert await db.fix_sold_out_counts() == []
        row = await db.get_banner(24152)
        assert row["current_packs"] == 0 and row["updated_at"] == ended
        assert (await db.get_banner(24154))["current_packs"] == 30
        assert (await db.get_banner(24155))["current_packs"] == 2

    asyncio.run(run())


def test_ended_banner_is_reevaluated_after_new_version(tmp_path, monkeypatch):
    monkeypatch.setenv("DISCORD_TOKEN", "x")
    monkeypatch.setenv("GUILD_ID", "1")

    async def run():
        import json as _json
        from bot.learning import LearningMixin
        db = Database(str(tmp_path / "b.db"))
        await db.init()
        await db.save_banner(ScrapedBanner(pack_id=500, category="MIX", price_coins=1000, current_packs=0,
                                           total_packs=100))
        await db.save_card_pool(500, summarize_cards(CARDS))
        await db.update_site_stats(500, {"cards": 0, "coins": 0, "players": 0})
        await db.update_site_stats(500, {"cards": 1, "coins": round(90000 / 1.1), "players": 1})   # Lugia verschickt
        await db.mark_banner_inactive(500)
        assert (await db.get_pull_tracking(500))["batches"] is None      # nach neuer Version leer

        class Bot(LearningMixin):
            def __init__(self):
                self.db = db

        assert await Bot()._reevaluate_ended(await db.get_ended_banners()) == 1
        state = await db.get_pull_tracking(500)
        assert state["pulled"] == ["1"] and state["batches"]
        assert await Bot()._reevaluate_ended(await db.get_ended_banners()) == 0   # nur einmal

    asyncio.run(run())
