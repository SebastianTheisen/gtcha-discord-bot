"""Lädt die Kartenlisten ALLER aktiven Banner neu (wie der Bot, über Tor), übernimmt geänderte Kartenwerte und
zeigt im Terminal, was sich geändert hat (Werte, neue/fehlende Karten, Packs der Liste).

Aufruf auf dem VPS (dauert je nach Anzahl der Banner einige Minuten):
    docker exec -i gtcha-discord-bot python - < tools/refresh_pools.py            # laden und übernehmen
    docker exec -i gtcha-discord-bot python - nur-zeigen < tools/refresh_pools.py # nur vergleichen, nichts speichern

Übernommene Änderungen stehen danach auch in tools/value_changes.py; Hit-Listen und Ø-Rückgabe passt der Bot
beim nächsten Scrape (alle 5 Minuten) an.
"""

import asyncio
import json
import sys

sys.path.insert(0, "/app")

from database.db import Database  # noqa: E402
from scraper.gtcha_scraper import GTCHAScraper  # noqa: E402
from utils.card_pool import card_value_changes, fmt_coins  # noqa: E402
from utils.translate import translate_pool  # noqa: E402

DRY = "nur-zeigen" in sys.argv[1:]
BATCH = 10


def describe(pid, old, new):
    """Unterschiede zweier Pools als Zeilen (leer = unverändert)."""
    lines = []
    if not old or not old.get("cards"):
        return [f"  neu geladen: {len(new.get('cards') or [])} Karten, {new.get('total_count')} Packs"]
    for c in card_value_changes(old, new):
        lines.append(f"  ✏️ {c['name'][:60]}: {fmt_coins(c['old'])} -> {fmt_coins(c['new'])} Coins")
    before = {str(c["id"]): c for c in old.get("cards") or []}
    after = {str(c["id"]): c for c in new.get("cards") or []}
    for cid in sorted(set(after) - set(before)):
        lines.append(f"  🆕 {after[cid]['name'][:60]} ({fmt_coins(after[cid]['value'])} Coins, {after[cid].get('copies')}×)")
    for cid in sorted(set(before) - set(after)):
        lines.append(f"  ❌ weg: {before[cid]['name'][:60]} ({fmt_coins(before[cid]['value'])} Coins)")
    for cid in sorted(set(before) & set(after)):
        if before[cid].get("copies") != after[cid].get("copies"):
            lines.append(f"  #  {after[cid]['name'][:60]}: Anzahl {before[cid].get('copies')} -> {after[cid].get('copies')}")
    if old.get("total_count") != new.get("total_count"):
        lines.append(f"  📦 Packs laut Liste: {old.get('total_count')} -> {new.get('total_count')}")
    return lines


async def main():
    db = Database("/app/data/gtcha_bot.db")
    rows = await db.get_active_banners()
    pids = sorted(rows, reverse=True)
    print(f"{len(pids)} aktive Banner - Kartenlisten werden neu geladen{' (nur zeigen, nichts wird gespeichert)' if DRY else ''} …\n")
    changed = unchanged = failed = 0
    async with GTCHAScraper() as scraper:
        for i in range(0, len(pids), BATCH):
            part = pids[i:i + BATCH]
            try:
                pools = await scraper.fetch_card_pools(part)
            except Exception as e:
                print(f"❌ {part}: {type(e).__name__}: {e}")
                failed += len(part)
                continue
            for pid in part:
                new = pools.get(pid)
                if not new:
                    print(f"⚠️ {pid}: Kartenliste nicht abrufbar")
                    failed += 1
                    continue
                if not new.get("total_count"):
                    print(f"🙈 {pid}: Kartenliste leer/versteckt - gespeicherte bleibt")
                    unchanged += 1
                    continue
                old = json.loads(rows[pid]["card_pool"]) if rows[pid].get("card_pool") else None
                new, _ = translate_pool(new)
                lines = describe(pid, old, new)
                if not lines:
                    unchanged += 1
                    continue
                changed += 1
                print(f"🔄 {pid} ({rows[pid].get('category')}, {fmt_coins(rows[pid].get('price_coins') or 0)} Coins):")
                print("\n".join(lines))
                if not DRY:
                    await db.save_card_pool(pid, new)
                    values = card_value_changes(old, new)
                    if values:
                        await db.save_value_changes(pid, values)
            print(f"… {min(i + BATCH, len(pids))} von {len(pids)} geprüft")
    print(f"\nERGEBNIS: {changed} Banner geändert{' (nicht gespeichert)' if DRY else ' und übernommen'}, "
          f"{unchanged} unverändert, {failed} nicht abrufbar.")


asyncio.run(main())
