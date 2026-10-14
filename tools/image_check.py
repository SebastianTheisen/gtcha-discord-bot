"""Welches Bild hat ein Banner laut Seite, was ist gespeichert, was zeigt die App? (liest pack/list wie der Bot)

Aufruf auf dem VPS:
    docker exec -i gtcha-discord-bot python - 24260 < tools/image_check.py
"""

import asyncio
import hashlib
import os
import sqlite3
import sys
import urllib.request

sys.path.insert(0, "/app")
from config import BASE_URL, SCRAPER_PROXY  # noqa: E402
from utils.banner_info import card_key, versioned_image  # noqa: E402
from utils.pack_list_client import PackListClient  # noqa: E402
from webapp.images import cache_name  # noqa: E402

pid = int(sys.argv[1]) if len(sys.argv) > 1 and sys.argv[1].isdigit() else None
if not pid:
    sys.exit("Banner-ID angeben, z. B.:  docker exec -i gtcha-discord-bot python - 24260 < tools/image_check.py")
data = os.path.dirname(os.getenv("BOT_DB", "/app/data/gtcha_bot.db"))
db = sqlite3.connect(os.path.join(data, "gtcha_bot.db"))
row = db.execute("SELECT image_url, starts_at, card_pool, is_active FROM banners WHERE pack_id = ?", (pid,)).fetchone()
print(f"Banner {pid}")
if not row:
    sys.exit("  nicht in der Datenbank")
stored, starts_at, pool, active = row
app_url = versioned_image(stored, starts_at, card_key(pool))
print(f"  aktiv: {active} · Verkaufsstart gespeichert: {starts_at or 'FEHLT'}")
print(f"  gespeichert: {stored}")
print(f"  App nutzt:   {app_url}")


def sha(b: bytes) -> str:
    return hashlib.sha1(b).hexdigest()[:10]


def cached(url):
    path = os.path.join(data, "img_cache", cache_name(url)) if url else None
    return open(path, "rb").read() if path and os.path.exists(path) else None


for label, url in (("App-Zwischenspeicher (neue Adresse)", app_url), ("Zwischenspeicher (alte Adresse)", stored)):
    b = cached(url)
    print(f"  {label}: {sha(b) + f' ({len(b)} Bytes)' if b else 'nicht vorhanden'}")


async def site_images():
    client = PackListClient(BASE_URL, SCRAPER_PROXY, fresh_browser=True)
    try:
        items = await client.fetch()
    finally:
        await client.close()
    return (items or {}).get(pid)

item = asyncio.run(site_images())
if not item:
    sys.exit("  pack/list: Banner nicht (mehr) in der Liste")
images = item.get("image")
print(f"  pack/list 'image': {images!r}")
print(f"  pack/list Start: {item.get('start_date')} · Name: {item.get('name')}")
for img in images if isinstance(images, list) else [images]:
    if not img:
        continue
    url = img if str(img).startswith("http") else f"{BASE_URL}{img}"
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        body = urllib.request.urlopen(req, timeout=20).read()
        print(f"  frisch von der Seite {url}: {sha(body)} ({len(body)} Bytes)")
    except Exception as e:
        print(f"  frisch von der Seite {url}: Fehler {e}")
print("Gleicher Fingerabdruck = gleiches Bild.")
