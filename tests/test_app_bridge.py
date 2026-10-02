import asyncio
import json
from datetime import datetime, timedelta

import aiosqlite

from utils.app_bridge import AppBridge


def test_link_code_flow(tmp_path):
    async def run():
        bridge = AppBridge(str(tmp_path / "w.db"))
        await bridge.init()
        code = await bridge.create_code(123, "Basti")
        assert len(code) == 6
        assert await bridge.redeem_code("FALSCH") is None
        device = await bridge.redeem_code(code.lower())               # Groß-/Kleinschreibung egal
        assert device["name"] == "Basti" and device["user_id"] == "123"
        assert await bridge.redeem_code(code) is None                 # nur einmal benutzbar
        assert await bridge.device(device["token"]) == {"user_id": "123", "name": "Basti"}
        assert await bridge.device("anderes-token") is None
        await bridge.unlink(device["token"])
        assert await bridge.device(device["token"]) is None
        # abgelaufener Code
        old = await bridge.create_code(5, "Alt")
        async with aiosqlite.connect(bridge.db_path) as db:
            await db.execute("UPDATE link_codes SET created_at = ?", ((datetime.now() - timedelta(minutes=11)).isoformat(),))
            await db.commit()
        assert await bridge.redeem_code(old) is None

    asyncio.run(run())


def test_medal_requests(tmp_path):
    async def run():
        bridge = AppBridge(str(tmp_path / "w.db"))
        await bridge.init()
        rid = await bridge.add_request(24149, "T2", {"user_id": "123", "name": "Basti"}, "claim")
        assert [r["id"] for r in await bridge.pending()] == [rid]
        await bridge.finish(rid, False, "T2 ist schon vergeben")
        req = await bridge.get_request(rid)
        assert req["status"] == "rejected" and req["reason"] == "T2 ist schon vergeben"
        assert await bridge.pending() == []

    asyncio.run(run())


def test_bot_applies_app_medals(tmp_path, monkeypatch):
    """Bot-Abholer: Medaille vergeben, doppelt ablehnen, nur eigene zurücknehmen."""
    monkeypatch.setenv("DISCORD_TOKEN", "x")
    monkeypatch.setenv("GUILD_ID", "1")
    from bot.app_link import AppLinkMixin
    from bot.medals import MedalsMixin
    from database.db import Database
    from utils.card_pool import summarize_cards

    sent = []

    class Thread:
        archived = False
        async def send(self, text, **kw): sent.append(text)
        async def fetch_message(self, _id): raise RuntimeError("kein Startbeitrag im Test")

    class Bot(AppLinkMixin, MedalsMixin):
        user = None
        def __init__(self, db, bridge):
            self.db, self._app_bridge = db, bridge
        def get_channel(self, _id): return Thread()
        async def _update_probability_message(self, *a): pass
        async def _refresh_pool_views(self, *a): pass

    async def run():
        db = Database(str(tmp_path / "bot.db"))
        await db.init()
        pool = summarize_cards([{"id": i, "name": f"Hit {i}", "buy_point": 40000 - i * 1000, "duplication": 1,
                                 "action_type": 2} for i in range(1, 4)]
                               + [{"id": 9, "name": "Normal", "buy_point": 300, "duplication": 97, "action_type": 0}])
        async with aiosqlite.connect(db.db_path) as conn:
            await conn.execute("INSERT INTO banners (pack_id, is_active, price_coins, card_pool) VALUES (7, 1, 1000, ?)",
                               (json.dumps(pool),))
            await conn.execute("INSERT INTO discord_threads (banner_id, thread_id) VALUES (7, 700)")
            await conn.commit()
        bridge = AppBridge(str(tmp_path / "w.db"))
        await bridge.init()
        bot = Bot(db, bridge)
        me, other = {"user_id": "11", "name": "Ich"}, {"user_id": "22", "name": "Andere"}
        r1 = await bridge.add_request(7, "T2", me, "claim")
        r2 = await bridge.add_request(7, "T2", other, "claim")
        r3 = await bridge.add_request(7, "T9", me, "claim")
        r4 = await bridge.add_request(7, "T2", other, "unclaim")
        await bot._process_app_requests()
        status = {r: (await bridge.get_request(r))["status"] for r in (r1, r2, r3, r4)}
        assert status == {r1: "ok", r2: "rejected", r3: "rejected", r4: "rejected"}
        assert (await db.get_medal(700, "T2"))["user_id"] == 11
        assert any("T2 geht an <@11>" in s for s in sent)
        r5 = await bridge.add_request(7, "T2", me, "unclaim")
        await bot._process_app_requests()
        assert (await bridge.get_request(r5))["status"] == "ok" and await db.get_medal(700, "T2") is None

    asyncio.run(run())


def test_medal_migration_keeps_medals_on_the_same_card(tmp_path, monkeypatch):
    monkeypatch.setenv("DISCORD_TOKEN", "x")
    monkeypatch.setenv("GUILD_ID", "1")
    from bot.medals import MedalsMixin
    from database.db import Database
    from utils.card_pool import summarize_cards

    class Bot(MedalsMixin):
        user = None
        def __init__(self, db): self.db = db
        def get_channel(self, _id): raise RuntimeError("kein Discord im Test")

    async def run():
        db = Database(str(tmp_path / "bot.db"))
        await db.init()
        # alt (Hits zuerst): T1 = Hit 50.000, T2 = Hit 30.000, T3 = normale Karte 90.000
        # neu (nach Wert):   T1 = normale Karte 90.000, T2 = Hit 50.000, T3 = Hit 30.000
        pool = summarize_cards([{"id": 1, "name": "Hit A", "buy_point": 50000, "duplication": 1, "action_type": 2},
                                {"id": 2, "name": "Hit B", "buy_point": 30000, "duplication": 1, "action_type": 2},
                                {"id": 7, "name": "Normal", "buy_point": 90000, "duplication": 1, "action_type": 0}])
        async with aiosqlite.connect(db.db_path) as conn:
            await conn.execute("INSERT INTO banners (pack_id, is_active, card_pool) VALUES (5, 1, ?)", (json.dumps(pool),))
            await conn.execute("INSERT INTO discord_threads (banner_id, thread_id) VALUES (5, 500)")
            await conn.commit()
        await db.save_medal(500, "T1", 11)     # war Hit A
        await db.save_medal(500, "T3", 22)     # war die normale Karte
        bot = Bot(db)
        await bot._migrate_medal_order()
        assert await db.get_medals(500) == {"T2": 11, "T1": 22}
        await db.save_medal(500, "T3", 33)
        await bot._migrate_medal_order()       # nur einmal
        assert await db.get_medals(500) == {"T2": 11, "T1": 22, "T3": 33}

    asyncio.run(run())


def test_init_prunes_old_imports(tmp_path):
    from utils import app_bridge

    async def run():
        bridge = AppBridge(str(tmp_path / "w.db"))
        await bridge.init()
        for i in range(app_bridge.KEEP_IMPORTS + 5):
            await bridge.add_import({"user_id": "1"}, "sync", f"https://gtchaxonline.com/p{i}", "{}")
        async with aiosqlite.connect(bridge.db_path) as db:   # wie aus der Zeit mit 100 je Person
            for i in range(10):
                await db.execute("INSERT INTO user_imports (discord_user_id, kind, url, data) VALUES ('1', 'sync', 'x', '{}')")
            await db.commit()
        await bridge.init()
        async with aiosqlite.connect(bridge.db_path) as db:
            cur = await db.execute("SELECT count(*), max(url) FROM user_imports")
            count, _ = await cur.fetchone()
        assert count == app_bridge.KEEP_IMPORTS

    asyncio.run(run())


def test_bot_reports_webapp_outage(monkeypatch):
    """Meldung erst nach 3 Fehlschlägen und nur, wenn die App vorher erreichbar war; danach Entwarnung."""
    monkeypatch.setenv("DISCORD_TOKEN", "x")
    monkeypatch.setenv("GUILD_ID", "1")
    from aiohttp import web
    from aiohttp.test_utils import TestServer

    import bot.monitoring as monitoring

    alerts = []

    async def fake_notify(text):
        alerts.append(text)

    monkeypatch.setattr(monitoring, "notify_critical_error", fake_notify)

    class Bot(monitoring.MonitoringMixin):
        _webapp_seen, _webapp_fails = False, 0

    async def run():
        state = {"up": True}

        async def health(request):
            if not state["up"]:
                raise web.HTTPServiceUnavailable()
            return web.json_response({"ok": True})

        app = web.Application()
        app.router.add_get("/api/health", health)
        server = TestServer(app)
        await server.start_server()
        monkeypatch.setattr(monitoring, "WEBAPP_HEALTH_URL", str(server.make_url("/api/health")))
        bot = Bot()
        state["up"] = False
        await bot._check_webapp()                 # noch nie erreichbar -> keine Meldung
        assert alerts == [] and bot._webapp_fails == 0
        state["up"] = True
        await bot._check_webapp()
        state["up"] = False
        for _ in range(4):
            await bot._check_webapp()
        assert len(alerts) == 1 and "antwortet seit 15 Minuten nicht" in alerts[0]
        state["up"] = True
        await bot._check_webapp()
        assert len(alerts) == 2 and "wieder erreichbar" in alerts[1]
        await server.close()

    asyncio.run(run())
