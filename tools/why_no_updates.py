"""Warum zeigt ein Banner im Verlauf keine Umwandlung/Versand (oder keine Pack-Updates)? Liest nur die Bot-Datenbank.

Aufruf auf dem VPS:
    docker exec -i gtcha-discord-bot python - 24191 < tools/why_no_updates.py
"""

import os
import sqlite3
import sys
from datetime import datetime

sys.path.insert(0, "/app")
from utils.banner_info import berlin_time  # noqa: E402
from utils.card_pool import fmt_coins  # noqa: E402

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
    sys.exit(f"Banner {pid} ist nicht in der Datenbank.")
print(f"=== Banner {pid} ({b['category']!r}) · aktiv: {b['is_active']} · Packs {b['current_packs']}/{b['total_packs']}"
      f" · converted-Spalte: {b['converted']} ===")

# Rohdaten der Seite: enthält die pack/list diesen Banner überhaupt, und ändern sich Versand/Umwandlung?
al = db.execute("SELECT changed_at, pack_count, sendcount, sendprice, kangen, sendpeople FROM api_log "
                "WHERE banner_id = ? ORDER BY id DESC LIMIT 12", (pid,)).fetchall()
print(f"\n--- Rohdaten der Seite (api_log): {len(al)} Einträge, neueste zuerst ---")
if not al:
    print("  ⚠️ KEINE Rohdaten! Dieser Banner steht nicht in der pack/list, die der Bot über Tor sieht")
    print("     → dann kommen weder Pack-Updates noch Umwandlung/Versand an.")
for r in al:
    print(f"  {when(r['changed_at'])}  packs={r['pack_count']}  sendcount={r['sendcount']}  "
          f"sendprice={r['sendprice']}  kangen(umgewandelt)={r['kangen']}  spieler={r['sendpeople']}")

ph = db.execute("SELECT count(*) n, max(changed_at) m FROM pack_history WHERE banner_id = ?", (pid,)).fetchone()
cv = db.execute("SELECT changed_at, old_coins, new_coins FROM convert_history WHERE banner_id = ? ORDER BY id DESC LIMIT 6", (pid,)).fetchall()
sh = db.execute("SELECT changed_at, old_cards, new_cards, old_coins, new_coins FROM shipment_history WHERE banner_id = ? ORDER BY id DESC LIMIT 6", (pid,)).fetchall()
print(f"\n--- Pack-Verlauf: {ph['n']} Einträge (letzter {when(ph['m']) if ph['m'] else '–'}) ---")
print(f"--- Umwandlung (convert_history): {len(cv)} ---")
for r in cv:
    print(f"  {when(r['changed_at'])}  {r['old_coins']} -> {r['new_coins']}  (+{fmt_coins((r['new_coins'] or 0) - (r['old_coins'] or 0))})")
print(f"--- Versand (shipment_history): {len(sh)} ---")
for r in sh:
    print(f"  {when(r['changed_at'])}  Karten {r['old_cards']}->{r['new_cards']}  Coins {r['old_coins']}->{r['new_coins']}")

print("\n=== Einschätzung ===")
kangen_vals = [r['kangen'] for r in al if r['kangen'] is not None]
sp_vals = [r['sendprice'] for r in al if r['sendprice'] is not None]
if not al:
    print("  ⚠️ Banner fehlt in der pack/list der Seite (über Tor) - deshalb keine Updates.")
else:
    if len(set(kangen_vals)) <= 1 and not cv:
        print(f"  ℹ️ Umwandlung laut Seite unverändert ({kangen_vals[:1]}) - es wurde dort (noch) nichts umgewandelt.")
    elif len(set(kangen_vals)) > 1 and not cv:
        print("  ⚠️ Umwandlung ändert sich laut Seite, wird aber NICHT im Verlauf gespeichert - Fehler, bitte melden.")
    if len(set(sp_vals)) <= 1 and not sh:
        print(f"  ℹ️ Versand laut Seite unverändert ({sp_vals[:1]}) - es wurde dort (noch) nichts verschickt.")
    elif len(set(sp_vals)) > 1 and not sh:
        print("  ⚠️ Versand ändert sich laut Seite, wird aber NICHT gespeichert - Fehler, bitte melden.")
