"""Monatsbericht zum Nachschärfen der Hit-Erkennung (nur lesen, ändert nichts).

Fasst zusammen, was in den gesammelten Daten steckt:
  1. Automatisch abgehakt: wie viel, wie viel davon vom Admin als falsch markiert (mit Liste)
  2. Versand-Schübe: mit welcher Stufe sie erklärt werden (Hits/wertvolle Karten, Aufträge, alle Karten, gar nicht)
     und wie viele Hits bei Packpreis-Faktor 2 / 3 / 4 sicher erkannt würden
  3. Medaillen-Fristen: wie oft sie greifen und wie oft sie nicht zu den Schüben passen
  4. Lernen: Fälle, Beobachtungen, Zeit vom Zug bis zum Versand
  5. Treffsicherheit der Ø Rückgabe (Ergebnis 24 Std. später)
  6. Rohdaten-Protokoll (api_log)

Aufruf auf dem VPS (Ausgabe auf den Bildschirm, ausführliche Fassung zusätzlich in data/report-<Datum>.txt):
    docker exec -i gtcha-discord-bot python - < tools/monthly_report.py
"""

import json
import os
import sqlite3
import sys
from collections import Counter
from datetime import datetime, timedelta, timezone

sys.path.insert(0, "/app")
from utils import card_pool  # noqa: E402
from utils.card_pool import (  # noqa: E402
    TAX_FACTOR, VALUE_TOLERANCE, _batch_options, _order_class_options, _valuable_pool, _value_classes,
    batch_deadlines, match_shipment_history, tier_keys, tracked_units,
)

DATA = os.getenv("BOT_DATA", "/app/data")
DB = os.getenv("BOT_DB", os.path.join(DATA, "gtcha_bot.db"))
APP_DB = os.path.join(DATA, "webapp.db")
DAYS = int(sys.argv[1]) if len(sys.argv) > 1 else 30

db = sqlite3.connect(f"file:{DB}?mode=ro", uri=True)
db.row_factory = sqlite3.Row
since = (datetime.now() - timedelta(days=DAYS)).isoformat()
short, full = [], []


def out(line="", detail=False):
    full.append(line)
    if not detail:
        short.append(line)


def ts(iso):
    try:
        return datetime.fromisoformat(iso).replace(tzinfo=timezone.utc).timestamp()
    except (TypeError, ValueError):
        return None


def table_exists(name):
    return bool(db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (name,)).fetchone())


out(f"GTCHA Monatsbericht · {datetime.now():%d.%m.%Y %H:%M} · letzte {DAYS} Tage")

# 1. Automatisch abgehakt
out("\n== 1. Automatisch abgehakt")
if table_exists("auto_ticks"):
    ticks = db.execute("SELECT t.*, r.card_key IS NOT NULL AS rejected FROM auto_ticks t LEFT JOIN pull_rejects r "
                       "ON r.pack_id = t.pack_id AND r.card_key = t.card_key WHERE t.created_at >= ?",
                       (since,)).fetchall()
    unique = {(t["pack_id"], t["card_key"]): t for t in ticks}
    live = [t for t in unique.values() if not t["rebuild"]]
    rejected = [t for t in unique.values() if t["rejected"]]
    out(f"{len(unique)} Hits abgehakt ({len(live)} bei neuem Versand, {len(unique) - len(live)} bei Neuberechnung) · "
        f"als falsch markiert: {len(rejected)}")
    for t in rejected:
        out(f"  ❌ {t['pack_id']} {t['tier']} {t['name']} ({t['value']})")
else:
    out("noch keine Daten (Bot-Version zu alt)")

# 2./3. Versand-Schübe und Medaillen-Fristen
out("\n== 2. Versand-Schübe: womit sie erklärt werden")
rows = db.execute("SELECT pack_id, is_active, category, price_coins, card_pool, ship_batches FROM banners "
                  "WHERE card_pool IS NOT NULL AND ship_batches IS NOT NULL").fetchall()
tiers = Counter()
per_factor = {}
deadline_stats = Counter()
detail_lines = []
for row in rows:
    pool = json.loads(row["card_pool"])
    batches = json.loads(row["ship_batches"] or "[]")
    hits = [u for u in tracked_units(pool) if u.get("shipping_only")]
    if not hits or not batches:
        continue
    price = row["price_coins"] or None
    classes = _value_classes(hits, VALUE_TOLERANCE)
    for b in batches:
        count, net = b[0], b[1]
        value = round(net * TAX_FACTOR)
        if count <= 0 or value <= 0:
            tiers["ohne Karte"] += 1
            continue
        valuable = _valuable_pool(pool, price)
        kind = "gar nicht"
        for name, p in (("wertvolle Karten", valuable), ("alle Karten", pool)):
            if p is None:
                continue
            if _batch_options(p, classes, count, value, VALUE_TOLERANCE):
                kind = f"Karten · {name}"
                break
            if value <= card_pool.ORDER_MAX_VALUE and _order_class_options(p, classes, count, value):
                kind = f"Aufträge · {name}"
                break
        tiers[kind] += 1
    # Medaillen mit Zeit (ohne Admin-Haken) für die Fristen
    keys = tier_keys(pool)
    if row["is_active"] == 2 or row["category"] == "Store":
        threads = [-row["pack_id"]]
    else:
        threads = [r[0] for r in db.execute("SELECT thread_id FROM discord_threads WHERE banner_id = ?",
                                            (row["pack_id"],))]
    medal_t = {}
    for th in threads:
        for m in db.execute("SELECT tier, created_at, source FROM medals WHERE thread_id = ?", (th,)):
            if m["tier"] in keys and (m["source"] or "") != "admin" and ts(m["created_at"]):
                medal_t[keys[m["tier"]]] = ts(m["created_at"])
    deadlines = batch_deadlines(batches, medal_t)
    for factor in (2, 3, 4):
        card_pool.SHIP_NORMAL_MIN_FACTOR = factor
        res = match_shipment_history(pool, batches, VALUE_TOLERANCE, deadlines, price)
        per_factor[factor] = per_factor.get(factor, 0) + len(res["certain"])
        if factor == 3:
            deadline_stats["Fristen gesamt"] += len(deadlines)
            deadline_stats["Fristen ignoriert"] += len(res["ignored_deadlines"])
            deadline_stats["Medaille vor Wert"] += len(res.get("value_mismatch") or [])
            detail_lines.append(f"  {row['pack_id']}: sicher {len(res['certain'])}, ❓ {sum(g['pulled'] for g in res['groups'])}, "
                                f"Schübe genutzt {res['used_batches']}/{len(batches)}"
                                + (f", Frist ignoriert {res['ignored_deadlines']}" if res["ignored_deadlines"] else "")
                                + (f", Medaille vor Wert {res['value_mismatch']}" if res.get("value_mismatch") else ""))
    card_pool.SHIP_NORMAL_MIN_FACTOR = 3
total = sum(tiers.values())
for kind, n in tiers.most_common():
    out(f"  {kind}: {n} ({n / total * 100:.0f} %)" if total else f"  {kind}: {n}")
out("Sicher erkannte Hits je Packpreis-Faktor (aktuell 3): "
    + " · ".join(f"{f}×: {n}" for f, n in sorted(per_factor.items())))
out("\n== 3. Medaillen-Fristen")
out(f"{deadline_stats['Fristen gesamt']} Fristen · Medaille vor Wert (Seite zählt anderen Wert): "
    f"{deadline_stats['Medaille vor Wert']} · nicht anwendbar: {deadline_stats['Fristen ignoriert']}")
out("Je Banner:", detail=True)
for line in detail_lines:
    out(line, detail=True)

# 4. Lernen
out("\n== 4. Lernen (Zeit vom Zug bis zum Versand)")
meta = db.execute("SELECT value FROM bot_meta WHERE key = 'ship_delay_counts'").fetchone()
learn = json.loads(meta[0]) if meta and meta[0] else {}
out(f"Fälle {learn.get('cases', 0)} · Beobachtungen {learn.get('n', 0)} · Klassen (0-1h, 1-6h, 6-24h, 1-3T, 3-7T, "
    f"7-14T, 14T+): {learn.get('counts', [])}")

# 5. Treffsicherheit
out("\n== 5. Treffsicherheit der Ø Rückgabe (Ergebnis 24 Std. später)")
if os.path.exists(APP_DB):
    try:
        from webapp.accuracy import evaluate
        app = sqlite3.connect(f"file:{APP_DB}?mode=ro", uri=True)
        snaps = app.execute("SELECT banner_id, t, ev_pct, remaining, out_value, price, title FROM ev_history "
                            "WHERE out_value IS NOT NULL ORDER BY banner_id, t").fetchall()
        by = {}
        for s in snaps:
            by.setdefault(s[0], []).append(s)
        acc = evaluate(by)
        out(f"{acc['count']} Banner · Ø Abweichung {acc['mean_abs']} %-Punkte · Richtung {acc['bias']} "
            f"(+ = Vorhersage zu hoch)")
        for i in acc["items"]:
            out(f"  {i['id']}: vorhergesagt {i['predicted']} % · tatsächlich {i['realized']} % · {i['sold']} Packs",
                detail=True)
    except Exception as e:   # z. B. App-Datenbank gerade gesperrt
        out(f"noch keine Daten oder nicht lesbar ({type(e).__name__})")
else:
    out("App-Datenbank nicht gefunden")

# 6. Rohdaten
out("\n== 6. Rohdaten-Protokoll (api_log)")
if table_exists("api_log"):
    n, banners = db.execute("SELECT count(*), count(DISTINCT banner_id) FROM api_log WHERE changed_at >= ?",
                            (since,)).fetchone()
    out(f"{n} Änderungen bei {banners} Bannern")

out("\nAusführliche Fassung: data/report-<Datum>.txt")
print("\n".join(short))
path = os.path.join(DATA, f"report-{datetime.now():%Y-%m-%d}.txt")
try:
    with open(path, "w", encoding="utf-8") as f:
        f.write("\n".join(full) + "\n")
    print(f"gespeichert: {path.replace(DATA, 'data')}")
except OSError as e:
    print(f"Datei nicht gespeichert: {e}")
