"""Zeigt, wann die Seite Versände gezählt hat (aus der Bot-Datenbank).

Aufruf auf dem VPS:
    docker exec -i gtcha-discord-bot python - 24111 < tools/shipments.py     # ein Banner
    docker exec -i gtcha-discord-bot python - < tools/shipments.py           # alle, neueste zuerst
"""

import sqlite3
import sys
from datetime import datetime

sys.path.insert(0, "/app")
from utils.banner_info import berlin_time  # noqa: E402

db = sqlite3.connect("/app/data/gtcha_bot.db")
args = sys.argv[1:]
query = ("SELECT banner_id, old_cards, new_cards, old_coins, new_coins, old_players, new_players, changed_at "
         "FROM shipment_history")
rows = (db.execute(query + " WHERE banner_id = ? ORDER BY id", (int(args[0]),)) if args
        else db.execute(query + " ORDER BY id DESC LIMIT 50")).fetchall()
if not rows:
    print("Noch keine Versände aufgezeichnet.")
for bid, oc, nc, ocoins, ncoins, op, np_, at in rows:
    when = berlin_time(int(datetime.fromisoformat(at).timestamp()))
    coins = f"+{(ncoins or 0) - (ocoins or 0):,}".replace(",", ".")
    print(f"{when:%d.%m. %H:%M:%S}  Banner {bid}: {oc} -> {nc} Karten ({coins} Coins), Spieler {op} -> {np_}")
