"""Bot-Datenbank und App-Datenbank laufen im WAL-Modus (Lesen und Schreiben sperren sich nicht gegenseitig)."""

import asyncio
import sqlite3

from database.db import Database
from utils.app_bridge import AppBridge


def test_databases_use_wal(tmp_path):
    async def run():
        await Database(str(tmp_path / "b.db")).init()
        await AppBridge(str(tmp_path / "w.db")).init()

    asyncio.run(run())
    for name in ("b.db", "w.db"):
        assert sqlite3.connect(tmp_path / name).execute("PRAGMA journal_mode").fetchone()[0] == "wal"


def test_history_queries_use_index(tmp_path):
    """Verläufe je Banner und offene Medaillen-Wünsche: per Index, nicht durch die ganze Tabelle."""
    async def run():
        await Database(str(tmp_path / "b.db")).init()
        await AppBridge(str(tmp_path / "w.db")).init()

    asyncio.run(run())
    bot = sqlite3.connect(tmp_path / "b.db")
    app = sqlite3.connect(tmp_path / "w.db")

    def plan(conn, sql, *args):
        return " ".join(str(r[-1]) for r in conn.execute("EXPLAIN QUERY PLAN " + sql, args))

    for table in ("pack_history", "shipment_history", "convert_history", "card_value_history"):
        assert "USING INDEX" in plan(bot, f"SELECT * FROM {table} WHERE banner_id = ? ORDER BY id", 1), table
    assert "USING" in plan(bot, "DELETE FROM api_log WHERE changed_at < ?", "x")
    assert "USING" in plan(app, "SELECT * FROM medal_requests WHERE status = 'pending' ORDER BY id")
    assert "USING" in plan(app, "SELECT * FROM user_imports WHERE discord_user_id = ? ORDER BY id DESC LIMIT 5", "1")
