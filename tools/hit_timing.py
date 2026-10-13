"""Wo braucht die Versand-Auswertung eines Banners ihre Zeit? Liest nur die Bot-Datenbank.

Aufruf auf dem VPS:
    docker exec -i gtcha-discord-bot python - 24164 < tools/hit_timing.py
"""

import json
import os
import signal
import sqlite3
import sys
import time

sys.path.insert(0, "/app")
from utils import card_pool as cp  # noqa: E402

pid = int(sys.argv[1]) if len(sys.argv) > 1 and sys.argv[1].isdigit() else None
if not pid:
    sys.exit("Banner-ID angeben, z. B.:  docker exec -i gtcha-discord-bot python - 24164 < tools/hit_timing.py")
db = sqlite3.connect(os.getenv("BOT_DB", "/app/data/gtcha_bot.db"))
row = db.execute("SELECT card_pool, ship_batches, price_coins FROM banners WHERE pack_id = ?", (pid,)).fetchone()
if not row or not row[0]:
    sys.exit(f"{pid}: keine Kartenliste")
pool, batches, price = json.loads(row[0]), json.loads(row[1] or "[]"), row[2]
hits = [u for u in cp.tracked_units(pool) if u["shipping_only"]]
classes = cp._value_classes(hits, cp.VALUE_TOLERANCE)
values = sorted(u["value"] for u in hits)
print(f"Banner {pid}: {len(hits)} Versand-Hits in {len(classes)} Wertklassen (größte Klasse "
      f"{max((len(c['keys']) for c in classes), default=0)}), Hit-Werte {values[0] if values else '-'}-"
      f"{values[-1] if values else '-'}, {sum((pool.get('normal_values') or {}).values())} normale Karten in "
      f"{len(pool.get('normal_values') or {})} Werten, Packpreis {price}")
print(f"{len(batches)} Versand-Schübe")


class Late(Exception):
    pass


def alarm(*_):
    raise Late()


signal.signal(signal.SIGALRM, alarm)
for i, b in enumerate(batches):
    count, net = b[0], b[1]
    value = round(net * cp.TAX_FACTOR)
    signal.alarm(60)
    started = time.monotonic()
    try:
        opts = cp._ship_options(pool, classes, count, value, cp.VALUE_TOLERANCE, price) \
            if 0 < count <= cp.MAX_SHIPMENT_CARDS and 0 < value <= cp.MAX_SHIPMENT_VALUE else "übersprungen"
        took = time.monotonic() - started
        n = "zu viele" if opts is None else (opts if isinstance(opts, str) else len(opts))
        print(f"Schub {i + 1:3d}: {count:2d} Karten, {value:>9} Coins -> {n} Aufteilungen ({took:.1f}s)")
    except Late:
        print(f"Schub {i + 1:3d}: {count:2d} Karten, {value:>9} Coins -> nach 60 s NICHT fertig")
    finally:
        signal.alarm(0)

signal.alarm(600)
started = time.monotonic()
try:
    res = cp.match_shipment_history(pool, batches, cp.VALUE_TOLERANCE, {}, price)
    print(f"Gesamt: {time.monotonic() - started:.1f}s, sicher {len(res['certain'])}, ❓-Gruppen {len(res['groups'])}, "
          f"genutzte Schübe {res['used_batches']}/{len(batches)}")
except Late:
    print("Gesamt: nach 10 Minuten NICHT fertig")
