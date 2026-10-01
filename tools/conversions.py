"""Zeigt, wann Karten eines Banners in Coins umgewandelt wurden (aus der Bot-Datenbank).

Zu jedem Sprung steht, ob er zu einem Kartenwert des Banners passt - einmal so wie geliefert
und einmal ×1,1. So lässt sich prüfen, ob die Seite Umwandlungen mit oder ohne Steuer zählt.

Aufruf auf dem VPS:
    docker exec -i gtcha-discord-bot python - 24060 < tools/conversions.py     # ein Banner
    docker exec -i gtcha-discord-bot python - < tools/conversions.py           # alle, neueste zuerst
"""

import json
import sqlite3
import sys
from datetime import datetime

sys.path.insert(0, "/app")
from utils.banner_info import berlin_time  # noqa: E402
from utils.card_pool import TAX_FACTOR, fmt_coins  # noqa: E402

db = sqlite3.connect("/app/data/gtcha_bot.db")
args = sys.argv[1:]
query = "SELECT banner_id, old_coins, new_coins, changed_at FROM convert_history"
rows = (db.execute(query + " WHERE banner_id = ? ORDER BY id", (int(args[0]),)) if args
        else db.execute(query + " ORDER BY id DESC LIMIT 50")).fetchall()
if not rows:
    print("Noch keine Umwandlungen aufgezeichnet.")

values = {}


def card_values(bid):
    if bid not in values:
        row = db.execute("SELECT card_pool FROM banners WHERE pack_id = ?", (bid,)).fetchone()
        pool = json.loads(row[0]) if row and row[0] else {}
        values[bid] = {c["value"] for c in pool.get("cards") or []}
    return values[bid]


def near(value, candidates):
    return any(abs(value - c) <= max(1, c * 0.01) for c in candidates)


exact = taxed = 0
for bid, old, new, at in rows:
    when = berlin_time(int(datetime.fromisoformat(at).timestamp()))
    delta = (new or 0) - (old or 0)
    vals = card_values(bid)
    hint = ""
    if vals:
        if near(delta, vals):
            hint, exact = "= ein Kartenwert", exact + 1
        elif near(round(delta * TAX_FACTOR), vals):
            hint, taxed = f"= Kartenwert ÷ 1,1 ({fmt_coins(round(delta * TAX_FACTOR))})", taxed + 1
        else:
            hint = "mehrere Karten oder kein einzelner Wert"
    print(f"{when:%d.%m. %H:%M:%S}  Banner {bid}: +{fmt_coins(delta)} Coins umgewandelt  {hint}")
if exact or taxed:
    print(f"\nPasst zu Kartenwert: {exact}x  |  passt zu Kartenwert ÷ 1,1: {taxed}x")
