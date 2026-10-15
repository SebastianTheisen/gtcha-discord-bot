"""Discord-Pause: Hinweis statt Updates, danach gar nichts - Datenbank und Apps laufen weiter."""

import asyncio
import time
from types import SimpleNamespace

from bot.discord_view import DISCORD_ALLOW, DiscordPaused, DiscordViewMixin
from utils.app_bridge import DEFAULT_PAUSE_NOTICE, AppBridge


def test_pause_states(tmp_path):
    async def run():
        b = AppBridge(str(tmp_path / "w.db"))
        await b.init()
        on = await b.discord_pause()
        await b.set_setting("discord_stop_at", str(int(time.time()) + 6 * 3600))
        notice = await b.discord_pause()
        off = await b.discord_pause(now=time.time() + 7 * 3600)
        return on, notice, off

    on, notice, off = asyncio.run(run())
    assert on["state"] == "on" and notice["state"] == "notice" and off["state"] == "off"
    assert notice["notice"] == DEFAULT_PAUSE_NOTICE


class Bot(DiscordViewMixin):
    def __init__(self, state):
        self.sent, self.requests = [], []
        self._pause = {"state": state, "notice": "Hinweis", "read": time.time()}

        async def request(route, **kw):
            self.requests.append((route.method, route.path))
            return {}
        self.http = SimpleNamespace(request=request)

    async def _banner_threads(self, pack_id):
        return [({"thread_id": 111}, "main"), ({"thread_id": 222}, "premium")]

    async def _open_thread(self, tid):
        bot = self

        class Thread:
            async def send(self, text):
                # Hinweis darf durch die Sperre (DISCORD_ALLOW), alles andere nicht
                await bot.http.request(SimpleNamespace(method="POST", path=f"/channels/{tid}/messages"))
                bot.sent.append((tid, text))
        return Thread()


def test_guard_blocks_writes_but_not_reads_interactions_or_the_notice():
    async def run():
        bot = Bot("notice")
        bot._install_discord_guard()
        route = lambda m, p: SimpleNamespace(method=m, path=p)
        await bot.http.request(route("GET", "/channels/1/messages"))
        await bot.http.request(route("POST", "/interactions/1/abc/callback"))   # /tracker-verknüpfen bleibt
        blocked = []
        for m, p in (("POST", "/channels/1/messages"), ("PATCH", "/channels/1/messages/2"),
                     ("PUT", "/channels/1/messages/2/reactions/x/@me"), ("DELETE", "/channels/1")):
            try:
                await bot.http.request(route(m, p))
            except DiscordPaused:
                blocked.append(m)
        token = DISCORD_ALLOW.set(True)
        await bot.http.request(route("POST", "/channels/1/messages"))
        DISCORD_ALLOW.reset(token)
        return blocked, bot.requests

    blocked, passed = asyncio.run(run())
    assert blocked == ["POST", "PATCH", "PUT", "DELETE"]
    assert ("GET", "/channels/1/messages") in passed and ("POST", "/channels/1/messages") in passed


def test_notice_instead_of_update_at_most_every_10_minutes_then_nothing():
    async def run():
        bot = Bot("notice")
        bot._install_discord_guard()
        first = await bot._discord_paused(24164)
        await bot._discord_paused(24164)         # gleich danach: kein zweiter Hinweis
        notice_sent = list(bot.sent)
        bot._pause["state"] = "off"
        bot._pause_notice_at = {}
        await bot._discord_paused(24164)         # aus: gar nichts mehr
        return first, notice_sent, bot.sent

    first, notice_sent, all_sent = asyncio.run(run())
    assert first is True                          # Aufrufer speichern trotzdem (Pack-Zahl, Banner, Medaille)
    assert notice_sent == [(111, "Hinweis"), (222, "Hinweis")] and all_sent == notice_sent


def test_discord_on_changes_nothing():
    async def run():
        bot = Bot("on")
        bot._install_discord_guard()
        await bot.http.request(SimpleNamespace(method="POST", path="/channels/1/messages"))
        return await bot._discord_paused(1), bot.sent

    assert asyncio.run(run()) == (False, [])


def test_medal_for_banner_without_thread_is_stored_internally(tmp_path, monkeypatch):
    """Während der Pause angelegter Banner hat keinen Thread: Medaille aus der App trotzdem gespeichert."""
    monkeypatch.setenv("DISCORD_TOKEN", "x")
    monkeypatch.setenv("GUILD_ID", "1")
    from bot.app_link import AppLinkMixin
    from bot.medals import MedalsMixin
    from database.db import Database, store_thread_id
    from scraper.models import ScrapedBanner
    from utils.card_pool import summarize_cards

    class MedalBot(AppLinkMixin, MedalsMixin):
        def __init__(self, db, bridge):
            self.db, self._app_bridge = db, bridge

    async def run():
        db = Database(str(tmp_path / "b.db"))
        await db.init()
        bridge = AppBridge(str(tmp_path / "w.db"))
        await bridge.init()
        await db.save_banner(ScrapedBanner(pack_id=24300, category="Pokemon", title="Neu", price_coins=1000,
                                           current_packs=90, total_packs=100))
        await db.save_card_pool(24300, summarize_cards([
            {"id": 1, "name": "Lugia", "buy_point": 90000, "duplication": 1, "action_type": 0},
            {"id": 2, "name": "Normal", "buy_point": 300, "duplication": 99, "action_type": 0}]))
        bot = MedalBot(db, bridge)
        rid = await bridge.add_request(24300, "T1", {"user_id": "42", "name": "Basti"}, "claim")
        await bot._process_app_requests()
        return (await bridge.get_request(rid))["status"], await db.get_medals(store_thread_id(24300))

    status, medals = asyncio.run(run())
    assert status == "ok" and medals == {"T1": 42}


def test_random_message_per_line_at_irregular_intervals():
    """Mehrere Zeilen: je Thread eine zufällige davon, nächste frühestens nach 5 Min (höchstens 25)."""
    async def run():
        bot = Bot("notice")
        bot._pause["notice"] = "⚠️ Timeout\n\n❌ Error\n🔄 Reconnecting"
        bot._install_discord_guard()
        await bot._discord_paused(1)
        waits = [t - time.time() for t in bot._pause_notice_at.values()]
        return bot.sent, waits

    sent, waits = asyncio.run(run())
    assert len(sent) == 2 and all(text in ("⚠️ Timeout", "❌ Error", "🔄 Reconnecting") for _, text in sent)
    assert all(5 * 60 - 1 <= w <= 25 * 60 for w in waits)
