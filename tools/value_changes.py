"""Zeigt, wann GTCHA Kartenwerte geändert hat (Vergleich der alle 6 Stunden neu geladenen Kartenpools).

Aufruf auf dem VPS:
    docker exec -i gtcha-discord-bot python - 24168 < tools/value_changes.py     # ein Banner
    docker exec -i gtcha-discord-bot python - < tools/value_changes.py           # alle, neueste zuerst
"""

import sqlite3
import sys
from datetime import datetime

sys.path.insert(0, "/app")
from utils.banner_info import berlin_time  # noqa: E402
from utils.card_pool import fmt_coins  # noqa: E402

db = sqlite3.connect("/app/data/gtcha_bot.db")
args = sys.argv[1:]
query = "SELECT banner_id, name, old_value, new_value, changed_at FROM card_value_history"
rows = (db.execute(query + " WHERE banner_id = ? ORDER BY id", (int(args[0]),)) if args
        else db.execute(query + " ORDER BY id DESC LIMIT 100")).fetchall()
if not rows:
    print("Noch keine Wertänderungen aufgezeichnet (Pools werden alle 6 Stunden verglichen).")
for bid, name, old, new, at in rows:
    when = berlin_time(int(datetime.fromisoformat(at).timestamp()))
    pct = (new - old) / old * 100 if old else 0
    print(f"{when:%d.%m. %H:%M}  Banner {bid}: {name[:40]:40} {fmt_coins(old):>8} -> {fmt_coins(new):>8}  ({pct:+.1f} %)")
banners = {r[0] for r in rows}
if rows:
    print(f"\n{len(rows)} Änderung(en) in {len(banners)} Banner(n)")
