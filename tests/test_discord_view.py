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
            await conn.execute("INSERT INTO discord_threads (banner_id, thread_id) VALUES (7, 700)")
            await conn.commit()
        bridge = AppBridge(str(tmp_path / "w.db"))
        await bridge.init()
        await bridge.set_setting("discord_delay", "30")
        await bridge.set_setting("discord_mode", "slim")   # schlank: erkannte Hits kommen verzögert in Discord
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
        async with aiosqlite.connect(db.db_path) as conn:
            await conn.execute("INSERT INTO discord_threads (banner_id, thread_id) VALUES (7, 700)")
            await conn.commit()
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
        assert (res["mode"], res["delay_minutes"]) == ("minimal", 30)               # Voreinstellung
        assert res["admins"] == [{"name": "U42"}] and "you" not in res          # keine IDs im Klartext
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


def test_security_basics(tmp_path):
    """Test-Push nur ans eigene Gerät, Sicherheits-Header, alte Rohdaten werden übernommen und gelöscht."""
    from aiohttp.test_utils import TestClient, TestServer

    from tests.test_webapp_history import COINS
    from webapp.server import App, make_app

    async def run():
        await Database(str(tmp_path / "gtcha_bot.db")).init()
        app = App(str(tmp_path / "gtcha_bot.db"), str(tmp_path), "x")
        await app.bridge.init()
        await app.bridge.add_import({"user_id": "42"}, "sync", "https://gtchaxonline.com/buy-point-history",
                                    json.dumps({"pages": [{"text": COINS}]}))
        await app.migrate_raw_imports()
        assert await app.bridge.raw_import_users() == []                       # Rohdaten weg
        assert len((await app.bridge.get_history("42"))["coins"]["items"]) == 6   # Verlauf bleibt
        web_app = make_app(app)
        web_app.on_startup.clear()
        web_app.on_cleanup.clear()
        client = TestClient(TestServer(web_app))
        await client.start_server()
        res = await client.post("/api/push/test", json={})
        assert res.status == 400                                               # nicht an alle
        res = await client.get("/")
        csp = res.headers["Content-Security-Policy"]
        assert "script-src 'self'" in csp and "frame-ancestors 'none'" in csp
        await client.close()

    asyncio.run(run())


def test_admin_tools_status_block_push_medal(tmp_path, monkeypatch):
    from aiohttp.test_utils import TestClient, TestServer

    from webapp.server import App, make_app

    async def run():
        await Database(str(tmp_path / "gtcha_bot.db")).init()
        app = App(str(tmp_path / "gtcha_bot.db"), str(tmp_path), "x")
        await app.bridge.init()
        await app.push.init()
        tok = {}
        for uid in (42, 7):
            tok[uid] = (await app.bridge.redeem_code(await app.bridge.create_code(uid, f"U{uid}")))["token"]
        monkeypatch.setenv("APP_ADMIN_IDS", "42")
        web_app = make_app(app)
        web_app.on_startup.clear()
        web_app.on_cleanup.clear()
        client = TestClient(TestServer(web_app))
        await client.start_server()
        h = lambda uid: {"X-Device-Token": tok[uid]}
        for path, body in (("/api/admin/medal", {"pack_id": 1, "tier": "T1", "action": "remove"}),
                           ("/api/admin/block", {"user_id": "42", "blocked": True}),
                           ("/api/admin/push", {"title": "x"})):
            assert (await client.post(path, headers=h(7), json=body)).status == 403        # nur Admin
        assert (await client.get("/api/admin/status", headers=h(7))).status == 403
        st = await (await client.get("/api/admin/status", headers=h(42))).json()
        assert st["users"] == 2 and st["slim"] is True
        # Push an alle
        assert (await (await client.post("/api/admin/push", headers=h(42), json={"title": "Hallo"})).json())["sent"] == 0
        # Medaille umtragen -> Auftrag für den Bot
        res = await client.post("/api/admin/medal", headers=h(42), json={"pack_id": 5, "tier": "T2", "action": "assign",
                                                                         "user_id": "7"})
        req = await app.bridge.get_request((await res.json())["id"])
        assert (req["action"], req["discord_user_id"], req["tier"]) == ("admin_assign", "7", "T2")
        # Sperren: Admin nicht, andere ja - Geräte weg, neu verknüpfen geht nicht
        assert (await client.post("/api/admin/block", headers=h(42), json={"user_id": "42", "blocked": True})).status == 400
        assert (await client.post("/api/admin/block", headers=h(42), json={"user_id": "7", "blocked": True})).status == 200
        assert await app.bridge.device(tok[7]) is None
        assert await app.bridge.redeem_code(await app.bridge.create_code(7, "U7")) is None
        users = (await (await client.get("/api/admin/users", headers=h(42))).json())["users"]
        assert next(u for u in users if u["user_id"] == "7")["blocked"] is True
        await client.post("/api/admin/block", headers=h(42), json={"user_id": "7", "blocked": False})
        assert await app.bridge.redeem_code(await app.bridge.create_code(7, "U7"))
        await client.close()

    asyncio.run(run())


def test_old_posts_deleted_in_bulk(env):
    """Junge Nachrichten gesammelt (max. 100 je Aufruf), ältere als 14 Tage einzeln."""
    from datetime import timezone

    from bot.discord_view import DiscordViewMixin

    calls = []

    class Msg:
        def __init__(self, days):
            self.created_at = datetime.now(timezone.utc) - timedelta(days=days)

        async def delete(self):
            calls.append("single")

    class Thread:
        async def delete_messages(self, chunk):
            calls.append(("bulk", len(chunk)))

    msgs = [Msg(1) for _ in range(150)] + [Msg(20) for _ in range(3)]
    deleted = asyncio.run(DiscordViewMixin()._delete_messages(Thread(), msgs))
    assert deleted == 153
    assert calls == [("bulk", 100), ("bulk", 50), "single", "single", "single"]


def test_pack_updates_stay_in_slim_mode(env):
    """Pack-Updates werden im schlanken Modus weiter gepostet und beim Aufräumen nie gelöscht."""
    import inspect

    from bot.discord_view import OLD_POST_PREFIXES
    from bot.scraping import ScrapingMixin
    assert not any("Pack-Update" in p for p in OLD_POST_PREFIXES)
    assert "_slim" not in inspect.getsource(ScrapingMixin._post_pack_update_to_thread)


def test_thread_rename_does_not_block(env):
    """Discord lässt Umbenennungen oft minutenlang warten - der Aufrufer (Scrape) darf darauf nicht warten."""
    import discord

    from bot.threads import ThreadsMixin

    renamed = []

    class Thread(discord.Thread):
        archived, name = False, "alt"

        def __init__(self):
            pass

        async def edit(self, name=None, **kw):
            await asyncio.sleep(0.5)          # simuliert die Wartezeit bei Discord
            renamed.append(name)

    class DB:
        async def get_banner(self, pid):
            return {"price_coins": 1000, "total_packs": 100}

        async def get_thread_by_banner_id(self, pid):
            return {"thread_id": 700, "title": "alt"}

        async def set_thread_title(self, tid, title):
            pass

    class Bot(ThreadsMixin):
        db = DB()

        def get_channel(self, _id):
            return Thread()

        async def _thread_status(self, row, data):
            return "running"

        async def _banner_threads(self, pid):
            return [(await self.db.get_thread_by_banner_id(pid), "main")]

        async def _slim(self, scope="main"):
            return True

    async def run():
        bot = Bot()
        t0 = asyncio.get_running_loop().time()
        await bot._sync_thread_title(5)
        assert asyncio.get_running_loop().time() - t0 < 0.2 and renamed == []   # sofort zurück
        await bot._title_worker
        assert renamed and "ID 5" in renamed[0]

    asyncio.run(run())



def test_minimal_mode_posts_no_detected_hits(tmp_path, env):
    """Minimal: automatisch erkannte Hits nur in der App - in Discord kein Post, auch nicht verzögert."""
    async def run():
        db = Database(str(tmp_path / "b.db"))
        await db.init()
        async with aiosqlite.connect(db.db_path) as conn:
            await conn.execute("INSERT INTO banners (pack_id, is_active) VALUES (7, 1)")
            await conn.commit()
        bridge = AppBridge(str(tmp_path / "w.db"))
        await bridge.init()
        await bridge.set_setting("discord_delay", "0")
        assert (await bridge.discord_view())["minimal"] is True      # Voreinstellung
        sent, refreshed = [], []
        bot = make_bot(db, bridge, sent, refreshed)
        before = await db.get_pull_tracking(7)
        await db.set_pull_tracking(7, None, None, None, ["hitA"], [])
        await bot._publish_pulls(7, 700, before, "🔥 **Hit gezogen:** A")
        assert sent == []
        assert (await db.get_pull_tracking(7))["pulled"] == ["hitA"]   # App sieht ihn trotzdem

    asyncio.run(run())


def test_minimal_cleanup_selects_only_outdated_posts(env):
    """Minimal räumt alte Bot-Posts auf: Hit-Listen, erkannte Hits, Hit-Chance, Lohnt sich, Endspurt, Admin-Haken
    ohne Person - Medaillen, Pack-Updates und Korrekturen an Medaillen von Personen bleiben."""
    from bot.discord_view import minimal_drop
    drop = ["🏆 **Hits im Pool** (nur Versand) · noch drin: 3 von 5", "🏆 **Top 5 Karten** (Coin-Wert)",
            "🔥 **Hit gezogen:** 🥇 Glurak (50.000 Coins)", "@everyone 🔥 **T1 gezogen:** X",
            "❓ **Möglicher Hit:** Eine Karte …", "🎯 **Hit-Chance:** 3,2 %", "💰 **Lohnt sich** …",
            "@everyone ⚡ **Endspurt** …", "🛠️ T2 als raus abgehakt *(Admin)*", "🛠️ T2: Abhaken aufgehoben *(Admin)*"]
    keep = ["🥇 T1 geht an <@5>!", "↩️ T2 von <@5> zurückgenommen", "📉 **Pack-Update:** 120 → 115 / 500 (-5)",
            "🛠️ T2 an <@6> umgetragen *(Admin)*", "🛠️ T3 von <@5> entfernt *(Admin)*", "@everyone Neuer Banner verfügbar!",
            "🟢 **Verkauf gestartet!** Ab jetzt kann gezogen werden."]
    assert all(minimal_drop(t) for t in drop)
    assert not any(minimal_drop(t) for t in keep)
