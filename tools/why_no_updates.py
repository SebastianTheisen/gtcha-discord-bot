"""Warum bekommt ein Banner keine Pack-Updates (im App-Verlauf)? Zeigt alles Relevante aus der Bot-Datenbank.

Aufruf auf dem VPS:
    docker exec -i gtcha-discord-bot python - 24191 < tools/why_no_updates.py
"""

import os
import sqlite3
import sys
from datetime import datetime

sys.path.insert(0, "/app")
from utils.banner_info import berlin_time  # noqa: E402

pid = int(sys.argv[1]) if len(sys.argv) > 1 and sys.argv[1].isdigit() else None
if not pid:
    sys.exit("Banner-ID angeben, z. B.:  docker exec -i gtcha-discord-bot python - 24191 < tools/why_no_updates.py")
db = sqlite3.connect(os.getenv("BOT_DB", "/app/data/gtcha_bot.db"))
db.row_factory = sqlite3.Row


def when(iso):
    try:
        return berlin_time(int(datetime.fromisoformat(iso).timestamp())).strftime("%d.%m. %H:%M:%S")
    except Exception:
        return str(iso)


b = db.execute("SELECT * FROM banners WHERE pack_id = ?", (pid,)).fetchone()
if not b:
    sys.exit(f"Banner {pid} ist nicht in der Datenbank (nie angelegt oder gelöscht).")
print(f"=== Banner {pid} ===")
print(f"  Kategorie: {b['category']!r} · aktiv: {b['is_active']} · Packs: {b['current_packs']}/{b['total_packs']}"
      f" · nicht-gefunden-Zähler: {b['not_found_count']}")
print(f"  angelegt: {when(b['created_at'])} · zuletzt aktualisiert: {when(b['updated_at'])}"
      f" · Pool: {'ja' if b['card_pool'] else 'nein'} (Stand {when(b['pool_updated_at']) if b['pool_updated_at'] else '–'})")
try:
    from config import CHANNEL_IDS
    print(f"  Discord-Forum für Kategorie {b['category']!r}: {CHANNEL_IDS.get(b['category']) or 'KEINS'}")
except Exception as e:
    print(f"  (Forum-Zuordnung nicht lesbar: {e})")

th = db.execute("SELECT * FROM discord_threads WHERE banner_id = ?", (pid,)).fetchall()
print(f"\n  Discord-Threads: {len(th)}")
for t in th:
    print(f"    thread {t['thread_id']} · Kanal {t['channel_id']} · abgelaufen: {t['is_expired']} · angelegt {when(t['created_at'])}")
try:
    pt = db.execute("SELECT * FROM premium_threads WHERE banner_id = ?", (pid,)).fetchall()
    for t in pt:
        print(f"    PREMIUM {t['thread_id']} · abgelaufen: {t['is_expired']}")
except sqlite3.OperationalError:
    pass

ph = db.execute("SELECT changed_at, old_count, new_count FROM pack_history WHERE banner_id = ? ORDER BY id DESC LIMIT 8", (pid,)).fetchall()
print(f"\n  Pack-Verlauf (neueste zuerst): {len(ph)} Einträge")
for r in ph:
    print(f"    {when(r['changed_at'])}  {r['old_count']} -> {r['new_count']}")
al = db.execute("SELECT changed_at, pack_count FROM api_log WHERE banner_id = ? ORDER BY id DESC LIMIT 8", (pid,)).fetchall()
print(f"\n  Rohdaten der Seite (api_log, neueste zuerst): {len(al)} Einträge")
for r in al:
    print(f"    {when(r['changed_at'])}  pack_count={r['pack_count']}")

print("\n=== Einschätzung ===")
if not b["is_active"]:
    print("  ⚠️ Banner ist NICHT aktiv (archiviert) - der Bot aktualisiert nur aktive Banner.")
elif not th:
    print("  ℹ️ Kein Discord-Thread. Pack-Zahlen werden ab sofort trotzdem gespeichert (nach dem Update);")
    print("     vorher waren sie evtl. eingefroren, weil der Discord-Post fehlte.")
elif th and all(t["is_expired"] for t in th):
    print("  ⚠️ Thread ist als abgelaufen markiert.")
if al and ph and al[0]["pack_count"] != ph[0]["new_count"]:
    print(f"  ⚠️ Seite meldet {al[0]['pack_count']} Packs, gespeichert ist {ph[0]['new_count']} - Updates kamen nicht an.")
