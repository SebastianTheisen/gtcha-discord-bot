"""Exportiert den letzten "Alles übertragen"-Lauf in eine Datei (zum Weitergeben an die Entwicklung).

Prüft dabei für jedes Kartenbild (/card/<ID>_small.jpg), in welchem bekannten Banner-Pool die Karte vorkommt.

Aufruf auf dem VPS:
    docker exec -i gtcha-app python - < tools/export_import.py
Ergebnis: ~/gtcha-discord-bot/data/gtcha_export.json
"""

import json
import re
import sqlite3
import sys

app_db = sqlite3.connect("/app/data/webapp.db")
last = app_db.execute("SELECT max(created_at) FROM user_imports WHERE kind = 'sync'").fetchone()[0]
if not last:
    sys.exit("Noch kein „Alles übertragen“.")
rows = app_db.execute("SELECT url, data, created_at FROM user_imports WHERE kind = 'sync' AND created_at >= ? ORDER BY id",
                      (last[:16],)).fetchall()

# Karten-ID -> Banner (aus den Kartenpools des Bots)
card_banners = {}
bot_db = sqlite3.connect("/app/data/gtcha_bot.db")
for pack_id, pool in bot_db.execute("SELECT pack_id, card_pool FROM banners WHERE card_pool IS NOT NULL"):
    try:
        for card in json.loads(pool).get("cards") or []:
            card_banners.setdefault(str(card.get("id")), []).append(
                {"banner": pack_id, "name": card.get("name"), "value": card.get("value")})
    except ValueError:
        continue

export = {"exported_from": last, "areas": []}
found = missing = 0
for url, data, created in rows:
    entry = json.loads(data)
    for page in entry.get("pages") or []:
        matches = []
        for src in page.get("images") or []:
            m = re.search(r"/card/(\d+)", src or "")
            if not m:
                continue
            hits = card_banners.get(m.group(1), [])
            found += bool(hits)
            missing += not hits
            matches.append({"image": src, "card_id": m.group(1), "in_banners": hits})
        page["card_matches"] = matches
    export["areas"].append({"path": url.rsplit("/", 1)[-1], **entry})

with open("/app/data/gtcha_export.json", "w", encoding="utf-8") as f:
    json.dump(export, f, ensure_ascii=False, indent=1)
pages = sum(len(a.get("pages") or []) for a in export["areas"])
print(f"Gespeichert: data/gtcha_export.json ({len(export['areas'])} Bereiche, {pages} Seiten)")
print(f"Kartenbilder einem bekannten Banner zugeordnet: {found}, unbekannt: {missing}")
