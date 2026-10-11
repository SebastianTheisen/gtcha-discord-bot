"""Sucht in den Versand-Schüben eines Tages nach einem Rechenmuster: Wie hängt der gezählte Betrag der Seite
(total_sendprice) mit den Kartenwerten des Banners zusammen? Liest nur die Bot-Datenbank, ändert nichts.

Je Schub mit neuen Karten: Rohwerte der Seite (Karten, gezählte Coins, Umwandlung, Packs im selben Abruf),
bei Einzelkarten die nächstliegenden Kartenwerte mit dem Verhältnis gezählt/Kartenwert. Am Ende eine
Häufigkeit der Verhältnisse (1/1,1 = 0,909 heißt: Kartenwert ohne 10 % Steuer - so rechnet der Bot) und
weitere Muster (Vielfache des Packpreises, Abstand zum Kartenwert).

Aufruf auf dem VPS:
    docker exec -i gtcha-discord-bot python - < tools/ship_pattern.py              # heute
    docker exec -i gtcha-discord-bot python - 2026-10-11 < tools/ship_pattern.py   # bestimmter Tag
"""

import json
import os
import sqlite3
import sys
from collections import Counter
from datetime import datetime, timedelta

sys.path.insert(0, "/app")
from utils.banner_info import berlin_time  # noqa: E402
from utils.card_pool import fmt_coins  # noqa: E402

day = sys.argv[1] if len(sys.argv) > 1 else berlin_time(int(datetime.now().timestamp())).strftime("%Y-%m-%d")
db = sqlite3.connect(os.getenv("BOT_DB", "/app/data/gtcha_bot.db"))
db.row_factory = sqlite3.Row
# Tag in Berliner Zeit; changed_at ist Serverzeit (wie überall im Bot) - großzügig ±1 Tag holen und filtern
start = datetime.strptime(day, "%Y-%m-%d")
rows = db.execute("SELECT * FROM api_log WHERE changed_at >= ? AND changed_at < ? ORDER BY banner_id, id",
                  ((start - timedelta(days=1)).isoformat(), (start + timedelta(days=2)).isoformat())).fetchall()
banners = {r["pack_id"]: r for r in db.execute("SELECT pack_id, category, price_coins, card_pool FROM banners")}

prev, batches = {}, []
for r in rows:
    p = prev.get(r["banner_id"])
    prev[r["banner_id"]] = r
    if not p or r["sendcount"] is None or p["sendcount"] is None:
        continue
    t = datetime.fromisoformat(r["changed_at"])
    if berlin_time(int(t.timestamp())).strftime("%Y-%m-%d") != day:
        continue
    dc, dv = (r["sendcount"] or 0) - (p["sendcount"] or 0), (r["sendprice"] or 0) - (p["sendprice"] or 0)
    if dc == 0 and dv == 0:
        continue
    batches.append({"pid": r["banner_id"], "t": t, "cards": dc, "coins": dv,
                    "kangen": (r["kangen"] or 0) - (p["kangen"] or 0), "packs": (p["pack_count"] or 0) - (r["pack_count"] or 0)})

print(f"Versand-Änderungen am {day}: {len(batches)}\n")
ratios, diffs, per_pack = Counter(), [], Counter()
for b in batches:
    row = banners.get(b["pid"])
    pool = json.loads(row["card_pool"]) if row and row["card_pool"] else {}
    values = sorted({int(c.get("value") or 0) for c in pool.get("cards") or [] if c.get("value")})
    price = (row["price_coins"] if row else 0) or 0
    when = berlin_time(int(b["t"].timestamp())).strftime("%H:%M:%S")
    sign = lambda n: ("+" if n > 0 else "-") + fmt_coins(abs(n))
    print(f"{when}  {b['pid']} ({row['category'] if row else '?'}, Pack {fmt_coins(price)})  "
          f"{b['cards']:+d} Karten  {sign(b['coins'])} gezählt"
          + (f"  · Umwandlung {sign(b['kangen'])}" if b["kangen"] else "")
          + (f"  · {b['packs']} Packs weg" if b["packs"] else ""))
    if price and b["coins"]:
        per_pack[round(b["coins"] / price, 2)] += 1
    if b["cards"] == 1 and b["coins"] > 0 and values:
        close = sorted(values, key=lambda v: abs(b["coins"] / v - 1 / 1.1))[:3]
        for v in close:
            print(f"     Karte {fmt_coins(v):>9}: gezählt/Wert = {b['coins'] / v:.4f}  ·  Wert - gezählt = {fmt_coins(v - b['coins'])}")
        best = close[0]
        ratios[round(b["coins"] / best, 3)] += 1
        diffs.append(best - b["coins"])
    elif b["cards"] > 1 and b["coins"] > 0:
        print(f"     Ø je Karte {fmt_coins(b['coins'] // b['cards'])} gezählt = {fmt_coins(round(b['coins'] * 1.1 / b['cards']))} Kartenwert")

if ratios:
    print("\nVerhältnis gezählt / nächster Kartenwert (Einzelkarten; 0.909 = ohne 10 % Steuer):")
    for r, n in ratios.most_common(10):
        print(f"  {r:.3f}  {'█' * n} {n}")
if diffs:
    print("\nAbstand Kartenwert - gezählt (häufigste):", ", ".join(f"{fmt_coins(d)} ({n}×)" for d, n in Counter(diffs).most_common(6)))
if per_pack:
    print("Gezählt ÷ Packpreis (häufigste):", ", ".join(f"{k} ({n}×)" for k, n in per_pack.most_common(6)))
