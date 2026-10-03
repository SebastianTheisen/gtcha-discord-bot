"""Store-Packs (gtchaxonline.com/store): nur App, nichts nach Discord."""

import asyncio
import json

import aiosqlite
import pytest

from database.db import STORE, Database
from utils.card_pool import summarize_cards

ITEM = {"id": 24126, "card_type": "13", "name": "宝石ガチャ BtoB", "point": 5000, "max_buy_count": 0, "rule_type": 0,
        "pack_count": 200, "stock_count": "0", "total_kangen": 0, "total_sendprice": 0, "total_sendcount": 0,
        "total_sendpeople": 0, "total_pack_count": 200, "market_enabled": 1, "is_countdown": 1,
        "start_date": "2026-10-03 00:00:00", "end_date": "2026-10-31 23:59:00", "is_before": False,
        "image": ["/pack/24126/1.webp?d=1791010597"]}
CARDS = [{"id": 200000000220, "name": "Emerald", "buy_point": 171000, "duplication": 1, "action_type": 0,
          "image_url": "https://gtchaxonline.com/card/200000000220_small.jpg"},
         {"id": 200000000221, "name": "Ring", "buy_point": 2000, "duplication": 199, "action_type": 0}]


@pytest.fixture
def env(monkeypatch):
    monkeypatch.setenv("DISCORD_TOKEN", "x")
    monkeypatch.setenv("GUILD_ID", "1")


class FakeClient:
    def __init__(self):
        self.items, self.ok, self.pool_calls = {24126: dict(ITEM)}, True, []

    async def fetch_store(self, card_types, discover):
        return self.items, {13}, self.ok

    async def fetch_store_pools(self, ids):
        self.pool_calls.append(list(ids))
        return {pid: summarize_cards(CARDS) for pid in ids}


def make_bot(db, client):
    from bot.store import StoreMixin

    class Bot(StoreMixin):
        def __init__(self):
            self.db = db

        def _store_client_get(self):
            return client

    return Bot()


def test_store_scrape_saves_hidden_from_discord_and_ends_missing_packs(tmp_path, env):
    async def run():
        db = Database(str(tmp_path / "b.db"))
        await db.init()
        client = FakeClient()
        bot = make_bot(db, client)
        await bot._scrape_store()
        row = await db.get_banner(24126)
        assert row["is_active"] == STORE and row["category"] == "Store" and row["price_coins"] == 5000
        assert row["current_packs"] == 200 and row["image_url"] == "https://gtchaxonline.com/pack/24126/1.webp"
        assert row["detail_page_url"].endswith("/store-pack-detail?packId=24126") and row["starts_at"]
        assert json.loads(row["card_pool"])["total_count"] == 200 and client.pool_calls == [[24126]]
        # Discord sieht den Pack nie: weder aktive Banner noch Not-found-Zähler noch Pool-Nachladen des Bots
        assert 24126 not in await db.get_active_banners() and 24126 in await db.get_store_banners()
        assert await db.get_all_active_banner_ids() == [] and await db.get_banners_without_pool(10, []) == []
        assert (await db.get_meta("store_card_types")) == "[13]"
        # Pack-Zahl ändert sich -> Verlauf
        client.items[24126]["pack_count"] = 190
        await bot._scrape_store()
        async with aiosqlite.connect(db.db_path) as conn:
            hist = await (await conn.execute("SELECT old_count, new_count FROM pack_history WHERE banner_id = 24126")).fetchall()
        assert hist == [(200, 190)] and client.pool_calls == [[24126]]          # Pool wird nicht erneut geholt
        # fehlt zweimal in Folge -> beendet (und danach vom normalen Aufräumen archiviert)
        client.items = {}
        await bot._scrape_store()
        assert 24126 in await db.get_store_banners()
        await bot._scrape_store()
        assert await db.get_store_banners() == {} and (await db.get_banner(24126))["is_active"] == 0
        # fehlgeschlagener Abruf ändert nichts
        client.items, client.ok = {24126: dict(ITEM)}, False
        await bot._scrape_store()
        assert await db.get_store_banners() == {}

    asyncio.run(run())


def test_store_never_touches_a_normal_banner_with_same_id(tmp_path, env):
    async def run():
        db = Database(str(tmp_path / "b.db"))
        await db.init()
        async with aiosqlite.connect(db.db_path) as conn:
            await conn.execute("INSERT INTO banners (pack_id, category, title, is_active) VALUES (24126, 'MIX', 'Normal', 1)")
            await conn.commit()
        assert await db.upsert_store_pack(24126, "Store", 5000, 200, 200, None, None, None, "x") is False
        row = await db.get_banner(24126)
        assert row["category"] == "MIX" and row["title"] == "Normal" and row["is_active"] == 1

    asyncio.run(run())


def test_store_packs_in_app_data(tmp_path, env):
    from webapp.view import BannerView

    async def run():
        db = Database(str(tmp_path / "b.db"))
        await db.init()
        bot = make_bot(db, FakeClient())
        await bot._scrape_store()
        async with aiosqlite.connect(db.db_path) as conn:   # ein normaler Banner daneben
            await conn.execute("INSERT INTO banners (pack_id, category, title, price_coins, current_packs, total_packs, "
                               "is_active, card_pool) VALUES (1, 'MIX', 'Normal', 1000, 50, 100, 1, ?)",
                               (json.dumps(summarize_cards(CARDS)),))
            await conn.commit()
        view = BannerView(db)
        banners = {b["id"]: b for b in await view.all_banners(with_pool=True)}
        store = banners[24126]
        assert store["store"] is True and store["category"] == "Store" and store["price"] == 5000
        assert store["hits_open"] is None and store["tracked_hits"] is False and store["out"] == []
        assert "store" not in banners[1] or banners[1].get("store") is None
        assert [b["id"] for b in view.hot(list(banners.values()))] == [1]       # Store nicht in der Top 10
        detail = await view.detail(24126)
        assert detail["title"] == "宝石ガチャ BtoB" and detail["cards"]
        assert any("/pack/24126/1.webp" in u for u in await view.image_urls())

    asyncio.run(run())
