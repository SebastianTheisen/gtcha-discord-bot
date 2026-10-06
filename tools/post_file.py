"""Eine Datei (z. B. eine Analyse) über den Bot in den Admin-Kanal von Discord posten.

Die Datei muss im Ordner data/ liegen (der ist im Container unter /app/data sichtbar).

Aufruf auf dem VPS:
    cp ~/analyse-24188.txt ~/gtcha-discord-bot/data/
    docker exec -i gtcha-discord-bot python - analyse-24188.txt < tools/post_file.py
"""

import asyncio
import json
import os
import sys

import aiohttp

name = os.path.basename(sys.argv[1]) if len(sys.argv) > 1 else ""
path = os.path.join("/app/data", name)
token = os.getenv("DISCORD_TOKEN", "")
channel = os.getenv("ADMIN_CHANNEL_ID", "")
if not name or not os.path.isfile(path):
    sys.exit(f"Datei nicht gefunden: data/{name} – zuerst nach ~/gtcha-discord-bot/data/ kopieren")
if not token or not channel.isdigit():
    sys.exit("DISCORD_TOKEN oder ADMIN_CHANNEL_ID fehlt in der .env")
if os.path.getsize(path) > 8 * 1024 * 1024:
    sys.exit("Datei größer als 8 MB – Discord nimmt sie nicht an")


async def main():
    form = aiohttp.FormData()
    form.add_field("payload_json", json.dumps({"content": f"📎 {name}", "allowed_mentions": {"parse": []}}))
    with open(path, "rb") as f:
        form.add_field("files[0]", f.read(), filename=name, content_type="text/plain")
    async with aiohttp.ClientSession() as session:
        async with session.post(f"https://discord.com/api/v10/channels/{channel}/messages", data=form,
                                headers={"Authorization": f"Bot {token}"}) as resp:
            print("✅ gepostet" if resp.status == 200 else f"❌ Discord antwortet {resp.status}: {(await resp.text())[:300]}")


asyncio.run(main())
