"""Alle Bannerbilder neu laden: löscht die zwischengespeicherten Bannerbilder (samt verkleinerten Kopien), die App
holt sie danach frisch von GTCHA. Kartenbilder bleiben. Bannerbild-Adressen übernimmt der Bot ohnehin bei jedem
Abruf aus pack/list (inkl. Version ?v=…, und "kein Bild" bei neuen Bannern - dann zeigt die App die Top-Karten).

Aufruf auf dem VPS:
    docker exec -i gtcha-discord-bot python - < tools/refresh_banner_images.py
"""

import os
import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, "/app")
from webapp.images import cache_name  # noqa: E402

DATA = "/app/data"
cache = Path(DATA) / "img_cache"
db = sqlite3.connect(os.path.join(DATA, "gtcha_bot.db"))
urls = {u for (u,) in db.execute("SELECT image_url FROM banners WHERE image_url IS NOT NULL AND image_url != ''")}
# auch die frühere Form ohne ?v=… (bis Oktober 2026 gespeichert)
urls |= {u.split("?")[0] for u in urls}
removed = 0
for url in urls:
    stem = Path(cache_name(url)).stem
    for f in cache.glob(stem + "*"):
        try:
            f.unlink()
            removed += 1
        except OSError:
            pass
print(f"{len(urls)} Bannerbild-Adressen, {removed} Dateien im Zwischenspeicher gelöscht - werden beim nächsten Aufruf "
      f"frisch geladen.")
no_image = db.execute("SELECT count(*) FROM banners WHERE is_active = 1 AND (image_url IS NULL OR image_url = '')").fetchone()[0]
print(f"Aktive Banner ohne eigenes Bild (App zeigt die Top-Karten): {no_image}")
