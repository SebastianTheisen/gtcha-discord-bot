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
