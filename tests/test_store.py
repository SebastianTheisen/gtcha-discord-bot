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

        async def _detect_pulled_hits(self, items):   # echte Erkennung: eigener Test unten
            self.detected = sorted(items)

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
        assert store["hits_total"] == 3 and store["hits_open"] == 3              # wie normale Banner (T1-T3)
        assert banners[1].get("store") is None
        assert 24126 in [b["id"] for b in view.hot(list(banners.values()))]    # wie alle anderen Packs
        detail = await view.detail(24126)
        assert detail["title"] == "宝石ガチャ BtoB" and detail["cards"]
        assert any("/pack/24126/1.webp" in u for u in await view.image_urls())

    asyncio.run(run())


HIT_CARDS = [{"id": 900, "name": "Diamant", "buy_point": 110000, "duplication": 1, "action_type": 2},
             {"id": 901, "name": "Perle", "buy_point": 900, "duplication": 199, "action_type": 0}]


def test_store_hits_detected_and_medals_like_normal_banners_without_discord(tmp_path, env):
    """Versand eines Hits wird erkannt, Medaillen aus der App gespeichert - und nichts geht nach Discord."""
    from bot.app_link import AppLinkMixin
    from bot.hits import HitsMixin
    from bot.medals import MedalsMixin
    from bot.store import StoreMixin
    from utils.app_bridge import AppBridge
    from webapp.view import BannerView

    discord = []

    class Bot(StoreMixin, HitsMixin, AppLinkMixin, MedalsMixin):
        def __init__(self, db, bridge, client):
            self.db, self._app_bridge, self._client = db, bridge, client

        def _store_client_get(self):
            return self._client

        # alles, was nach Discord ginge, wird nur mitgeschrieben
        def get_channel(self, _id):
            discord.append(("get_channel", _id))

        async def fetch_channel(self, _id):
            discord.append(("fetch_channel", _id))

        async def _publish_pulls(self, *a, **kw):
            discord.append("publish")

        async def _refresh_pool_views(self, *a, **kw):
            discord.append("refresh")

        async def _update_probability_message(self, *a):
            discord.append("probability")

    class Client(FakeClient):
        async def fetch_store_pools(self, ids):
            return {pid: summarize_cards(HIT_CARDS) for pid in ids}

    async def run():
        db = Database(str(tmp_path / "b.db"))
        await db.init()
        bridge = AppBridge(str(tmp_path / "w.db"))
        await bridge.init()
        client = Client()
        bot = Bot(db, bridge, client)
        await bot._scrape_store()            # anlegen, Pool laden, erste Erkennung (nur merken)
        await bot._scrape_store()
        # der Diamant (Versand nur, 110.000) wird verschickt: Seite zählt ohne Steuer
        client.items[24126].update(total_sendcount=1, total_sendprice=100000, pack_count=199)
        await bot._scrape_store()
        state = await db.get_pull_tracking(24126)
        assert state["pulled"] == ["900"]
        view = BannerView(db)
        b = next(x for x in await view.all_banners(with_pool=True) if x["id"] == 24126)
        assert b["hits_open"] == 0 and [h["name"] for h in b["out"]] == ["Diamant"]
        # Medaille aus der App (Karte abhaken) - gespeichert an der internen Nummer, ohne Discord
        user = {"user_id": "42", "name": "Basti"}
        rid = await bridge.add_request(24126, "T1", user, "claim")
        await bot._process_app_requests()
        assert (await bridge.get_request(rid))["status"] == "ok"
        assert await db.get_medals(-24126) == {"T1": 42}
        hits = (await view.detail(24126))["hits"]
        assert any(h.get("medal_user") == "42" for h in hits)
        assert [m["banner_id"] for m in await view.my_medals("42")] == [24126]
        assert 24126 in await view.claim_targets()
        # zurücknehmen geht nur für die eigene Medaille
        other = await bridge.add_request(24126, "T1", {"user_id": "7", "name": "X"}, "unclaim")
        mine = await bridge.add_request(24126, "T1", user, "unclaim")
        await bot._process_app_requests()
        assert (await bridge.get_request(other))["status"] == "rejected"
        assert (await bridge.get_request(mine))["status"] == "ok" and await db.get_medals(-24126) == {}
        assert discord == []                 # nichts davon hat Discord berührt
        assert await db.get_thread_by_banner_id(24126) is None

    asyncio.run(run())


def test_api_log_only_on_change(tmp_path):
    async def run():
        db = Database(str(tmp_path / "b.db"))
        await db.init()
        item = {"pack_count": 10, "total_sendcount": 1, "total_sendprice": 1000, "total_kangen": 500,
                "total_sendpeople": 1, "point": 1200}
        assert await db.log_api_values(5, item) is True
        assert await db.log_api_values(5, dict(item)) is False            # unverändert: kein Eintrag
        assert await db.log_api_values(5, {**item, "total_sendprice": 1500}) is True
        db2 = Database(db.db_path)                                        # nach Neustart: letzter Stand bekannt
        assert await db2.log_api_values(5, {**item, "total_sendprice": 1500}) is False
        async with aiosqlite.connect(db.db_path) as conn:
            rows = await (await conn.execute("SELECT sendprice FROM api_log WHERE banner_id = 5 ORDER BY id")).fetchall()
        assert [r[0] for r in rows] == [1000, 1500]

    asyncio.run(run())
