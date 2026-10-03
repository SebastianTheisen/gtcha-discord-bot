"""Vergleicht zwei Modelle für die Versand-Zähler der Seite an allen gespeicherten Versand-Schüben.

  heute:     total_sendcount = Anzahl Karten, Wert auf ±1 % (so erkennt der Bot heute)
  Aufträge:  total_sendcount = Anzahl Versand-Aufträge; ein Auftrag hat beliebig viele Karten,
             dafür muss der Wert (fast) exakt aufgehen

Für jeden Banner mit Versand-Hits: wie viele Schübe jedes Modell erklärt, und welche Versand-Hits
nach dem Auftrags-Modell sicher oder möglicherweise verschickt wurden. Liest nur die Bot-Datenbank.

Aufruf auf dem VPS:
    docker exec -i gtcha-discord-bot python - < tools/ship_model_check.py          # alle Banner, Übersicht
    docker exec -i gtcha-discord-bot python - 24152 < tools/ship_model_check.py    # ein Banner, jeder Schub
"""

import json
import os
import sqlite3
import sys

sys.path.insert(0, "/app")
from utils.card_pool import (  # noqa: E402
    ORDER_MAX_NORMALS, TAX_FACTOR, VALUE_TOLERANCE, _batch_options, _normal_sums, _value_classes, fmt_coins,
    medal_units, order_options, tracked_units,
)

DB = os.getenv("BOT_DB", "/app/data/gtcha_bot.db")
only = int(sys.argv[1]) if len(sys.argv) > 1 else None
db = sqlite3.connect(DB)
db.row_factory = sqlite3.Row
query = ("SELECT pack_id, title, category, price_coins, card_pool, ship_batches FROM banners "
         "WHERE card_pool IS NOT NULL AND ship_batches IS NOT NULL")
rows = db.execute(query + (" AND pack_id = ?" if only else " AND is_active IN (1, 2)"),
                  (only,) if only else ()).fetchall()

totals = {"batches": 0, "old": 0, "orders": 0}
for row in rows:
    pool = json.loads(row["card_pool"])
    batches = json.loads(row["ship_batches"] or "[]")
    hits = [u for u in tracked_units(pool) if u.get("shipping_only")]
    if not hits or not batches:
        continue
    tier = {u["key"]: u["tier"] for u in medal_units(pool)}
    name = {u["key"]: f"{tier.get(u['key'], '?')} {u['name'][:40]}" for u in hits}
    classes = _value_classes(hits, VALUE_TOLERANCE)
    biggest = max(round(net * TAX_FACTOR) for _, net in batches) + 1000
    sums = _normal_sums(pool, ORDER_MAX_NORMALS, biggest)
    old_ok = orders_ok = 0
    lines = []
    for i, (count, net) in enumerate(batches, 1):
        value = round(net * TAX_FACTOR)
        old = bool(count > 0 and value > 0 and _batch_options(pool, classes, count, value, VALUE_TOLERANCE))
        opts = order_options(pool, count, value, sums=sums) if value > 0 else []
        old_ok += old
        orders_ok += bool(opts)
        if opts:
            certain = set.intersection(*(set(o) for o in opts))
            possible = set().union(*opts) - certain
            desc = ("sicher " + ", ".join(name[k] for k in sorted(certain)) if certain else "")
            if possible:
                desc += (" · " if desc else "") + "vielleicht " + ", ".join(name[k] for k in sorted(possible))
            if frozenset() in opts:
                desc += (" · " if desc else "") + "oder nur normale Karten"
        else:
            desc = "nicht erklärbar"
        lines.append(f"  Schub {i}: {count} · {fmt_coins(value)} Coins → heute {'✅' if old else '–'} · "
                     f"Aufträge {'✅' if opts else '–'}  {desc}")
    totals["batches"] += len(batches)
    totals["old"] += old_ok
    totals["orders"] += orders_ok
    print(f"{row['pack_id']} {row['category']} · {fmt_coins(row['price_coins'] or 0)} Coins: {len(batches)} Schübe · "
          f"heute erklärt {old_ok} · als Aufträge erklärt {orders_ok}")
    if only:
        print("\n".join(lines))

print(f"\nGesamt: {totals['batches']} Schübe · heutiges Modell erklärt {totals['old']} · "
      f"Auftrags-Modell erklärt {totals['orders']}")
