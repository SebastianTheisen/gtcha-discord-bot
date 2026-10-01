"""Beobachtet die Zähler eines Banners und protokolliert jede Änderung mit Uhrzeit.

Jede Abfrage: neue Tor-Route, frischer Browser-Kontext ohne Cookies (neue Sitzung), dann
pack/list und pack/detail. Läuft unabhängig vom Bot, auch wenn dessen Thread gelöscht ist.

Start im Hintergrund (läuft bis zum Container-Neustart oder bis Ctrl+C beim Vordergrund-Start):
    docker exec -d gtcha-discord-bot python /app/tools/watch_banner.py 24147
Mitlesen:
    tail -f ~/gtcha-discord-bot/data/watch_24147.log
Beenden:
    docker exec gtcha-discord-bot pkill -f watch_banner.py
"""

import asyncio
import json
import os
import sys
from datetime import datetime

sys.path.insert(0, "/app")
from utils.banner_info import berlin_time  # noqa: E402
from utils.maintenance import new_tor_identity  # noqa: E402
from utils.pack_list_client import PackListClient  # noqa: E402

FIELDS = ("pack_count", "total_sendcount", "total_sendprice", "total_sendpeople", "total_kangen")
INTERVAL = int(os.getenv("WATCH_INTERVAL") or "60")


def log(path: str, text: str):
    line = f"{berlin_time(int(datetime.now().timestamp())):%d.%m. %H:%M:%S}  {text}"
    print(line, flush=True)
    with open(path, "a") as f:
        f.write(line + "\n")


async def main():
    pid = int(sys.argv[1])
    path = f"/app/data/watch_{pid}.log"
    client = PackListClient("https://gtchaxonline.com", os.getenv("SCRAPER_PROXY"))
    last = None
    log(path, f"Beobachte Banner {pid} alle {INTERVAL} s")
    try:
        while True:
            try:
                await new_tor_identity()
                item = (await client.fetch()).get(pid)
                if item is None:
                    log(path, "Banner nicht mehr in pack/list")
                else:
                    now = {k: item.get(k) for k in FIELDS}
                    if now != last:
                        changes = ", ".join(f"{k}: {last.get(k) if last else '-'} -> {v}"
                                            for k, v in now.items() if not last or last.get(k) != v)
                        log(path, f"ÄNDERUNG {changes}")
                        last = now
            except Exception as e:
                log(path, f"Fehler: {type(e).__name__}: {e}")
            await asyncio.sleep(INTERVAL)
    finally:
        await client.close()


if __name__ == "__main__":
    asyncio.run(main())
