"""Discord schlank und zeitversetzt: die Datenbank (App) hat alles sofort, Discord erst nach der Verzögerung."""

import asyncio
import json
import time
from datetime import datetime, timedelta

import aiosqlite
import pytest

from database.db import Database
from utils.app_bridge import AppBridge


@pytest.fixture
def env(monkeypatch):
    monkeypatch.setenv("DISCORD_TOKEN", "x")
    monkeypatch.setenv("GUILD_ID", "1")


def make_bot(db, bridge, sent, refreshed):
    import discord

    from bot.discord_view import DiscordViewMixin

    class Thread(discord.Thread):
        archived, id = False, 700

        def __init__(self):
            pass

        async def send(self, text, **kw):
            sent.append(text)

        async def fetch_message(self, _id):
            raise RuntimeError("kein Startbeitrag im Test")

    class Bot(DiscordViewMixin):
        def __init__(self):
            self.db, self._app_bridge = db, bridge

        @property
        def app_bridge(self):
            return self._app_bridge

        def get_channel(self, _id):
            return Thread()

        async def _refresh_pool_views(self, pid, **kw):
            refreshed.append(pid)

        async def _sync_thread_title(self, pid):
            pass

        async def _set_starter_reaction(self, *a, **kw):
            pass

    return Bot()


def test_app_medal_hidden_in_discord_until_delay(tmp_path):
    async def run():
        db = Database(str(tmp_path / "b.db"))
        await db.init()
        await db.save_medal(700, "T1", 5)                       # im Thread geschrieben
        await db.save_medal(700, "T2", 6, source="app")          # in der App gemeldet
        assert await db.get_medals(700) == {"T1": 5, "T2": 6}    # App/Datenbank: sofort alles
        since = (datetime.now() - timedelta(minutes=30)).isoformat()
        assert await db.get_medals(700, hide_app_since=since) == {"T1": 5}   # Discord: T2 noch nicht
        async with aiosqlite.connect(db.db_path) as conn:
            await conn.execute("UPDATE medals SET created_at = ? WHERE tier = 'T2'",
                               ((datetime.now() - timedelta(minutes=31)).isoformat(),))
            await conn.commit()
        assert await db.get_medals(700, hide_app_since=since) == {"T1": 5, "T2": 6}

    asyncio.run(run())


def test_detected_hit_published_after_delay(tmp_path, env):
    async def run():
        db = Database(str(tmp_path / "b.db"))
        await db.init()
        async with aiosqlite.connect(db.db_path) as conn:
            await conn.execute("INSERT INTO banners (pack_id, is_active) VALUES (7, 1)")
            await conn.commit()
        bridge = AppBridge(str(tmp_path / "w.db"))
        await bridge.init()
        await bridge.set_setting("discord_delay", "30")
        sent, refreshed = [], []
        bot = make_bot(db, bridge, sent, refreshed)
        before = await db.get_pull_tracking(7)
        await db.set_pull_tracking(7, None, None, None, ["hitA"], [])         # Bot erkennt Hit A
        await bot._publish_pulls(7, 700, {"pulled": before["pulled"], "unsure": before["unsure"]},
                                 "🔥 **Hit gezogen:** A")
        assert (await db.get_pull_tracking(7))["pulled"] == ["hitA"]          # App sieht ihn sofort
        assert (await bot._public_pull_tracking(7))["pulled"] == []           # Discord noch nicht
        assert sent == []
        await bot._process_discord_outbox()                                   # noch nicht fällig
        assert sent == [] and (await bot._public_pull_tracking(7))["pulled"] == []
        async with aiosqlite.connect(db.db_path) as conn:
            await conn.execute("UPDATE discord_outbox SET send_at = ?", (time.time() - 1,))
            await conn.commit()
        await bot._process_discord_outbox()
        assert sent == ["🔥 **Hit gezogen:** A"] and refreshed == [7]
        assert (await bot._public_pull_tracking(7))["pulled"] == ["hitA"]
        assert await db.get_public_pulls(7) is None                           # wieder echter Stand

    asyncio.run(run())


def test_no_delay_in_full_mode(tmp_path, env):
    async def run():
        db = Database(str(tmp_path / "b.db"))
        await db.init()
        bridge = AppBridge(str(tmp_path / "w.db"))
        await bridge.init()
        await bridge.set_setting("discord_mode", "full")
        assert (await bridge.discord_view())["delay"] == 0
        sent = []
        bot = make_bot(db, bridge, sent, [])
        assert not await bot._slim()
        await bot._publish_pulls(7, 700, {"pulled": [], "unsure": []}, "🔥 sofort")
        assert sent == ["🔥 sofort"]

    asyncio.run(run())


def test_slim_embed_shows_light_and_hits_only(env):
    from bot.threads import ThreadsMixin

    class Bot(ThreadsMixin):
        pass

    stats = {"ev": 1100, "ev_pct": 110.0, "estimated": False, "data_based": True, "tracked_hits": True,
             "hits_open": 2, "hits_total": 5, "open_tiers": ["T2"], "cost_to_hit": 9000}
    banner = {"pack_id": 7, "title": "Test", "price_coins": 1000, "current_packs": 40, "total_packs": 100}
    slim = Bot()._build_banner_embed(banner, stats=stats, tempo="12/h", shipped="3 Karten", minimum="300 Coins",
                                     pool_value="99.000 Coins", slim=True)
    names = [f.name for f in slim.fields]
    assert "Rückgabe" in names and "Hits" in names
    assert not {"Ø Rückgabe pro Zug", "Gesamtwert", "Abverkauf", "Verschickt", "Mindestens zurück pro Zug"} & set(names)
    assert next(f.value for f in slim.fields if f.name == "Rückgabe") == "🟢 lohnt sich"
    assert "Kosten" not in json.dumps([f.value for f in slim.fields])
    full = Bot()._build_banner_embed(banner, stats=stats, tempo="12/h", slim=False)
    assert "Ø Rückgabe pro Zug" in [f.name for f in full.fields]


def test_admin_settings_only_for_admins(tmp_path, monkeypatch):
    from aiohttp.test_utils import TestClient, TestServer

    from webapp.server import App, make_app

    async def run():
        await Database(str(tmp_path / "gtcha_bot.db")).init()
        app = App(str(tmp_path / "gtcha_bot.db"), str(tmp_path), "x")
        await app.bridge.init()
        tokens = {}
        for uid in (42, 7):
            tokens[uid] = (await app.bridge.redeem_code(await app.bridge.create_code(uid, f"U{uid}")))["token"]
        monkeypatch.setenv("APP_ADMIN_IDS", "42")
        await app.bridge.add_admin(7)          # alter Tabelleneintrag zählt nicht - nur die .env
        web_app = make_app(app)
        web_app.on_startup.clear()
        web_app.on_cleanup.clear()
        client = TestClient(TestServer(web_app))
        await client.start_server()
        h = lambda uid: {"X-Device-Token": tokens[uid]}
        assert (await (await client.get("/api/me", headers=h(42))).json())["admin"] is True
        assert (await (await client.get("/api/me", headers=h(7))).json())["admin"] is False
        assert (await client.get("/api/admin/settings", headers=h(7))).status == 403
        assert (await client.post("/api/admin/settings", headers=h(7), json={"mode": "full", "delay_minutes": 0})).status == 403
        res = await (await client.get("/api/admin/settings", headers=h(42))).json()
        assert (res["mode"], res["delay_minutes"]) == ("slim", 30)                  # Voreinstellung
        assert res["admins"] == [{"user_id": "42", "name": "U42"}] and res["you"] == "42"
        res = await (await client.post("/api/admin/settings", headers=h(42), json={"mode": "slim", "delay_minutes": 90})).json()
        assert (res["mode"], res["delay_minutes"]) == ("slim", 90)
        # Nutzer-Statistik: nur Admin
        assert (await client.get("/api/admin/users", headers=h(7))).status == 403
        assert (await client.get("/api/admin/user/42", headers=h(7))).status == 403
        users = (await (await client.get("/api/admin/users", headers=h(42))).json())["users"]
        assert sorted(u["name"] for u in users) == ["U42", "U7"]
        assert (await (await client.get("/api/admin/user/7", headers=h(42))).json())["empty"] is True
        assert (await client.get("/api/admin/user/999", headers=h(42))).status == 404
        assert (await app.bridge.discord_view())["delay"] == 90 * 60
        assert (await client.post("/api/admin/settings", headers=h(42), json={"mode": "x", "delay_minutes": 1})).status == 400
        await client.close()

    asyncio.run(run())


def test_admins_replaced_on_start_only_configured_ids(tmp_path, env, monkeypatch):
    """Admin-Tabelle spiegelt nur APP_ADMIN_IDS - frühere Einträge und der Server-Inhaber fliegen raus."""
    import config
    from bot.discord_view import DiscordViewMixin

    class Guild:
        owner_id = 111

    class Bot(DiscordViewMixin):
        guilds = [Guild()]

        def __init__(self, bridge):
            self._app_bridge = bridge

        @property
        def app_bridge(self):
            return self._app_bridge

    async def run():
        bridge = AppBridge(str(tmp_path / "w.db"))
        await bridge.init()
        await bridge.add_admin(999)                                   # z. B. ein Discord-Admin von früher
        monkeypatch.setattr(config, "APP_ADMIN_IDS", ["42"])
        await Bot(bridge)._remember_owner_as_admin()
        assert [a["user_id"] for a in await bridge.admins()] == ["42"]
        assert not await bridge.is_admin(999) and not await bridge.is_admin(111)
        monkeypatch.setattr(config, "APP_ADMIN_IDS", [])
        await Bot(bridge)._remember_owner_as_admin()
        assert await bridge.admins() == []                      # leer = niemand, auch nicht der Inhaber

    asyncio.run(run())


def test_webapp_admin_check_reads_env_each_time(monkeypatch):
    from webapp.server import is_admin
    monkeypatch.delenv("APP_ADMIN_IDS", raising=False)
    assert not is_admin("42")                                    # leer = niemand
    monkeypatch.setenv("APP_ADMIN_IDS", "42")
    assert is_admin("42") and not is_admin("7") and not is_admin("")
    monkeypatch.setenv("APP_ADMIN_IDS", " 42 , 99 ,abc")
    assert is_admin("99") and not is_admin("abc")
