"""Rohdaten der Seite für einen Banner (Protokoll api_log) und Test der Versand-Rechnung.

Zeigt jede Änderung von Packs, verschickten Karten, Versandsumme und umgewandelten Coins - und prüft
für jeden Versand, mit welcher Annahme er zu Karten des Banners passt:
  ×1,1  = Seite zählt den Kartenwert ohne 10 % Steuer (so rechnet der Bot heute)
  ×1,08 = ohne 8 % Steuer
  ×1,0  = Seite zählt den vollen Kartenwert
Bei einzelnen Karten stehen die nächstliegenden Kartenwerte des Pools daneben.

Aufruf auf dem VPS (Standard: letzte 72 Stunden):
    docker exec -i gtcha-discord-bot python - 24152 < tools/api_log.py
    docker exec -i gtcha-discord-bot python - 24152 168 < tools/api_log.py
"""

import json
import os
import sqlite3
import sys
from datetime import datetime, timedelta, timezone

sys.path.insert(0, "/app")
from utils.banner_info import berlin_time  # noqa: E402
from utils.card_pool import VALUE_TOLERANCE, _batch_options, _value_classes, fmt_coins, tracked_units  # noqa: E402

DB = os.getenv("BOT_DB", "/app/data/gtcha_bot.db")
FACTORS = (("×1,1", 1.1), ("×1,08", 1.08), ("×1,0", 1.0))

if len(sys.argv) < 2:
    sys.exit("Aufruf: ... python - <Banner-ID> [Stunden] < tools/api_log.py")
pid = int(sys.argv[1])
hours = int(sys.argv[2]) if len(sys.argv) > 2 else 72
db = sqlite3.connect(DB)
db.row_factory = sqlite3.Row


def when(iso):
    try:
        return berlin_time(datetime.fromisoformat(iso).replace(tzinfo=timezone.utc).timestamp()).strftime("%d.%m. %H:%M:%S")
    except (TypeError, ValueError):
        return str(iso)


row = db.execute("SELECT title, category, price_coins, card_pool FROM banners WHERE pack_id = ?", (pid,)).fetchone()
pool = json.loads(row["card_pool"]) if row and row["card_pool"] else None
print(f"Banner {pid} · {(row['title'] if row else '') or ''} · {row['category'] if row else '?'} · "
      f"{fmt_coins((row['price_coins'] if row else 0) or 0)} Coins")
cards = sorted({(c["name"], c["value"]) for c in (pool or {}).get("cards") or []} |
               {(c["name"], c["value"]) for c in (pool or {}).get("hits") or []}, key=lambda c: -c[1])
ship_hits = [u for u in tracked_units(pool)] if pool else []
ship_hits = [u for u in ship_hits if u.get("shipping_only")]
classes = _value_classes(ship_hits, VALUE_TOLERANCE) if ship_hits else []
if cards:
    values = sorted({v for _, v in cards})
    print(f"Kartenwerte im Pool: {len(cards)} Karten, kleinster {fmt_coins(values[0])}, "
          f"größte {', '.join(fmt_coins(v) for v in values[-5:][::-1])}")


def explains(count, card_value):
    if not pool or count <= 0 or card_value <= 0:
        return False
    if classes:
        return bool(_batch_options(pool, classes, count, card_value, VALUE_TOLERANCE))
    return any(abs(v - card_value) <= v * VALUE_TOLERANCE for _, v in cards) if count == 1 else False


def nearest(card_value, n=3):
    return sorted(cards, key=lambda c: abs(c[1] - card_value))[:n]


since = (datetime.now() - timedelta(hours=hours)).isoformat()
rows = db.execute("SELECT * FROM api_log WHERE banner_id = ? AND changed_at >= ? ORDER BY id", (pid, since)).fetchall()
prev = db.execute("SELECT * FROM api_log WHERE banner_id = ? AND changed_at < ? ORDER BY id DESC LIMIT 1",
                  (pid, since)).fetchone()
print(f"\n== Rohdaten der Seite (letzte {hours} Std, {len(rows)} Änderungen)")
if not rows:
    print("noch keine - das Protokoll läuft ab dem Update; später erneut aufrufen")
for r in rows:
    d = (lambda k: (r[k] or 0) - (prev[k] or 0)) if prev else (lambda k: 0)
    parts = [f"{when(r['changed_at'])}  Packs {r['pack_count']}"
             + (f" ({d('pack_count'):+d})" if prev and d('pack_count') else "")]
    if prev and (d("sendcount") or d("sendprice")):
        parts.append(f"Versand {d('sendcount'):+d} Karte(n) / {d('sendprice'):+,} Coins gezählt".replace(",", "."))
    if prev and d("kangen"):
        parts.append(f"umgewandelt {d('kangen'):+,}".replace(",", "."))
    if prev and d("sendpeople"):
        parts.append(f"Spieler {d('sendpeople'):+d}")
    print(" · ".join(parts))
    if prev and d("sendcount") > 0 and d("sendprice") > 0:
        count, net = d("sendcount"), d("sendprice")
        tests = [f"{label}={fmt_coins(round(net * f))} {'✅' if explains(count, round(net * f)) else '–'}"
                 for label, f in FACTORS]
        print("      passt " + " · ".join(tests))
        if count == 1:
            for name, value in nearest(round(net * 1.1)):
                print(f"      nächste Karte: {name[:50]} {fmt_coins(value)} (gezählt ×1,1 = {(net * 1.1 / value - 1) * 100:+.1f} %, "
                      f"×1,0 = {(net / value - 1) * 100:+.1f} %)")
    elif prev and d("sendcount") == 0 and d("sendprice"):
        print("      ⚠️ Versandsumme geändert ohne neue Karte")
    if prev and d("kangen") > 0:
        near = nearest(d("kangen"), 1)
        if near:
            print(f"      Umwandlung: nächste Karte {near[0][0][:50]} {fmt_coins(near[0][1])}")
    prev = r
