"""7-Tage-Pflicht: Banner & Co. nur für verknüpfte Geräte, die in den letzten 7 Tagen übertragen haben."""

import asyncio
import os

import aiosqlite


def test_banners_locked_until_synced_admin_exempt(tmp_path, monkeypatch):
    from aiohttp.test_utils import TestClient, TestServer

    from database.db import Database
    from webapp.server import App, make_app

    monkeypatch.setenv("APP_ADMIN_IDS", "1")

    async def run():
        await Database(str(tmp_path / "gtcha_bot.db")).init()
        app = App(str(tmp_path / "gtcha_bot.db"), str(tmp_path), "x")
        client = TestClient(TestServer(make_app(app)))
        await client.start_server()
        try:
            await app.bridge.init()
            user = (await app.bridge.redeem_code(await app.bridge.create_code(42, "Basti")))["token"]
            admin = (await app.bridge.redeem_code(await app.bridge.create_code(1, "Chef")))["token"]

            async def status(path, token=None):
                r = await client.get(path, headers={"X-Device-Token": token} if token else {})
                return r.status, (await r.json() if r.status == 423 else None)

            code, body = await status("/api/banners")
            assert code == 423 and body["locked"]["reason"] == "link"           # nicht verknüpft
            code, body = await status("/api/hot", user)
            assert code == 423 and body["locked"]["reason"] == "sync"           # verknüpft, nie übertragen
            assert (await status("/api/banners", admin))[0] == 200             # Admin immer
            await app.bridge.mark_synced("42")
            for path in ("/api/banners", "/api/hot", "/api/archive", "/api/accuracy", "/api/cards?q=xx"):
                assert (await status(path, user))[0] == 200, path
            assert await app.allowed_users() == {"1", "42"}
            me = await (await client.get("/api/me", headers={"X-Device-Token": user})).json()
            assert me["sync"]["ok"] and 6.9 < me["sync"]["days_left"] <= 7
            # 8 Tage später: wieder gesperrt, keine Banner-Pushes mehr
            async with aiosqlite.connect(app.bridge.db_path) as db:
                await db.execute("UPDATE sync_marks SET synced_at = '2000-01-01T00:00:00'")
                await db.commit()
            assert (await status("/api/banners", user))[0] == 423
            assert await app.allowed_users() == {"1"}
        finally:
            await client.close()

    asyncio.run(run())
    os.environ.pop("APP_ADMIN_IDS", None)
