"""Prüft den schnellen Abruf (ohne Browser) gegen die echte Seite und vergleicht mit der Datenbank.

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
    print(f"Proxy: {client.proxy}")
    started = time.time()
    try:
        await client._new_session()
        print(f"Sitzung geholt in {time.time() - started:.1f}s, Cookie-Datei: "
              f"{open(client.cookie_file).read().count(chr(9))} Einträge")
        body = await client._curl("https://gtchaxonline.com/api/user/pack/list", ["-H", "Accept: application/json"])
        print(f"Antwort ({len(body)} Zeichen): {body[:200]!r}")
    except Exception as e:
        print(f"FEHLER: {type(e).__name__}: {e}")
        return
    items = await client.fetch()
    print(f"\nfetch(): {len(items)} Banner in {time.time() - started:.1f}s")
    db = sqlite3.connect("/app/data/gtcha_bot.db")
    rows = dict(db.execute("SELECT pack_id, current_packs FROM banners WHERE is_active = 1").fetchall())
    diff = [(pid, rows[pid], int(float(it.get("pack_count") or 0))) for pid, it in items.items()
            if pid in rows and rows[pid] != int(float(it.get("pack_count") or 0))]
    print(f"Abweichungen DB <-> Seite: {diff or 'keine'}")


asyncio.run(main())
