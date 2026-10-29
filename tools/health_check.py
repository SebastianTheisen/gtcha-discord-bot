"""Arbeitet der Bot noch sauber? Prüft die Bot-Datenbank in einem Durchlauf und markiert Auffälligkeiten (nur lesend).

Aufruf auf dem VPS:
    docker exec -i gtcha-discord-bot python - < tools/health_check.py
"""

import json
import os
import sqlite3
import time
from datetime import datetime, timedelta

DATA = os.path.dirname(os.getenv("BOT_DB", "/app/data/gtcha_bot.db"))
db = sqlite3.connect(f"file:{os.path.join(DATA, 'gtcha_bot.db')}?mode=ro", uri=True)
problems = []


def ago(seconds):
    if seconds is None:
        return "nie"
    return f"{seconds:.0f} s" if seconds < 120 else f"{seconds / 60:.0f} Min" if seconds < 7200 else f"{seconds / 3600:.1f} Std"


def age_iso(iso):
    try:
        return (datetime.now() - datetime.fromisoformat(iso)).total_seconds()
    except (TypeError, ValueError):
        return None


def check(ok, text, hint=""):
    print(("  ✅ " if ok else "  ⚠️ ") + text)
    if not ok:
        problems.append(text + (f" – {hint}" if hint else ""))


def one(sql, *args):
    return db.execute(sql, args).fetchone()


print("== Bot läuft?")
try:
    hb = time.time() - os.stat(os.path.join(DATA, "heartbeat")).st_mtime
except OSError:
    hb = None
check(hb is not None and hb < 600, f"letzter erfolgreicher Scrape vor {ago(hb)}", "Bot hängt oder Scrape scheitert")
meta = dict(db.execute("SELECT key, value FROM bot_meta").fetchall())
try:
    ws = json.loads(meta.get("worker_status") or "null") or {}
    ws_age = time.time() - ws.get("at", 0) if ws else None
except ValueError:
    ws, ws_age = {}, None
check(ws_age is not None and ws_age < 120, f"Bot meldet seinen Stand (vor {ago(ws_age)})", "Bot-Prozess läuft nicht")
if ws:
    cpu = ws.get("cpu") or {}
    running = cpu.get("running_all") or []
    long_jobs = [j for j in running if time.time() - j.get("since", time.time()) > 600]
    check(not long_jobs, f"Rechnungen: {len(running)} laufen, {cpu.get('waiting', 0)} warten"
          + (f" – lange: {', '.join(j['name'] + ' ' + j['label'] for j in long_jobs)}" if long_jobs else ""),
          "Rechnung hängt über 10 Min")
    hits = ws.get("hits") or {}
    check((hits.get("waiting") or 0) < 50, f"Hit-Erkennung: {hits.get('waiting', 0)} Banner warten")

print("\n== Daten von der Seite kommen an?")
last_move = one("SELECT max(changed_at) FROM pack_history")[0]
moves_hour = one("SELECT count(*) FROM pack_history WHERE changed_at >= ?",
                 (datetime.now() - timedelta(hours=1)).isoformat())[0]
check(age_iso(last_move) is not None and age_iso(last_move) < 3600,
      f"letzte Pack-Bewegung vor {ago(age_iso(last_move))} · {moves_hour} in der letzten Stunde",
      "pack/list liefert keine neuen Zahlen (Tor? Seite?)")
last_api = one("SELECT max(changed_at) FROM api_log")[0]
check(age_iso(last_api) is not None and age_iso(last_api) < 3600, f"Rohdaten der Seite zuletzt vor {ago(age_iso(last_api))}")
last_ship = one("SELECT max(changed_at) FROM shipment_history")[0]
last_conv = one("SELECT max(changed_at) FROM convert_history")[0]
print(f"  ·  letzter Versand-Schub vor {ago(age_iso(last_ship))}, letzte Umwandlung vor {ago(age_iso(last_conv))}")

print("\n== Banner vollständig?")
rows = db.execute("SELECT pack_id, category, card_pool, pool_updated_at, image_url, starts_at, current_packs, "
                  "total_packs, site_name FROM banners WHERE is_active = 1").fetchall()
print(f"  ·  {len(rows)} aktive Banner")
no_pool, old_pool, hidden, no_img, no_start, zero = [], [], [], [], [], []
for pid, cat, pool, pool_at, img, start, packs, total, name in rows:
    try:
        p = json.loads(pool) if pool else None
    except ValueError:
        p = None
    if not p or p.get("version") != 2 or "cards" not in p:
        no_pool.append(pid)
    elif not p.get("total_count"):
        hidden.append(pid)   # Kartenliste von GTCHA versteckt
    elif (age_iso(pool_at) or 0) > 24 * 3600:
        old_pool.append(pid)
    if not img and not (p and p.get("cards")):
        no_img.append(pid)
    if not start:
        no_start.append(pid)
    if not packs:
        zero.append(pid)
check(not no_pool, f"Kartenliste fehlt: {len(no_pool)}" + (f" ({', '.join(map(str, no_pool[:15]))})" if no_pool else ""),
      "Kartenlisten werden nicht nachgeladen")
print(f"  ·  Kartenliste von GTCHA versteckt: {len(hidden)}" + (f" ({', '.join(map(str, hidden[:15]))})" if hidden else ""))
check(len(old_pool) <= 5, f"Kartenliste älter als 24 Std: {len(old_pool)}"
      + (f" ({', '.join(map(str, old_pool[:15]))})" if old_pool else ""), "Kartenlisten werden nicht aufgefrischt")
check(not no_img, f"ohne Bild und ohne Kartenbilder: {len(no_img)}" + (f" ({', '.join(map(str, no_img))})" if no_img else ""))
check(not no_start, f"ohne Verkaufsstart: {len(no_start)}" + (f" ({', '.join(map(str, no_start[:15]))})" if no_start else ""))
print(f"  ·  laufend mit 0 Packs: {len(zero)}" + (f" ({', '.join(map(str, zero))})" if zero else ""))

print("\n== Hit-Erkennung")
tracked = db.execute("SELECT count(*) FROM banners WHERE is_active = 1 AND card_pool LIKE '%\"hits\": [{%' "
                     "AND ship_batches IS NULL AND ship_count IS NULL").fetchone()[0]
check(tracked <= 3, f"Banner mit Versand-Hits ohne Auswertungsstand: {tracked}", "Hit-Erkennung kommt nicht hinterher")
ticks = one("SELECT count(*), max(created_at) FROM auto_ticks")
print(f"  ·  automatisch erkannte Hits (14 Tage): {ticks[0]}, zuletzt vor {ago(age_iso(ticks[1]))}")

print("\n== Discord")
try:
    app = sqlite3.connect(f"file:{os.path.join(DATA, 'webapp.db')}?mode=ro", uri=True)
    s = dict(app.execute("SELECT key, value FROM app_settings").fetchall())
    stop = int(float(s.get("discord_stop_at") or 0))
    state = "normal" if not stop else ("Meldungen bis " + datetime.fromtimestamp(stop).strftime("%d.%m. %H:%M")
                                      if time.time() < stop else "aus (Pause)")
    print(f"  ·  Discord: {state}")
except Exception as e:
    print(f"  ·  Discord-Einstellung nicht lesbar: {e}")

print("\n== Ergebnis")
if problems:
    for p in problems:
        print(f"  ⚠️ {p}")
else:
    print("  ✅ Alles unauffällig")
