"""Prüft die Coin-Auswertung: alle laufenden Banner nur aus Coins bzw. aus Coins + Versand-Hits.

Zeigt je Banner: gezogene Packs, umgewandelte Coins, verschickte Karten, die Spanne umgewandelter Coin-Karten,
was die Rechnung jetzt ergibt (sicher / ❓) und was der Bot gespeichert hat. Ändert nichts.

Aufruf auf dem VPS:
    docker exec -i gtcha-discord-bot python - < tools/coin_check.py
"""

import json
import sqlite3
import sys
from datetime import datetime, timedelta

sys.path.insert(0, "/app")
from utils.card_pool import (  # noqa: E402
    fmt_coins, is_coin_mixed_pool, is_coin_pool, match_coin_conversions, tier_keys, tracked_units,
)
from utils.coin_history import coin_intervals, match_coin_history  # noqa: E402

PENDING_MINUTES = 10   # wie COIN_PENDING_MINUTES im Bot
db = sqlite3.connect("file:/app/data/gtcha_bot.db?mode=ro", uri=True)
db.row_factory = sqlite3.Row
rows = db.execute("SELECT pack_id, title, price_coins, total_packs, current_packs, converted, site_stats, card_pool, "
                  "pulled_cards, unsure_cards FROM banners WHERE is_active IN (1, 2) AND card_pool IS NOT NULL "
                  "ORDER BY pack_id").fetchall()
since = (datetime.now() - timedelta(minutes=PENDING_MINUTES)).isoformat()
found = 0
for r in rows:
    pool = json.loads(r["card_pool"])
    pure, mixed = is_coin_pool(pool), is_coin_mixed_pool(pool)
    if not (pure or mixed):
        continue
    found += 1
    tiers = {k: t for t, k in tier_keys(pool).items()}
    drawn = (r["total_packs"] or 0) - (r["current_packs"] or 0)
    shipped = (json.loads(r["site_stats"]).get("cards") or 0) if r["site_stats"] else 0
    pending = db.execute("SELECT COALESCE(SUM(old_count - new_count), 0) FROM pack_history WHERE banner_id = ? "
                         "AND changed_at >= ? AND new_count < old_count", (r["pack_id"], since)).fetchone()[0]
    ship_units = len(tracked_units(pool)) if mixed else 0
    n_max = max(0, drawn - shipped) if mixed else drawn
    n_min = max(0, drawn - ship_units - pending)
    res = match_coin_conversions(pool, r["converted"], n_min, n_max) if r["converted"] is not None else None
    stored = json.loads(r["pulled_cards"] or "[]")
    unsure = json.loads(r["unsure_cards"] or "[]")
    print(f"\n== {r['pack_id']} · {'nur Coins' if pure else 'Coins + Versand-Hits'} · {fmt_coins(r['price_coins'] or 0)} Coins/Pack")
    print(f"   gezogen {drawn} Packs · umgewandelt {fmt_coins(r['converted'] or 0)} Coins"
          + (f" · verschickt {shipped} Karten" if mixed else "") + f" · zuletzt gezogen ({PENDING_MINUTES} Min): {pending}")
    print(f"   umgewandelte Coin-Karten: {n_min} bis {n_max}")
    if res is None:
        print("   Rechnung: passt zu keiner Aufteilung (wird übergangen)")
    else:
        groups = [f"{g['pulled']} von {'/'.join(tiers.get(k, k) for k in g['keys'])}" for g in res["groups"]]
        print(f"   Rechnung jetzt: sicher {[tiers.get(k, k) for k in res['certain']] or '-'}"
              + (f" · ❓ {groups}" if groups else ""))
    conv = db.execute("SELECT changed_at, old_coins, new_coins FROM convert_history WHERE banner_id = ? ORDER BY id",
                      (r["pack_id"],)).fetchall()
    moves = db.execute("SELECT changed_at, old_count, new_count FROM pack_history WHERE banner_id = ? ORDER BY id",
                       (r["pack_id"],)).fetchall()
    iv = coin_intervals([tuple(x) for x in conv], [tuple(x) for x in moves], r["total_packs"] or 0, r["current_packs"] or 0)
    if iv:
        hist = match_coin_history(pool, iv, ship_units)
        last = ", ".join(f"{i['drawn']} Packs/{fmt_coins(i['coins'])}" for i in iv[-4:])
        print(f"   Schübe: {len(iv)} (letzte: {last})")
        if hist:
            hgroups = [f"{g['pulled']} von {'/'.join(tiers.get(k, k) for k in g['keys'])}" for g in hist["groups"]]
            print(f"   Schub für Schub: sicher {[tiers.get(k, k) for k in hist['certain']] or '-'}"
                  + (f" · ❓ {hgroups}" if hgroups else "") + f" · {hist['used']} genutzt, {hist['skipped']} übergangen")
    print(f"   gespeichert: gezogen {[tiers.get(k, k) for k in stored] or '-'}"
          + (f" · ❓ {len(unsure)} Gruppe(n)" if unsure else ""))
if not found:
    print("Gerade kein laufender Banner nur aus Coins oder aus Coins + Versand-Hits.")
