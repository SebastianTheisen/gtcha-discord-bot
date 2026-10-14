"""Verlauf eines Banners aus dem neuesten Backup zurückholen (Pack-Bewegungen, Versand, Umwandlungen, Kartenwerte,
Rohdaten). Ohne --anwenden wird nur angezeigt, was fehlt; vorhandene Einträge werden nie doppelt eingefügt.

Aufruf auf dem VPS:
    docker exec -i gtcha-discord-bot python - 24260 < tools/restore_history.py
    docker exec -i gtcha-discord-bot python - 24260 --anwenden < tools/restore_history.py
"""

import glob
import os
import sqlite3
import sys

args = [a for a in sys.argv[1:] if a.isdigit()]
if not args:
    sys.exit("Banner-ID angeben, z. B.:  docker exec -i gtcha-discord-bot python - 24260 < tools/restore_history.py")
pid, apply = int(args[0]), "--anwenden" in sys.argv
db_path = os.getenv("BOT_DB", "/app/data/gtcha_bot.db")
backups = sorted(glob.glob(os.path.join(os.path.dirname(db_path), "backups", "gtcha_bot_*.db")))
if not backups:
    sys.exit("Kein Backup gefunden")
backup = backups[-1]
print(f"Backup: {os.path.basename(backup)}  ->  Banner {pid}{'' if apply else '  (nur anzeigen, --anwenden schreibt)'}")

TABLES = {
    "pack_history": ("old_count", "new_count", "changed_at"),
    "shipment_history": ("old_cards", "new_cards", "old_coins", "new_coins", "old_players", "new_players", "changed_at"),
    "convert_history": ("old_coins", "new_coins", "changed_at"),
    "card_value_history": ("card_id", "name", "old_value", "new_value", "changed_at"),
    "api_log": ("changed_at", "pack_count", "sendcount", "sendprice", "kangen", "sendpeople", "price"),
}
src = sqlite3.connect(f"file:{backup}?mode=ro", uri=True)
dst = sqlite3.connect(db_path, timeout=30)
total = 0
for table, cols in TABLES.items():
    sel = ", ".join(cols)
    have = set(dst.execute(f"SELECT {sel} FROM {table} WHERE banner_id = ?", (pid,)).fetchall())
    old = src.execute(f"SELECT {sel} FROM {table} WHERE banner_id = ? ORDER BY id", (pid,)).fetchall()
    missing = [r for r in old if r not in have]
    when = f" ({missing[0][cols.index('changed_at')][:16]} bis {missing[-1][cols.index('changed_at')][:16]})" if missing else ""
    print(f"  {table:<19} Backup {len(old):>5} · jetzt {len(have):>5} · fehlen {len(missing):>5}{when}")
    if apply and missing:
        dst.executemany(f"INSERT INTO {table} (banner_id, {sel}) VALUES (?, {', '.join('?' * len(cols))})",
                        [(pid, *r) for r in missing])
        total += len(missing)
if apply:
    # Erkennung mit vollständigem Verlauf neu aufbauen (erste Auswertung postet nichts)
    dst.execute("UPDATE banners SET decided_value = NULL, ship_count = NULL, ship_value = NULL, pulled_cards = NULL, "
                "unsure_cards = NULL, ship_batches = NULL WHERE pack_id = ?", (pid,))
    dst.commit()
    print(f"{total} Einträge zurückgeholt.")
