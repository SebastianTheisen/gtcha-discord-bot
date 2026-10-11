"""Prüft, ob der Bot nach einem Umbau von gtchaxonline.com noch alles bekommt. Ändert nichts.

1. Datenbank: Wie frisch sind die Daten (letzte Pack-Bewegung, letzte Rohdaten der Seite, neue Banner,
   Kartenpools, Bilder)?
2. Seite (genau wie der Bot, gleicher Browser/Proxy): pack/list, Kartenliste eines Banners, Banner auf der
   Startseite - mit allen Feldern, die der Bot benutzt. Fehlt ein Feld, steht es unter ❌.

Aufruf auf dem VPS:
    docker exec -i gtcha-discord-bot python - < tools/site_check.py
"""

import asyncio
import json
import sqlite3
import sys
from datetime import datetime, timedelta

sys.path.insert(0, "/app")

DB = "/app/data/gtcha_bot.db"
# Felder aus pack/list, die der Bot liest (Pack-Zahlen, Preis, Versand/Umwandlung, Kategorie, Start/Ende ...)
PACK_FIELDS = ["id", "name", "point", "pack_count", "total_pack_count", "total_sendcount", "total_sendprice",
               "total_kangen", "total_sendpeople", "start_date", "end_date", "image", "max_buy_count",
               "is_before", "is_bonus", "card_type", "password_flag", "min_charge_amount", "badges"]
# wichtig = ohne das geht Erkennung/Ø Rückgabe nicht
PACK_REQUIRED = ["id", "point", "pack_count", "total_pack_count", "total_sendcount", "total_sendprice", "total_kangen"]
CARD_FIELDS = ["id", "name", "buy_point", "duplication", "action_type", "image_url", "model_number", "rarity"]
CARD_REQUIRED = ["id", "name", "buy_point", "duplication", "action_type"]


def ago(iso):
    try:
        mins = (datetime.now() - datetime.fromisoformat(iso)).total_seconds() / 60
    except (TypeError, ValueError):
        return "nie"
    return f"vor {mins:.0f} Min" if mins < 120 else f"vor {mins / 60:.1f} Std"


def fields(name, items, wanted, required):
    present = {k for it in items for k in it}
    missing = [k for k in wanted if k not in present]
    bad = [k for k in required if k in missing]
    print(f"  {name}: {len(items)} Einträge, Felder fehlen: {', '.join(missing) or 'keine'}")
    if bad:
        print(f"  ❌ WICHTIGE Felder fehlen: {', '.join(bad)}")
    new = sorted(present - set(wanted))
    if new:
        print(f"  (weitere Felder der Seite: {', '.join(new[:25])}{' …' if len(new) > 25 else ''})")
    return not bad


def db_check():
    print("== 1. Datenbank (was der Bot zuletzt bekommen hat)")
    db = sqlite3.connect(f"file:{DB}?mode=ro", uri=True)
    q = lambda sql, *a: db.execute(sql, a).fetchone()
    active = q("SELECT count(*) FROM banners WHERE is_active = 1")[0]
    print(f"  aktive Banner: {active}")
    print(f"  letzte Pack-Bewegung: {ago(q('SELECT max(changed_at) FROM pack_history')[0])}")
    print(f"  letzte Rohdaten-Änderung (pack/list): {ago(q('SELECT max(changed_at) FROM api_log')[0])}")
    since = (datetime.now() - timedelta(hours=1)).isoformat()
    print(f"  Banner mit Änderungen in der letzten Stunde: {q('SELECT count(DISTINCT banner_id) FROM api_log WHERE changed_at > ?', since)[0]}")
    day = (datetime.now() - timedelta(days=1)).isoformat()
    new = db.execute("SELECT pack_id, created_at, card_pool IS NOT NULL, image_url IS NOT NULL, price_coins, "
                     "total_packs FROM banners WHERE created_at > ? ORDER BY pack_id DESC", (day,)).fetchall()
    print(f"  neue Banner (24 Std): {len(new)}")
    for pid, created, pool, img, price, total in new[:10]:
        print(f"    {pid}: {ago(created)} · Kartenpool {'✅' if pool else '❌'} · Bild {'✅' if img else '❌'} · "
              f"Preis {price} · Packs {total}")
    nopool = q("SELECT count(*) FROM banners WHERE is_active = 1 AND card_pool IS NULL")[0]
    print(f"  aktive Banner ohne Kartenpool: {nopool}")


async def site_check():
    print("\n== 2. Seite (live abgefragt wie der Bot)")
    from scraper.gtcha_scraper import FETCH_CARD_LIST_JS, GTCHAScraper
    ok = True
    async with GTCHAScraper() as s:
        items = await s.fetch_pack_list()
        if not items:
            print("  ❌ pack/list liefert KEINE Banner - der Bot bekommt keine Pack-Zahlen mehr")
            return False
        lst = list(items.values())
        ok &= fields("pack/list", lst, PACK_FIELDS, PACK_REQUIRED)
        sample = lst[0]
        print("  Beispiel:", json.dumps({k: sample.get(k) for k in PACK_REQUIRED + ["name"]}, ensure_ascii=False)[:300])
        pid = int(sample["id"])
        page = await s._context.new_page()
        try:
            await page.goto(f"{s.base_url}/api/user/point", wait_until="domcontentloaded", timeout=60000)
            cards = await page.evaluate(FETCH_CARD_LIST_JS, pid)
            if not cards:
                print(f"  ❌ Kartenliste von {pid} leer/nicht abrufbar - keine Hit-Listen/Erkennung für neue Banner")
                ok = False
            else:
                ok &= fields(f"Kartenliste {pid}", cards, CARD_FIELDS, CARD_REQUIRED)
            # Startseite: Banner-Kacheln und Kategorie-Reiter (Tab-Scrape alle 15 Min, Titel/Bild/Ende)
            await page.goto(s.base_url, wait_until="domcontentloaded", timeout=90000)
            try:
                await page.wait_for_selector("[data-pack-id]", timeout=30000)
            except Exception:
                pass
            tiles = await page.eval_on_selector_all("[data-pack-id]", "els => els.length")
            tabs = await page.eval_on_selector_all(".pack_menu_list .pack_menu", "els => els.map(e => e.innerText.trim())")
            print(f"  Startseite: {tiles} Banner-Kacheln ([data-pack-id]), Reiter: {', '.join(tabs) or 'KEINE'}")
            if not tiles or not tabs:
                print("  ⚠️ Startseite anders aufgebaut - der Tab-Scrape (Titel, Bild, Enddatum) greift evtl. nicht mehr; "
                      "Pack-Zahlen kommen trotzdem aus pack/list")
            detail = await page.evaluate("""async () => {
                const r = await fetch('/api/user/detail', {credentials: 'include'});
                return r.status;
            }""")
            print(f"  /api/user/detail (Lesezeichen, Mitglieds-ID): HTTP {detail} "
                  f"{'(ohne Login normal: 401/403)' if detail in (401, 403) else ''}")
        finally:
            await page.close()
    return ok


async def main():
    db_check()
    try:
        ok = await site_check()
    except Exception as e:
        print(f"  ❌ Abfrage fehlgeschlagen: {type(e).__name__}: {e}")
        ok = False
    print("\n== Ergebnis:", "✅ alles Wichtige kommt an" if ok else "❌ etwas Wichtiges fehlt (siehe oben)")


asyncio.run(main())
