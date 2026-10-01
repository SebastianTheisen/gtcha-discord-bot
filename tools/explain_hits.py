"""Zeigt, warum der Bot Hits eines Banners als verschickt erkannt hat.

Liest Kartenpool und zuletzt gesehene Versand-Zähler aus der Datenbank (kein Internet nötig)
und listet jede Kombination aus Hits + normalen Karten, die genau die verschickte Anzahl
und den verschickten Coin-Wert ergibt.

Aufruf auf dem VPS:
    docker exec -i gtcha-discord-bot python - 24102 < tools/explain_hits.py
"""

import json
import sqlite3
import sys
from itertools import combinations

sys.path.insert(0, "/app")
from utils.card_pool import _normal_sums, tracked_units  # noqa: E402

DB = "/app/data/gtcha_bot.db"


def normal_example(pool, count, value):
    """Eine Beispiel-Zerlegung von `value` in genau `count` normale Karten (oder None)."""
    values = sorted((int(v) for v, n in pool["normal_values"].items() for _ in range(min(n, count))), reverse=True)
    best = {(0, 0): []}
    for v in values:
        for (c, s), cards in list(best.items()):
            key = (c + 1, s + v)
            if c < count and s + v <= value and key not in best:
                best[key] = cards + [v]
    return best.get((count, value))


def main():
    pid = int(sys.argv[1]) if len(sys.argv) > 1 else 24102
    con = sqlite3.connect(DB)
    row = con.execute("SELECT card_pool, ship_count, ship_value, pulled_cards, unsure_cards FROM banners "
                      "WHERE pack_id = ?", (pid,)).fetchone()
    if not row or not row[0]:
        print(f"Banner {pid}: kein Kartenpool in der Datenbank")
        return
    pool = json.loads(row[0])
    count, value = row[1], row[2]
    pulled = set(json.loads(row[3] or "[]"))
    units = tracked_units(pool)
    names = {u["key"]: u["name"] for u in units}

    print(f"Banner {pid}: zuletzt gesehen {count} verschickte Karten, {value:,} Coins".replace(",", "."))
    print(f"Als gezogen gespeichert: {[names.get(k, k) for k in pulled] or 'nichts'}")
    print(f"Unsichere Fälle: {json.loads(row[4] or '[]') or 'keine'}")
    print(f"\nVersand-Hits im Pool ({len(units)}):")
    for i, u in enumerate(units, 1):
        print(f"  {i:>2}. {u['name'][:45]:<45} {u['value']:>9,}".replace(",", "."))
    top_normal = sorted((int(v) for v in pool["normal_values"]), reverse=True)[:5]
    print(f"Höchste Werte normaler Karten: {top_normal}")

    if not count or not value:
        return
    if count > 10:
        print("\nMehr als 10 Karten verschickt - der Bot zerlegt das nicht.")
        return
    sums = _normal_sums(pool, count, value)
    print(f"\nAlle Kombinationen, die genau {count} Karten / {value:,} Coins ergeben:".replace(",", "."))
    found = 0
    for size in range(0, min(count, len(units)) + 1):
        for combo in combinations(units, size):
            rest = value - sum(u["value"] for u in combo)
            if rest >= 0 and (sums[count - size] >> rest) & 1:
                found += 1
                hits = " + ".join(f"{u['name'][:30]} ({u['value']:,})" for u in combo) or "keine Hits"
                example = normal_example(pool, count - size, rest)
                print(f"  {found}. {hits}".replace(",", "."))
                print(f"     + {count - size} normale Karten = {rest:,} Coins, z.B. {example}".replace(",", "."))
                if found >= 30:
                    print("  ... (weitere abgeschnitten)")
                    return
    if not found:
        print("  keine - der Bot erkennt dann nichts")
    elif found == 1:
        print("\nNur eine Kombination möglich -> diese Hits sind sicher verschickt.")
    else:
        print("\nMehrere Kombinationen -> sicher sind nur Hits, die in allen vorkommen.")


main()
