"""Warum wurde ein Platz (z. B. T2) eines Banners nicht automatisch als gezogen erkannt?

Liest nur die Bot-Datenbank (kein Internet) und zeigt: welche Karte der Platz ist, ob sie meldbar ist,
Medaille, Erkennungsstand, Versand- und Umwandlungs-Verlauf, Auswertung der Versand-Schübe und
zeitversetzte Discord-Posts - am Ende eine Diagnose.

Aufruf auf dem VPS:
    docker exec -i gtcha-discord-bot python - 24152 T2 < tools/why_not_pulled.py
"""

import json
import os
import sqlite3
import sys
from datetime import datetime, timedelta, timezone

sys.path.insert(0, "/app")
from utils.banner_info import berlin_time  # noqa: E402
from utils.card_pool import (  # noqa: E402
    card_value, claimable_units, fmt_coins, match_shipment_history, medal_units, resolve_pulled, tier_keys,
    tracked_units,
)

DB = os.getenv("BOT_DB", "/app/data/gtcha_bot.db")
DAYS = 3

if len(sys.argv) < 2:
    sys.exit("Aufruf: ... python - <Banner-ID> [T2] < tools/why_not_pulled.py")
pid = int(sys.argv[1])
tier = (sys.argv[2] if len(sys.argv) > 2 else "T2").upper()
db = sqlite3.connect(DB)
db.row_factory = sqlite3.Row


def when(iso):
    try:
        ts = datetime.fromisoformat(str(iso)).replace(tzinfo=timezone.utc).timestamp()
        return berlin_time(ts).strftime("%d.%m. %H:%M")
    except (TypeError, ValueError):
        return str(iso)


def section(title):
    print(f"\n== {title}")


since = (datetime.now() - timedelta(days=DAYS)).isoformat()
row = db.execute("SELECT * FROM banners WHERE pack_id = ?", (pid,)).fetchone()
if not row:
    sys.exit(f"Banner {pid} ist nicht (mehr) in der Datenbank.")
kind = {1: "normal", 2: "Store (nur App)", 0: "beendet"}.get(row["is_active"], str(row["is_active"]))
price = row["price_coins"] or 0
print(f"Banner {pid} · {row['title'] or ''} · {row['category']} · {fmt_coins(price)} Coins · "
      f"{row['current_packs']}/{row['total_packs']} Packs · {kind}")

pool = json.loads(row["card_pool"]) if row["card_pool"] else None
if not pool or not pool.get("total_count"):
    sys.exit("Kein Kartenpool gespeichert - ohne Kartenliste gibt es keine Erkennung.")
keys = tier_keys(pool)
unit = next((u for u in medal_units(pool) if u["tier"] == tier), None)
if not unit:
    sys.exit(f"{tier} gibt es bei diesem Banner nicht (Plätze: T1–T{len(keys)}).")
key = unit["key"]
has_ship_hits = bool(pool.get("hits"))
tracked = {u["key"] for u in tracked_units(pool)}
claimable = {u["key"] for u in claimable_units(pool, price or None)}

section(f"{tier}: welche Karte")
print(f"{unit['name']} · {fmt_coins(unit['value'])} Coins · Schlüssel {key} · "
      f"{'Versand nur ✈' if unit.get('shipping_only') else 'normale Karte (umwandelbar)'}")
print(f"Banner hat Versand-Hits: {'ja' if has_ship_hits else 'nein'} · "
      f"{tier} wird beobachtet: {'ja' if key in tracked else 'nein'} · "
      f"meldbar (ab Packpreis): {'ja' if key in claimable else 'nein'}")

thread = db.execute("SELECT thread_id, is_expired FROM discord_threads WHERE banner_id = ?", (pid,)).fetchone()
medal_thread = -pid if row["is_active"] == 2 else (thread["thread_id"] if thread else None)
medals = {}
if medal_thread is not None:
    for m in db.execute("SELECT * FROM medals WHERE thread_id = ?", (medal_thread,)):
        medals[m["tier"]] = dict(m)
section("Medaillen")
print(", ".join(f"{t} ({m.get('source') or 'discord'}, {when(m['created_at'])})" for t, m in sorted(medals.items()))
      or "keine")

state = db.execute("SELECT decided_value, ship_count, ship_value, pulled_cards, unsure_cards, ship_batches "
                   "FROM banners WHERE pack_id = ?", (pid,)).fetchone()
pulled = json.loads(state["pulled_cards"] or "[]")
unsure = json.loads(state["unsure_cards"] or "[]")
batches = json.loads(state["ship_batches"]) if state["ship_batches"] else None
winners = {keys[t] for t in medals if t in keys}
_, sure, open_groups = resolve_pulled(pulled, unsure, winners)
section("Erkennungsstand")
print(f"sicher erkannt: {pulled or '–'}")
print(f"❓-Gruppen: {[(g['pulled'], g['keys']) for g in unsure] or '–'}")
print(f"zuletzt gesehen: Versand {state['ship_count']} Karten / {fmt_coins(card_value(state['ship_value'] or 0))} Coins "
      f"Kartenwert · umgewandelt+verschickt (Seite) {fmt_coins(state['decided_value'] or 0)}")

section(f"Versand (letzte {DAYS} Tage, Kartenwert ×1,1)")
ships = db.execute("SELECT * FROM shipment_history WHERE banner_id = ? AND changed_at >= ? ORDER BY id",
                   (pid, since)).fetchall()
for s in ships:
    cards = (s["new_cards"] or 0) - (s["old_cards"] or 0)
    coins = card_value((s["new_coins"] or 0) - (s["old_coins"] or 0))
    print(f"{when(s['changed_at'])}: +{cards} Karte(n) · {fmt_coins(coins)} Coins"
          + ("  ← passt genau zu " + tier if cards == 1 and abs(coins - unit["value"]) <= unit["value"] * 0.02 else ""))
print("keine" if not ships else "")
if batches is not None and has_ship_hits:
    joint = match_shipment_history(pool, batches, winners)
    print(f"Auswertung aller {len(batches)} Schübe: sicher {joint['certain'] or '–'} · "
          f"Gruppen {[(g['pulled'], g['keys']) for g in joint['groups']] or '–'} · "
          f"genutzt {joint['used_batches']} von {len(batches)}")

section(f"Umwandlungen (letzte {DAYS} Tage)")
convs = db.execute("SELECT * FROM convert_history WHERE banner_id = ? AND changed_at >= ? ORDER BY id",
                   (pid, since)).fetchall()
for c in convs:
    jump = (c["new_coins"] or 0) - (c["old_coins"] or 0)
    print(f"{when(c['changed_at'])}: +{fmt_coins(jump)} Coins" + (f"  ← groß genug für {tier}" if jump >= unit["value"] else ""))
print("keine" if not convs else "")

moves = db.execute("SELECT count(*), sum(old_count - new_count) FROM pack_history WHERE banner_id = ? AND changed_at >= ? "
                   "AND new_count < old_count", (pid, since)).fetchone()
section("Pack-Bewegungen")
print(f"{moves[0]} Änderungen, {moves[1] or 0} Packs verkauft in den letzten {DAYS} Tagen")

pending = []
try:
    pending = db.execute("SELECT send_at, kind FROM discord_outbox WHERE pack_id = ?", (pid,)).fetchall()
    public = db.execute("SELECT pulled_cards FROM discord_public WHERE pack_id = ?", (pid,)).fetchone()
except sqlite3.OperationalError:
    public = None

section("Diagnose")
if tier in medals:
    print(f"✅ {tier} hat bereits eine Medaille ({medals[tier].get('source') or 'discord'}).")
elif key in sure:
    print(f"✅ {tier} wurde sicher erkannt.")
    if public is not None and key not in json.loads(public[0] or "[]"):
        print(f"   In Discord noch nicht sichtbar: zeitversetzt, {len(pending)} Post(s) warten "
              f"(fällig {', '.join(datetime.fromtimestamp(p[0]).strftime('%H:%M') for p in pending) or '–'}).")
elif any(key in g["keys"] for g in open_groups):
    g = next(g for g in open_groups if key in g["keys"])
    print(f"❓ {tier} steckt in einer unsicheren Gruppe: {g['pulled']} von {g['keys']} gezogen - "
          f"wertgleiche Karten, der Bot weiß nicht welche. Eine Medaille löst das auf.")
elif key not in tracked:
    print(f"❌ {tier} wird gar nicht automatisch beobachtet: "
          + ("in Bannern mit Versand-Hits erkennt der Bot nur Versand-Hits („Versand nur ✈“). "
             f"{tier} ist eine normale Karte - wird sie umgewandelt oder verschickt, kann der Bot das nicht "
             "eindeutig zuordnen. Nur per Medaille." if has_ship_hits else
             "nur T1–T3 werden in Bannern ohne Versand-Hits beobachtet."))
elif has_ship_hits:
    if not ships:
        print(f"❌ Die Seite hat in den letzten {DAYS} Tagen keinen Versand gezählt - "
              f"{tier} wurde (noch) nicht zum Versand angefordert oder die Seite zählt es noch nicht.")
    else:
        print(f"❌ Es gab Versände, aber keiner ließ sich eindeutig {tier} zuordnen "
              "(Anzahl/Wert passen nur zusammen mit anderen Karten oder mehrdeutig). Siehe Auswertung oben.")
else:
    big = [c for c in convs if (c["new_coins"] or 0) - (c["old_coins"] or 0) >= unit["value"]]
    if not big:
        print(f"❌ Kein Sprung bei „umgewandelt“ groß genug für {tier} ({fmt_coins(unit['value'])} Coins) - "
              "die Karte wurde wohl noch nicht umgewandelt (Frist 24 Std) oder angefordert.")
    else:
        print(f"❌ Es gab einen passenden Sprung, aber er wurde nicht als {tier} erkannt "
              "(z. B. größere Karte vorrangig zugeordnet oder Erkennung lief da noch nicht).")
