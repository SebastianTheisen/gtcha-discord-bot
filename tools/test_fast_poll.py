"""Prüft den schnellen Abruf gegen die echte Seite und vergleicht mit der Datenbank.

Aufruf auf dem VPS:
    docker exec -i gtcha-discord-bot python - < tools/test_fast_poll.py
"""

import asyncio
import os
import sqlite3
import sys
import time

sys.path.insert(0, "/app")
from utils.pack_list_client import PackListClient  # noqa: E402


async def main():
    client = PackListClient("https://gtchaxonline.com", os.getenv("SCRAPER_PROXY"))
    db = sqlite3.connect("/app/data/gtcha_bot.db")
    try:
        for run in (1, 2, 3):
            started = time.time()
            items = await client.fetch()
            rows = dict(db.execute("SELECT pack_id, current_packs FROM banners WHERE is_active = 1").fetchall())
            diff = [(pid, rows[pid], int(float(it.get("pack_count") or 0))) for pid, it in items.items()
                    if pid in rows and rows[pid] != int(float(it.get("pack_count") or 0))]
            print(f"Abruf {run}: {len(items)} Banner in {time.time() - started:.1f}s | "
                  f"Abweichungen (Banner, DB, Seite): {diff or 'keine'}")
            await asyncio.sleep(5)
    finally:
        await client.close()


asyncio.run(main())
