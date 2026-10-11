"""Prüft die letzten Versand-Schübe aller Banner: Passen die Zahlen der Seite (Karten, Coins) zu den Karten
des Banners? Liest nur die Bot-Datenbank, ändert nichts.

Je Schub: Karten- und Coin-Zuwachs laut Seite, Kartenwert (×1,1), und ob sich das erklären lässt:
  ✅ passt       eine Karte mit genau diesem Wert / Kombination aus Karten des Banners
  ⏳ Teilschritt  Seite zählt Karte und Coins getrennt (1 Karte mit 0 Coins, kurz danach Coins ohne Karte)
  ❓ unklar      mehrere Karten, keine eindeutige Kombination
  ❌ unmöglich   weniger Coins, als die billigsten Karten wert sind (Zähler der Seite falsch)
Dazu: Passen einzelne Karten besser zu ×1,0 oder ×1,08 als zu ×1,1? (Hinweis, falls die Seite anders rechnet)

Aufruf auf dem VPS:
    docker exec -i gtcha-discord-bot python - < tools/ship_check.py              # letzte 3 Tage
    docker exec -i gtcha-discord-bot python - 7 < tools/ship_check.py            # letzte 7 Tage
    docker exec -i gtcha-discord-bot python - 7 24237 < tools/ship_check.py      # nur ein Banner
"""

import json
import os
import sqlite3
import sys
from collections import Counter
from datetime import datetime, timedelta

sys.path.insert(0, "/app")
from utils.banner_info import berlin_time  # noqa: E402
from utils.card_pool import explain_batch, fmt_coins, tracked_units  # noqa: E402

args = [a for a in sys.argv[1:] if a.isdigit()]
days = int(args[0]) if args else 3
only = int(args[1]) if len(args) > 1 else None
db = sqlite3.connect(os.getenv("BOT_DB", "/app/data/gtcha_bot.db"))
db.row_factory = sqlite3.Row
since = (datetime.now() - timedelta(days=days)).isoformat()
rows = db.execute("SELECT banner_id, old_cards, new_cards, old_coins, new_coins, changed_at FROM shipment_history "
                  "WHERE changed_at > ?" + (" AND banner_id = ?" if only else "") + " ORDER BY banner_id, id",
                  (since, only) if only else (since,)).fetchall()
banners = {r["pack_id"]: r for r in db.execute("SELECT pack_id, category, price_coins, card_pool FROM banners")}

# Schübe desselben Banners innerhalb von 3 Minuten zusammenfassen (Seite zählt Karte und Coins teils getrennt)
batches = []
for r in rows:
    t = datetime.fromisoformat(r["changed_at"])
    cards, coins = (r["new_cards"] or 0) - (r["old_cards"] or 0), (r["new_coins"] or 0) - (r["old_coins"] or 0)
    last = batches[-1] if batches else None
    if last and last["pid"] == r["banner_id"] and t - last["t_end"] <= timedelta(minutes=3):
        last.update(cards=last["cards"] + cards, coins=last["coins"] + coins, t_end=t, steps=last["steps"] + 1)
    else:
        batches.append({"pid": r["banner_id"], "t": t, "t_end": t, "cards": cards, "coins": coins, "steps": 1})


def near(values, target, factor):
    """Karten, deren Wert zum gezählten Betrag passt (auf ±1 %)."""
    return [v for v in values if abs(v - target * factor) <= max(2, v * 0.01)]


verdicts, factors, cross = Counter(), Counter(), Counter()
all_cards = [(pid, int(c.get("value") or 0)) for pid, r in banners.items() if r["card_pool"]
             for c in (json.loads(r["card_pool"]).get("cards") or []) if c.get("value")]
print(f"Versand-Schübe der letzten {days} Tage: {len(batches)} (aus {len(rows)} Zähler-Änderungen)\n")
for b in batches:
    row = banners.get(b["pid"])
    pool = json.loads(row["card_pool"]) if row and row["card_pool"] else {}
    cards = pool.get("cards") or []
    values = sorted({int(c.get("value") or 0) for c in cards if c.get("value")})
    gross = round(b["coins"] * 1.1)
    when = berlin_time(int(b["t"].timestamp())).strftime("%d.%m. %H:%M")
    head = f"{when}  {b['pid']}  {b['cards']:+d} Karten  {b['coins']:+,} gezählt = {gross:,} Kartenwert".replace(",", ".")
    head += f" ({b['steps']} Schritte)" if b["steps"] > 1 else ""
    if b["cards"] <= 0 and b["coins"] <= 0:
        continue
    if b["cards"] > 0 and b["coins"] == 0:
        verdict, note = "⏳", "nur Karte gezählt - Coins folgen später"
    elif b["cards"] <= 0:
        verdict, note = "⏳", "Coins ohne neue Karte (Nachzählung/Korrektur der Seite)"
    elif not values:
        verdict, note = "❓", "keine Kartenliste gespeichert"
    elif gross < b["cards"] * values[0] * 0.9:
        verdict, note = "❌", f"weniger als {b['cards']}× billigste Karte ({fmt_coins(values[0])}) - Zähler der Seite falsch"
    elif b["cards"] == 1:
        hit = near(values, b["coins"], 1.1)
        for f in (1.1, 1.08, 1.0):
            if near(values, b["coins"], f):
                factors[f] += 1
        if hit:
            names = [c["name"] for c in cards if int(c.get("value") or 0) in hit][:3]
            verdict, note = "✅", f"Karte {fmt_coins(hit[0])}: {', '.join(n[:40] for n in names)}"
        elif (off := explain_batch(pool, 1, b["coins"], set(), price=row["price_coins"])).get("off_value"):
            # wie der Bot: Hit, für den die Seite einen anderen (meist niedrigeren) Wert zählt
            units = {u["key"]: u for u in tracked_units(pool)}
            keys = off["certain"] + [k for g in off["groups"] for k in g["keys"]]
            shown = ", ".join(f"{units[k]['name'][:30]} {fmt_coins(units[k]['value'])} (×{gross / units[k]['value']:.2f})"
                              for k in keys[:3] if k in units)
            verdict, note = "🔶", f"Hit, Seite zählt anderen Wert: {shown}" + (" (eins davon)" if len(keys) > 1 else "")
        else:
            alt = [(f, near(values, b["coins"], f)) for f in (1.08, 1.0) if near(values, b["coins"], f)]
            verdict = "❓"
            note = "kein Kartenwert passt" + (f" (aber bei ×{alt[0][0]}: {fmt_coins(alt[0][1][0])})" if alt else "")
            # Spurensuche: nächstliegende Karten, frühere Werte (Preisänderung), Karten anderer Banner mit dem Wert
            close = sorted(values, key=lambda v: abs(v - gross))[:2]
            note += "\n     nächste Karten: " + ", ".join(f"{fmt_coins(v)} ({v / gross - 1:+.1%})" for v in close)
            old = [r for r in db.execute("SELECT name, old_value, new_value, changed_at FROM card_value_history "
                                         "WHERE banner_id = ?", (b["pid"],)) if near([r["old_value"]], b["coins"], 1.1)]
            if old:
                note += "\n     früherer Wert passt: " + ", ".join(
                    f"{r['name'][:40]} ({fmt_coins(r['old_value'])} -> {fmt_coins(r['new_value'])} am {r['changed_at'][:10]})" for r in old[:3])
            others = sorted({pid for pid, c in all_cards if near([c], b["coins"], 1.1) and pid != b["pid"]})
            if others:
                note += f"\n     Karte mit diesem Wert in anderen Bannern: {', '.join(map(str, others[:8]))}"
                cross[b["pid"]] += 1
    else:
        res = explain_batch(pool, b["cards"], b["coins"], set(), price=row["price_coins"])
        ok = res["kind"] in ("hits", "normal", "maybe")
        verdict = "✅" if ok else "❓"
        note = {"hits": "mit Hit(s)", "normal": "nur normale Karten", "maybe": "passt (Hits oder normale Karten)",
                "unclear": "keine passende Kombination", "too_big": "zu groß zum Zerlegen"}.get(res["kind"], res["kind"])
        if res.get("certain"):
            note += f" · sicher: {', '.join(res['certain'])[:80]}"
    verdicts[verdict] += 1
    print(f"{verdict} {head}\n     {note}")

total = sum(verdicts.values())
print(f"\nERGEBNIS: {total} Schübe · ✅ {verdicts['✅']} passen · 🔶 {verdicts['🔶']} Hit mit anderem Wert · ⏳ {verdicts['⏳']} Teilschritte · "
      f"❓ {verdicts['❓']} unklar · ❌ {verdicts['❌']} unmöglich")
if cross:
    print(f"Einzelkarten ohne Treffer im eigenen Banner, aber mit gleichem Wert in einem anderen: {sum(cross.values())}")
if factors:
    print("Einzelkarten passen zu ×1,1: {} · ×1,08: {} · ×1,0: {}  (der Bot rechnet mit ×1,1)".format(
        factors[1.1], factors[1.08], factors[1.0]))
