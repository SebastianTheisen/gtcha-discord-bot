import asyncio


def test_converted_history(tmp_path, monkeypatch):
    monkeypatch.setenv("DISCORD_TOKEN", "x")
    from database.db import Database
    import aiosqlite

    async def run():
        db = Database(str(tmp_path / "t.db"))
        await db.init()
        async with aiosqlite.connect(db.db_path) as conn:
            await conn.execute("INSERT INTO banners (pack_id, is_active) VALUES (1, 1)")
            await conn.commit()
        assert await db.update_converted(1, 1000) == 1000        # erster Stand: kein Verlauf
        assert await db.update_converted(1, 1000) is None        # unverändert
        assert await db.update_converted(1, 1660) == 660
        assert await db.update_converted(99, 5) is None          # unbekannter Banner
        async with aiosqlite.connect(db.db_path) as conn:
            rows = await (await conn.execute("SELECT old_coins, new_coins FROM convert_history")).fetchall()
        assert rows == [(1000, 1660)]

    asyncio.run(run())


def test_value_change_history(tmp_path, monkeypatch):
    monkeypatch.setenv("DISCORD_TOKEN", "x")
    from database.db import Database
    import aiosqlite

    async def run():
        db = Database(str(tmp_path / "v.db"))
        await db.init()
        await db.save_value_changes(7, [{"id": "1", "name": "A", "old": 14000, "new": 15400}])
        await db.save_value_changes(7, [])
        async with aiosqlite.connect(db.db_path) as conn:
            rows = await (await conn.execute("SELECT banner_id, card_id, old_value, new_value FROM card_value_history")).fetchall()
        assert rows == [(7, "1", 14000, 15400)]

    asyncio.run(run())
