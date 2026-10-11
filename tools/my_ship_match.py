"""Legt die eigenen Versand-Daten (aus "Alles übertragen", Lesezeichen ab v19) neben die Versand-Zähler der Banner:
Welchen Wert zählt die Seite beim Versand einer bekannten Karte wirklich - Kartenwert der Liste, stock_sale_price
der Karte, mit oder ohne 10 % Steuer? Liest nur die Datenbanken, ändert nichts.

Aufruf auf dem VPS (Standard: Discord-IDs aus APP_ADMIN_IDS, letzte 60 Tage):
    docker exec -i gtcha-discord-bot python - < tools/my_ship_match.py
    docker exec -i gtcha-discord-bot python - 123456789012345678 90 < tools/my_ship_match.py
"""

import json
import os
import re
import sqlite3
import sys
from collections import Counter
from datetime import datetime, timedelta

sys.path.insert(0, "/app")
from utils.card_pool import fmt_coins  # noqa: E402

args = sys.argv[1:]
users = [a for a in args if len(a) > 10] or [i.strip() for i in os.getenv("APP_ADMIN_IDS", "").split(",") if i.strip()]
days = next((int(a) for a in args if a.isdigit() and len(a) <= 4), 60)
since = (datetime.now() - timedelta(days=days)).strftime("%Y-%m-%d")
bot = sqlite3.connect("/app/data/gtcha_bot.db")
app = sqlite3.connect("/app/data/webapp.db")

# Karten-ID -> Banner (aktive und beendete Banner mit Kartenliste); Bildnummer als Ersatz
by_id, by_img = {}, {}
for pid, raw in bot.execute("SELECT pack_id, card_pool FROM banners WHERE card_pool IS NOT NULL"):
    for c in (json.loads(raw).get("cards") or []):
        entry = (pid, int(c.get("value") or 0), c.get("name") or "")
        by_id.setdefault(str(c.get("id")), []).append(entry)
        m = re.search(r"/card/(\d+)", str(c.get("image") or ""))
        if m:
            by_img.setdefault(m.group(1), []).append(entry)


def batches(pid, day):
    """Versand-Zähler-Änderungen des Banners vom Tag der Karte bis 3 Tage danach."""
    start = datetime.strptime(day, "%Y-%m-%d")
    return bot.execute("SELECT changed_at, new_cards - old_cards, new_coins - old_coins FROM shipment_history "
                       "WHERE banner_id = ? AND changed_at >= ? AND changed_at < ? ORDER BY id",
                       (pid, start.isoformat(), (start + timedelta(days=4)).isoformat())).fetchall()


def close(a, b):
    return a and b and abs(a - b) <= max(2, b * 0.01)


stats = Counter()
for user in users:
    rows = app.execute("SELECT area, data FROM user_history WHERE discord_user_id = ? AND "
                       "(area LIKE 'shipped%' OR area LIKE 'pending%')", (user,)).fetchall()
    cards = [(area.split("@")[0], c) for area, data in rows for c in (json.loads(data).get("items") or [])]
    cards = [(a, c) for a, c in cards if (c.get("date") or "") >= since]
    print(f"Nutzer {user}: {len(cards)} Karten seit {since} (Versendet + Angefordert)\n")
    seen = set()
    for area, c in sorted(cards, key=lambda x: x[1].get("date") or "", reverse=True):
        key = (c.get("card_id"), c.get("date"), c.get("tracking"))
        if key in seen:
            continue
        seen.add(key)
        img = re.search(r"/card/(\d+)", str(c.get("image") or ""))
        found = by_id.get(str(c.get("card_id"))) or (by_img.get(img.group(1)) if img else None)
        price = c.get("price")
        line = f"{c.get('date')}  {('versendet' if area == 'shipped' else 'angefordert'):11s} {c.get('name', '')[:45]}"
        if not found:
            stats["kein Banner"] += 1
            print(f"·  {line}\n     keine Kartenliste mit dieser Karte" + (f" · Seite: {fmt_coins(price)}" if price else ""))
            continue
        # dieselbe Karte steckt oft in mehreren Bannern: den Banner nehmen, dessen Versand-Zähler am Tag passt
        best = None
        for pid, value, _ in found:
            hits = []
            for at, n, coins in batches(pid, c["date"]):
                if not n or n <= 0:
                    continue   # nur Schübe mit neuer Karte (Korrekturen ohne Karte zählen nicht)
                gross = round((coins or 0) * 1.1)
                tags = [t for t, ok in (("=Kartenwert", close(gross, value)),
                                        ("=Seitenwert×1,1", close(gross, round((price or 0) * 1.1))),
                                        ("=Seitenwert", close(coins, price)), ("ungezählt=Kartenwert", close(coins, value))) if ok]
                hits.append((at, n, coins, gross, tags))
            score = (any(h[4] for h in hits), bool(hits))
            if best is None or score > best[0]:
                best = (score, pid, value, hits)
        _, pid, value, hits = best
        match = next((h for h in hits if h[4]), None)
        stats["Kartenwert passt" if match and "=Kartenwert" in match[4] else
              "Seitenwert passt" if match else "kein Schub passt"] += 1
        print(f"{'✅' if match else '❓'} {line}\n     Banner {pid} · Kartenwert {fmt_coins(value)}"
              + ("" if hits else " · kein Versand-Schub mit neuer Karte in den 3 Tagen danach")
              + (f" · Seitenwert {fmt_coins(price)}" if price else "")
              + (f" · {len(found)} Banner mit dieser Karte" if len(found) > 1 else ""))
        for at, n, coins, gross, tags in hits[:4]:
            print(f"     {at[5:16].replace('T', ' ')}  {n:+d} Karten  {coins:+,} gezählt = {gross:,}".replace(",", ".")
                  + (f"  {' '.join(tags)}" if tags else ""))
print("\nERGEBNIS: " + " · ".join(f"{k}: {v}" for k, v in stats.most_common()))
if stats and not any("price" in c for _, c in cards):
    print("Hinweis: noch kein Seitenwert gespeichert - einmal „Alles übertragen“ mit dem Lesezeichen v19 ausführen.")
