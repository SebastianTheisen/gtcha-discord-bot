"""Postet data/gtcha_export.json (aus tools/export_import.py) als Datei in den Admin-Kanal.

Nutzt den Bot-Zugang (DISCORD_TOKEN, ADMIN_CHANNEL_ID aus der .env des Bot-Containers).
Große Dateien werden gezippt (Discord-Limit 10 MB).

Aufruf auf dem VPS:
    docker exec -i gtcha-discord-bot python - < tools/post_export.py
"""

import asyncio
import gzip
import json
import os
import sys

import aiohttp

PATH = "/app/data/gtcha_export.json"
LIMIT = 9_500_000


async def main():
    token, channel = os.getenv("DISCORD_TOKEN"), os.getenv("ADMIN_CHANNEL_ID")
    if not token or not channel:
        sys.exit("DISCORD_TOKEN oder ADMIN_CHANNEL_ID fehlt in der .env")
    if not os.path.exists(PATH):
        sys.exit("Keine Datei - zuerst: docker exec -i gtcha-app python - < tools/export_import.py")
    raw = open(PATH, "rb").read()
    name, data = "gtcha_export.json", raw
    if len(raw) > LIMIT:
        name, data = "gtcha_export.json.gz", gzip.compress(raw)
    if len(data) > LIMIT:
        sys.exit(f"Datei zu groß für Discord ({len(data) // 1_000_000} MB)")
    info = json.loads(raw)
    pages = sum(len(a.get("pages") or []) for a in info.get("areas") or [])
    form = aiohttp.FormData()
    form.add_field("payload_json", json.dumps({
        "content": f"📥 GTCHA-Export vom {str(info.get('exported_from', ''))[:16]} · "
                   f"{len(info.get('areas') or [])} Bereiche, {pages} Seiten"}))
    form.add_field("files[0]", data, filename=name, content_type="application/octet-stream")
    async with aiohttp.ClientSession() as session:
        async with session.post(f"https://discord.com/api/v10/channels/{channel}/messages",
                                headers={"Authorization": f"Bot {token}"}, data=form) as resp:
            if resp.status >= 300:
                sys.exit(f"Discord-Fehler {resp.status}: {(await resp.text())[:300]}")
    print(f"Gepostet in den Admin-Kanal: {name} ({len(data) // 1024} KB)")


asyncio.run(main())
