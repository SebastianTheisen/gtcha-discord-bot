"""Premium-Foren: jeder Banner zweimal - normal (minimal) und Premium (alle Infos, zeitversetzt).
Medaillen hängen am normalen Thread und erscheinen in beiden."""

import asyncio
import time

import aiosqlite
import pytest

from database.db import Database
from utils.app_bridge import AppBridge

MAIN, PREMIUM = 700, 900


@pytest.fixture
def env(monkeypatch):
    monkeypatch.setenv("DISCORD_TOKEN", "x")
    monkeypatch.setenv("GUILD_ID", "1")
    import bot.common
    monkeypatch.setitem(bot.common.PREMIUM_CHANNEL_IDS, "Pokémon", 555)


def make_bot(db, bridge, sent):
    import discord

    from bot.discord_view import DiscordViewMixin
    from bot.medals import MedalsMixin

    class Thread(discord.Thread):
        archived = False

        def __init__(self, tid):
            self.id = tid

        async def send(self, text, **kw):
            sent.append((self.id, text))

        async def fetch_message(self, _id):
            raise RuntimeError("kein Startbeitrag im Test")

    class Bot(MedalsMixin, DiscordViewMixin):
        def __init__(self):
            self.db, self._app_bridge = db, bridge

        @property
        def app_bridge(self):
            return self._app_bridge

        def get_channel(self, tid):
            return Thread(tid)

        async def _refresh_pool_views(self, pid, **kw):
            pass

        async def _sync_thread_title(self, pid):
            pass

        async def _update_probability_message(self, *a):
            pass

        async def _set_starter_reaction(self, *a, **kw):
            pass

        async def process_commands(self, message):
            pass

    return Bot()


async def setup(tmp_path):
    db = Database(str(tmp_path / "b.db"))
    await db.init()
    async with aiosqlite.connect(db.db_path) as conn:
        await conn.execute("INSERT INTO banners (pack_id, is_active, category) VALUES (7, 1, 'Pokémon')")
        await conn.execute("INSERT INTO discord_threads (banner_id, thread_id) VALUES (7, ?)", (MAIN,))
        await conn.commit()
    bridge = AppBridge(str(tmp_path / "w.db"))
    await bridge.init()
    await bridge.set_setting("discord_mode", "minimal")
    await bridge.set_setting("discord_delay", "30")
    return db, bridge


def test_missing_premium_threads_are_found_and_backfilled_once(tmp_path, env):
    async def run():
        db, _ = await setup(tmp_path)
        assert await db.premium_missing() == [7]
        await db.save_premium_thread(7, PREMIUM, 555, PREMIUM)
        assert await db.premium_missing() == []
        await db.mark_thread_expired(7)      # Banner beendet: beide Threads abgelaufen
        assert (await db.get_premium_thread(7))["is_expired"] == 1

    asyncio.run(run())


def test_detected_hits_only_in_premium_after_delay(tmp_path, env):
    async def run():
        db, bridge = await setup(tmp_path)
        await db.save_premium_thread(7, PREMIUM, 555, PREMIUM)
        sent = []
        bot = make_bot(db, bridge, sent)
        assert (await bot._view("premium"))["delay"] == 30 * 60 and not await bot._minimal("premium")
        assert await bot._minimal("main")
        before = await db.get_pull_tracking(7)
        await db.set_pull_tracking(7, None, None, None, ["hitA"], [])
        await bot._publish_pulls(7, MAIN, {"pulled": before["pulled"], "unsure": before["unsure"]}, "🔥 Hit A")
        assert sent == []                                        # erst nach der Verzögerung
        async with aiosqlite.connect(db.db_path) as conn:
            await conn.execute("UPDATE discord_outbox SET send_at = ?", (time.time() - 1,))
            await conn.commit()
        await bot._process_discord_outbox()
        assert sent == [(PREMIUM, "🔥 Hit A")]                    # nur Premium, das normale Forum ist minimal

        sent.clear()
        assert await bot._post_to_threads(7, "📉 Pack-Update")   # Pack-Updates in beide
        assert sent == [(MAIN, "📉 Pack-Update"), (PREMIUM, "📉 Pack-Update")]

    asyncio.run(run())


def test_medal_in_premium_thread_counts_for_both(tmp_path, env):
    async def run():
        import discord

        db, bridge = await setup(tmp_path)
        await db.save_premium_thread(7, PREMIUM, 555, PREMIUM)
        sent = []
        bot = make_bot(db, bridge, sent)
        replies = []

        class Message:
            content = "T1 endlich!"
            author = type("A", (), {"bot": False, "id": 42, "name": "x", "mention": "<@42>"})()
            channel = bot.get_channel(PREMIUM)
            channel.parent_id, channel.name = 555, "Premium"

            async def reply(self, text):
                replies.append(text)

            async def add_reaction(self, _e):
                pass

        await bot.on_message(Message())
        assert await db.get_medals(MAIN) == {"T1": 42}               # am normalen Thread gespeichert
        assert await bot._public_medals(PREMIUM) == {"T1": 42}       # Premium liest dieselben Medaillen
        assert replies == ["🥇 T1 geht an <@42>!"]
        assert sent == [(MAIN, "🥇 T1 geht an <@42>!")]              # im normalen Forum gespiegelt
        await bot.on_message(Message())
        assert "bereits" in replies[-1]                              # zählt nur einmal
        assert isinstance(Message.channel, discord.Thread)

    asyncio.run(run())


def test_announcement_posted_once_at_time(tmp_path, env):
    from datetime import datetime

    from utils.banner_info import berlin_time, berlin_to_ts

    local = datetime(2026, 10, 12, 0, 14)
    assert berlin_time(berlin_to_ts(local)).strftime("%d.%m. %H:%M") == "12.10. 00:14"   # Sommerzeit
    assert berlin_time(berlin_to_ts(datetime(2026, 12, 1, 0, 14))).strftime("%H:%M") == "00:14"   # Winterzeit

    async def run():
        db, bridge = await setup(tmp_path)
        await db.save_premium_thread(7, PREMIUM, 555, PREMIUM)
        sent = []
        bot = make_bot(db, bridge, sent)
        later = await bridge.add_announcement("später", time.time() + 3600, "main", False, "Admin")
        await bridge.add_announcement("Hinweis", time.time() - 1, "main", False, "Admin")
        await bot._process_announcements()
        assert sent == [(MAIN, "Hinweis")]                   # nur normales Forum, nur die fällige
        await bot._process_announcements()
        assert sent == [(MAIN, "Hinweis")]                   # nur einmal
        assert await bridge.cancel_announcement(later)
        rows = {r["text"]: r for r in await bridge.announcements()}
        assert rows["Hinweis"]["status"] == "sent" and rows["Hinweis"]["posted"] == 1
        assert rows["später"]["status"] == "cancelled"

    asyncio.run(run())


def test_announcement_also_for_new_banners_until_stopped(tmp_path, env):
    async def run():
        db, bridge = await setup(tmp_path)
        sent = []
        bot = make_bot(db, bridge, sent)
        ann = await bridge.add_announcement("Hinweis", time.time() - 1, "main", False, "Admin", also_new=True)
        await bot._process_announcements()
        assert sent == [(MAIN, "Hinweis")]
        async with aiosqlite.connect(db.db_path) as conn:      # neuer Banner nach dem Zeitpunkt
            await conn.execute("INSERT INTO banners (pack_id, is_active, category) VALUES (8, 1, 'Pokémon')")
            await conn.execute("INSERT INTO discord_threads (banner_id, thread_id) VALUES (8, 701)")
            await conn.commit()
        await bot._process_announcements()
        await bot._process_announcements()
        assert sent == [(MAIN, "Hinweis"), (701, "Hinweis")]   # neuer Thread genau einmal
        assert await bridge.cancel_announcement(ann)             # beenden
        assert (await bridge.announcements())[0]["status"] == "stopped"
        async with aiosqlite.connect(db.db_path) as conn:
            await conn.execute("INSERT INTO banners (pack_id, is_active, category) VALUES (9, 1, 'Pokémon')")
            await conn.execute("INSERT INTO discord_threads (banner_id, thread_id) VALUES (9, 702)")
            await conn.commit()
        await bot._process_announcements()
        assert len(sent) == 2                                    # nach dem Beenden nichts mehr

    asyncio.run(run())
