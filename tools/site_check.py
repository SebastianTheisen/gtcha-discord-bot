"""Komplette Prüfung nach einem Umbau von gtchaxonline.com: bekommen Bot, App und Lesezeichen noch alles? Ändert nichts.

Geprüft wird alles, was der Bot von der Seite liest - genau wie der Bot (gleicher Browser, gleicher Proxy/Tor):
  1. Datenbank: wie frisch sind die Daten
  2. pack/list (normaler Abruf und schneller Abruf): Banner, alle benutzten Felder, Werte plausibel
     (Pack-Zahlen, Preis, Kategorie, Start/Ende, Bild, Kaufbedingungen, Versand/Umwandlung)
  3. Abgleich mit der Datenbank (gleiche Banner, gleiche Pack-Zahlen)
  4. Kartenlisten (je Kategorie ein Banner): Seiten, Felder, Summe der Karten = Packs des Banners
  5. Store-Seite (Schmuck & Co.)
  6. Startseite mit Kategorie-Reitern (Titel, Bild, Enddatum, Zuglimit aus den Kacheln)
  7. Detailseite eines Banners (Link "Öffnen" in App und Discord)
  8. Bilder (Banner und Karte): laden vollständig
  9. Lesezeichen-Seiten und Mitglieds-Abfrage (ohne Login nur Erreichbarkeit - Inhalt prüft das Lesezeichen selbst)
 10. Neue Banner ohne Kartenliste / ohne Bild: Werte der Seite (card_hide_flag usw.) und was die öffentliche
     Detailseite einem Besucher zeigt - mit den Abfragen, die die Seite dabei selbst lädt (nur mitgelesen,
     keine eigenen Versuche). Versteckt GTCHA eine Kartenliste, bleibt sie versteckt.

Das Ergebnis landet in data/site_check.txt und wird automatisch in den Admin-Kanal von Discord geschickt.

Aufruf auf dem VPS (dauert 2-5 Minuten):
    docker exec -i gtcha-discord-bot python - < tools/site_check.py
"""

import asyncio
import sqlite3
import sys
from collections import Counter
from datetime import datetime, timedelta

sys.path.insert(0, "/app")

DB = "/app/data/gtcha_bot.db"
BASE = "https://gtchaxonline.com"
PACK_FIELDS = ["id", "name", "point", "pack_count", "total_pack_count", "total_sendcount", "total_sendprice",
               "total_kangen", "total_sendpeople", "start_date", "end_date", "image", "max_buy_count",
               "is_before", "is_bonus", "card_type", "password_flag", "min_charge_amount", "badges"]
PACK_REQUIRED = ["id", "point", "pack_count", "total_pack_count", "total_sendcount", "total_sendprice",
                 "total_kangen", "card_type", "end_date"]
CARD_FIELDS = ["id", "name", "buy_point", "duplication", "action_type", "image_url", "model_number", "rarity"]
CARD_REQUIRED = ["id", "name", "buy_point", "duplication", "action_type"]
SYNC_PAGES = ["undecided-detail", "pending-detail", "shipped-detail", "downloaded-detail",
              "buy-point-history", "purchase-history", "ticket-history", "change-member"]

results = []   # (Bereich, Status, Text)


def report(area, status, text):
    results.append((area, status, text))
    print(f"  {status} {area}: {text}")


def num(v):
    try:
        return int(float(v))
    except (TypeError, ValueError):
        return None


def ago_min(iso):
    try:
        return (datetime.now() - datetime.fromisoformat(iso)).total_seconds() / 60
    except (TypeError, ValueError):
        return None


# --- 1. Datenbank ---
def check_db():
    print("\n== 1. Datenbank (was der Bot zuletzt bekommen hat)")
    db = sqlite3.connect(f"file:{DB}?mode=ro", uri=True)
    q = lambda sql, *a: db.execute(sql, a).fetchone()
    active = q("SELECT count(*) FROM banners WHERE is_active = 1")[0]
    move = ago_min(q("SELECT max(changed_at) FROM pack_history")[0])
    raw = ago_min(q("SELECT max(changed_at) FROM api_log")[0])
    report("Aktive Banner", "✅" if active else "❌", str(active))
    report("Letzte Pack-Bewegung", "✅" if move is not None and move < 60 else "⚠️",
           "nie" if move is None else f"vor {move:.0f} Min")
    report("Letzte Rohdaten der Seite", "✅" if raw is not None and raw < 60 else "⚠️",
           "nie" if raw is None else f"vor {raw:.0f} Min")
    day = (datetime.now() - timedelta(days=1)).isoformat()
    new = db.execute("SELECT pack_id, card_pool IS NOT NULL, image_url IS NOT NULL, sale_end_date IS NOT NULL "
                     "FROM banners WHERE created_at > ? ORDER BY pack_id DESC", (day,)).fetchall()
    # Bild: neue Banner haben seit dem Umbau keins mehr (App zeigt die Top-Karten) - zählt nicht als Fehler
    bad = [str(p) for p, pool, img, end in new if not (pool and end)]
    report("Neue Banner (24 Std)", "✅" if not bad else "⚠️",
           f"{len(new)} neu" + (f", unvollständig (Kartenpool/Ende fehlt): {', '.join(bad[:10])}" if bad else ""))
    nopool = q("SELECT count(*) FROM banners WHERE is_active = 1 AND card_pool IS NULL")[0]
    report("Aktive Banner ohne Kartenpool", "✅" if not nopool else "⚠️", str(nopool))
    return {r[0]: r[1] for r in db.execute("SELECT pack_id, current_packs FROM banners WHERE is_active = 1")}


# --- 2./3. pack/list ---
def check_pack_list(items, db_packs):
    import json as _json
    from utils.banner_info import category_for, jst_timestamp, sale_end_timestamp, set_card_type_names
    try:   # Namen neuer Kategorien wie im Bot (z. B. Yu-Gi-Oh!)
        row = sqlite3.connect(DB).execute("SELECT value FROM bot_meta WHERE key = 'card_type_names'").fetchone()
        set_card_type_names(_json.loads(row[0]) if row else {})
    except Exception:
        pass
    print("\n== 2. pack/list (Banner-Liste der Seite)")
    lst = list(items.values())
    report("Banner in pack/list", "✅" if lst else "❌", str(len(lst)))
    if not lst:
        return
    present = {k for it in lst for k in it}
    missing = [k for k in PACK_FIELDS if k not in present]
    hard = [k for k in PACK_REQUIRED if k in missing]
    report("Felder", "❌" if hard else ("⚠️" if missing else "✅"),
           f"fehlen: {', '.join(missing) or 'keine'}" + (f" (WICHTIG: {', '.join(hard)})" if hard else ""))
    extra = sorted(present - set(PACK_FIELDS))
    if extra:
        print(f"     weitere Felder der Seite: {', '.join(extra[:30])}")
    # Werte
    bad_packs = [it["id"] for it in lst if num(it.get("pack_count")) is None or num(it.get("total_pack_count")) is None
                 or num(it.get("pack_count")) > num(it.get("total_pack_count"))]
    report("Pack-Zahlen", "✅" if not bad_packs else "❌", "plausibel" if not bad_packs
           else f"unlesbar/zu groß bei {bad_packs[:10]}")
    bad_price = [it["id"] for it in lst if num(it.get("point")) is None]
    report("Preise", "✅" if not bad_price else "❌", "lesbar" if not bad_price else f"unlesbar bei {bad_price[:10]}")
    cats = Counter(category_for(it) or f"unbekannt(card_type={it.get('card_type')}, bonus={it.get('is_bonus')})"
                   for it in lst)
    unknown = {k: v for k, v in cats.items() if k.startswith("unbekannt")}
    report("Kategorien", "✅" if not unknown else "⚠️", ", ".join(f"{k}: {v}" for k, v in cats.most_common()))
    end_bad = [it["id"] for it in lst if it.get("end_date") and not sale_end_timestamp(it.get("end_date"))]
    report("Verkaufsende", "✅" if not end_bad else "❌", "lesbar" if not end_bad
           else f"Format unbekannt bei {end_bad[:5]}: {items[end_bad[0]].get('end_date')!r}")
    start_bad = [it["id"] for it in lst if it.get("start_date") and not jst_timestamp(it.get("start_date"))]
    upcoming = sum(1 for it in lst if it.get("is_before"))
    report("Verkaufsstart", "✅" if not start_bad else "❌",
           f"{upcoming} angekündigt" if not start_bad
           else f"Format unbekannt bei {start_bad[:5]}: {items[start_bad[0]].get('start_date')!r}")
    no_img = [it["id"] for it in lst if not (isinstance(it.get("image"), list) and it["image"])]
    odd_img = [pid for pid in no_img if items[pid].get("image") not in ([], None, "")]
    report("Bannerbilder", "✅" if not odd_img else "⚠️",
           (f"vorhanden; ohne eigenes Bild (App zeigt Top-Karten): {no_img[:10]}" if no_img else "vorhanden")
           if not odd_img else f"anderes Format bei {odd_img[:10]} (Beispiel: {items[odd_img[0]].get('image')!r})")
    badges = [it["id"] for it in lst if it.get("badges") is not None and not isinstance(it.get("badges"), list)]
    report("Kaufbedingungen (Rang)", "✅" if not badges else "⚠️", "lesbar" if not badges
           else f"anderes Format bei {badges[:5]}")
    ship_bad = [it["id"] for it in lst if any(it.get(k) is not None and num(it.get(k)) is None
                                              for k in ("total_sendcount", "total_sendprice", "total_kangen"))]
    report("Versand/Umwandlung (Hit-Erkennung)", "✅" if not ship_bad else "❌", "lesbar" if not ship_bad
           else f"unlesbar bei {ship_bad[:10]}")

    print("\n== 3. Abgleich mit der Datenbank")
    ids, known = set(items), set(db_packs)
    gone = known - ids
    report("Aktive Banner auch in pack/list", "✅" if not gone else "⚠️",
           "alle" if not gone else f"{len(gone)} fehlen in pack/list: {sorted(gone)[:10]}")
    diff = [pid for pid in ids & known if db_packs[pid] is not None
            and abs((num(items[pid].get("pack_count")) or 0) - db_packs[pid]) > 20]
    report("Pack-Zahlen wie in der Datenbank", "✅" if not diff else "⚠️",
           "passen" if not diff else f"weichen stark ab bei {diff[:10]}")


# --- 4. Kartenlisten ---
async def check_card_lists(page, items):
    from scraper.gtcha_scraper import FETCH_CARD_LIST_JS
    from utils.banner_info import category_for
    from utils.card_pool import summarize_cards
    print("\n== 4. Kartenlisten (je Kategorie ein Banner)")
    picked = {}
    for it in items.values():
        cat = category_for(it)
        if cat and cat not in picked and num(it.get("total_pack_count")):
            picked[cat] = it
    for cat, it in picked.items():
        pid = int(it["id"])
        try:
            cards = await page.evaluate(FETCH_CARD_LIST_JS, pid)
        except Exception as e:
            report(f"Karten {pid} ({cat})", "❌", f"Fehler: {e}")
            continue
        if not cards:
            report(f"Karten {pid} ({cat})", "❌", "leer/nicht abrufbar")
            continue
        present = {k for c in cards for k in c}
        hard = [k for k in CARD_REQUIRED if k not in present]
        soft = [k for k in CARD_FIELDS if k not in present and k not in hard]
        pool = summarize_cards(cards)
        total = num(it.get("total_pack_count"))
        count_ok = pool and abs(pool["total_count"] - total) <= max(2, total // 100)
        kinds = Counter(str(c.get("action_type")) for c in cards)
        report(f"Karten {pid} ({cat})", "❌" if hard or not pool else ("⚠️" if soft or not count_ok else "✅"),
               f"{len(cards)} Karten, Summe {pool['total_count'] if pool else '–'} / {total} Packs"
               + (f", WICHTIGE Felder fehlen: {', '.join(hard)}" if hard else "")
               + (f", fehlen: {', '.join(soft)}" if soft else "")
               + f", Arten (action_type): {dict(kinds)}")
    return picked


# --- 5. Store ---
async def check_store():
    print("\n== 5. Store-Seite")
    from config import SCRAPER_PROXY
    from utils.store_client import StoreClient
    client = StoreClient(BASE, SCRAPER_PROXY)
    try:
        items, types, ok = await client.fetch_store([], discover=True)
        report("Store", "✅" if ok and items else "⚠️",
               f"{len(items)} Packs, Kartenarten {sorted(types)}" if ok else "Store-Seite fragt keine Pack-Liste mehr ab")
    except Exception as e:
        report("Store", "❌", f"Fehler: {type(e).__name__}: {e}")
    finally:
        await client.close()


# --- 6. Startseite / Reiter ---
async def check_tabs(items):
    print("\n== 6. Startseite mit Kategorie-Reitern (Titel, Bild, Ende, Zuglimit)")
    from scraper.gtcha_scraper import GTCHAScraper
    try:
        async with GTCHAScraper() as s:
            banners = await s.scrape_all_banners()
    except Exception as e:
        report("Startseite", "❌", f"Fehler: {type(e).__name__}: {e}")
        return
    report("Banner auf der Startseite", "✅" if banners else "❌",
           f"{len(banners)} gefunden (pack/list: {len(items)})")
    if not banners:
        return
    # Seit dem Umbau liefern die Kacheln keine Titel/Bilder/Preise/Enddaten mehr - der Bot nimmt sie aus pack/list.
    # Hier zählt nur noch, dass die Startseite alle Banner zeigt (fürs Erkennen beendeter Banner).
    for field, label in (("title", "Titel"), ("image_url", "Bild"), ("sale_end_date", "Enddatum"),
                         ("price_coins", "Preis")):
        missing = [b.pack_id for b in banners if not getattr(b, field)]
        report(f"Kacheln: {label} (nur Hinweis; Bild/Preis/Ende kommen aus pack/list)", "✅" if not missing else "ℹ️",
               f"bei {len(banners) - len(missing)} von {len(banners)}")
    titles = sum(1 for it in items.values() if str(it.get("name") or "").strip())
    report("Interner Name in pack/list (kein Titel)", "ℹ️", f"bei {titles} von {len(items)} "
           f"(z. B. {next((it.get('name') for it in items.values() if it.get('name')), '–')!r})")


# --- 7./8./9. Detailseite, Bilder, Lesezeichen-Seiten ---
async def check_pages(page, items, picked):
    print("\n== 7. Detailseite (Link „Öffnen“)")
    pid = next(iter(picked.values()))["id"] if picked else next(iter(items))
    try:
        resp = await page.goto(f"{BASE}/pack-detail?packId={pid}", wait_until="domcontentloaded", timeout=60000)
        try:   # neue Seite lädt den Inhalt nach
            await page.wait_for_load_state("networkidle", timeout=20000)
        except Exception:
            pass
        await page.wait_for_timeout(2000)
        text = await page.evaluate("document.body ? document.body.innerText.length : 0")
        report("Detailseite", "✅" if resp and resp.status < 400 and text > 200 else "ℹ️",
               f"HTTP {resp.status if resp else '–'}, {text} Zeichen Text, Adresse {page.url}")
    except Exception as e:
        report("Detailseite", "❌", f"Fehler: {type(e).__name__}: {e}")

    print("\n== 8. Bilder")
    import aiohttp
    try:
        from webapp.images import complete
    except Exception:
        complete = lambda data: len(data) > 0
    urls = []
    it = items.get(int(pid)) or next(iter(items.values()))
    if isinstance(it.get("image"), list) and it["image"]:
        urls.append(("Bannerbild", BASE + it["image"][0].split("?")[0]))
    from scraper.gtcha_scraper import FETCH_CARD_LIST_JS
    try:
        await page.goto(f"{BASE}/api/user/point", wait_until="domcontentloaded", timeout=60000)
        cards = await page.evaluate(FETCH_CARD_LIST_JS, int(pid)) or []
        img = next((c.get("image_url") for c in cards if c.get("image_url")), None)
        if img:
            urls.append(("Kartenbild", img if img.startswith("http") else BASE + img))
    except Exception:
        pass
    async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=30),
                                     headers={"User-Agent": "Mozilla/5.0"}) as session:
        for label, url in urls:
            try:
                async with session.get(url) as r:
                    data = await r.read()
                    ok = r.status == 200 and complete(data)
                    report(label, "✅" if ok else "❌",
                           f"HTTP {r.status}, {r.headers.get('Content-Type')}, {len(data)} Bytes"
                           + ("" if ok else " (unvollständig/kein Bild)") + f" · {url}")
            except Exception as e:
                report(label, "❌", f"Fehler: {type(e).__name__}: {e} · {url}")
    if not urls:
        report("Bilder", "⚠️", "keine Bildadresse gefunden")

    print("\n== 9. Lesezeichen-Seiten (ohne Login: nur Erreichbarkeit)")
    for path in SYNC_PAGES:
        try:
            resp = await page.goto(f"{BASE}/{path}", wait_until="domcontentloaded", timeout=45000)
            moved = page.url.rstrip("/") != f"{BASE}/{path}"
            status = resp.status if resp else None
            report(f"/{path}", "✅" if status and status < 400 else "❌",
                   f"HTTP {status}" + (f" → weitergeleitet nach {page.url}" if moved else ""))
        except Exception as e:
            report(f"/{path}", "❌", f"Fehler: {type(e).__name__}: {e}")
    try:
        status = await page.evaluate("""async () => (await fetch('/api/user/detail', {credentials: 'include'})).status""")
        report("/api/user/detail (Mitglieds-ID)", "✅" if status in (200, 401, 403) else "⚠️",
               f"HTTP {status} (ohne Login sind 401/403 normal)")
    except Exception as e:
        report("/api/user/detail", "❌", f"Fehler: {e}")


# --- 10. Banner ohne Kartenliste / Bild ---
async def check_hidden(page, items):
    print("\n== 10. Banner ohne Kartenliste oder Bild")
    import json as _json
    db = sqlite3.connect(DB)
    pools = {r[0]: r[1] for r in db.execute("SELECT pack_id, card_pool FROM banners WHERE is_active = 1")}
    # nur aktive Banner des Bots (pack/list enthält auch beendete)
    empty = [pid for pid in pools if not (pools[pid] and '"cards": [{' in pools[pid])]
    hidden = sorted(pid for pid, it in items.items() if str(it.get("card_hide_flag")) == "1")
    report("Kartenliste versteckt (card_hide_flag=1)", "ℹ️", f"{len(hidden)} Banner"
           + (f": {', '.join(map(str, hidden))}" if hidden else ""))
    no_image = [pid for pid, it in items.items() if not it.get("image")]
    report("Banner ohne eigenes Bild", "ℹ️", f"{len(no_image)} von {len(items)}"
           + (f" (z. B. {', '.join(map(str, sorted(no_image)[:8]))})" if no_image else ""))
    report("Banner ohne Kartenliste", "✅" if not empty else "ℹ️", f"{len(empty)}"
           + (f": {', '.join(map(str, sorted(empty)[:15]))}" if empty else ""))
    picks = hidden + [p for p in sorted(empty, reverse=True) if p not in hidden]
    picks += [p for p in sorted(no_image, reverse=True) if p not in picks]
    for pid in picks[:5]:
        it = items.get(pid) or {}
        fields = {k: it.get(k) for k in ("card_hide_flag", "show_list_count", "is_before", "is_countdown",
                                          "rule_type", "lottery_method", "stock_count", "start_date")}
        report(f"{pid}: Werte der Seite", "ℹ️", _json.dumps(fields, ensure_ascii=False))
        seen, own = [], {}   # Abfragen, die die Detailseite selbst lädt (nur mitgelesen)

        async def on_response(r, seen=seen, own=own, pid=pid):
            if "/api/" in r.url:
                seen.append(f"{r.request.method} {r.url.replace(BASE, '')} -> {r.status}")
                if f"/pack/detail/{pid}" in r.url or f"/pack/card_list/{pid}/" in r.url:
                    try:
                        own["detail" if "/detail/" in r.url else "cards"] = await r.json()
                    except Exception:
                        pass
        page.on("response", on_response)
        try:
            await page.goto(f"{BASE}/pack-detail?packId={pid}", wait_until="domcontentloaded", timeout=60000)
            try:
                await page.wait_for_load_state("networkidle", timeout=20000)
            except Exception:
                pass
            await page.wait_for_timeout(2500)
            shown = await page.evaluate("""() => ({cards: document.querySelectorAll('img[src*="/card/"]').length,
                text: (document.body ? document.body.innerText : '').replace(/\\s+/g, ' ').slice(0, 300)})""")
            report(f"{pid}: Detailseite zeigt", "ℹ️", f"{shown['cards']} Kartenbilder · Text: {shown['text']!r}")
            report(f"{pid}: Abfragen der Detailseite", "ℹ️", " | ".join(dict.fromkeys(seen)) or "keine")
            det = (own.get("detail") or {}).get("detail") or {}
            if det:   # öffentliche Banner-Daten, die die Detailseite lädt: Bilder und Kartenliste
                keys = ["image", "image02", "image03", "image04", "image05", "image06", "card_type_name",
                        "show_cardlist", "card_hide_flag", "is_before"]
                report(f"{pid}: Banner-Daten der Detailseite", "ℹ️",
                       _json.dumps({k: det.get(k) for k in keys}, ensure_ascii=False)[:900])
                extra = sorted(set(det) - set(keys) - {"detail", "name"})
                report(f"{pid}: weitere Felder", "ℹ️", ", ".join(extra))
            cl = own.get("cards")
            report(f"{pid}: Kartenliste, die die Detailseite selbst lädt", "ℹ️",
                   "nicht geladen" if cl is None else
                   f"{len(cl.get('list') or [])} Karten auf Seite 1 (gesamt {(cl.get('page') or {}).get('all')})")
        except Exception as e:
            report(f"{pid}: Detailseite", "❌", f"Fehler: {type(e).__name__}: {e}")
        finally:
            page.remove_listener("response", on_response)


def post_to_discord(path):
    """Ergebnis als Datei in den Admin-Kanal (wie tools/post_file.py)."""
    import json as _json
    import os
    import aiohttp
    token, channel = os.getenv("DISCORD_TOKEN", ""), os.getenv("ADMIN_CHANNEL_ID", "")
    if not token or not channel.isdigit():
        print("Discord: DISCORD_TOKEN oder ADMIN_CHANNEL_ID fehlt - Datei liegt in data/site_check.txt")
        return

    async def send():
        form = aiohttp.FormData()
        form.add_field("payload_json", _json.dumps({"content": "🔎 Seiten-Prüfung (site_check)"}))
        with open(path, "rb") as f:
            form.add_field("files[0]", f.read(), filename=os.path.basename(path), content_type="text/plain")
        async with aiohttp.ClientSession() as session:
            async with session.post(f"https://discord.com/api/v10/channels/{channel}/messages", data=form,
                                    headers={"Authorization": f"Bot {token}"}) as r:
                print("Discord: gesendet" if r.status < 300 else f"Discord: Fehler {r.status} {await r.text()}")
    asyncio.run(send())


async def main():
    db_packs = {}
    try:
        db_packs = check_db()
    except Exception as e:
        report("Datenbank", "❌", f"{type(e).__name__}: {e}")
    from config import SCRAPER_PROXY
    from scraper.gtcha_scraper import GTCHAScraper
    from utils.pack_list_client import PackListClient
    items, picked = {}, {}
    try:
        fast = PackListClient(BASE, SCRAPER_PROXY)
        try:
            quick = await fast.fetch()
        finally:
            await fast.close()
        report("Schneller Abruf (jede Minute)", "✅" if quick else "❌", f"{len(quick)} Banner")
    except Exception as e:
        report("Schneller Abruf (jede Minute)", "❌", f"{type(e).__name__}: {e}")
    try:
        async with GTCHAScraper() as s:
            items = await s.fetch_pack_list()
            check_pack_list(items, db_packs)
            if items:
                page = await s._context.new_page()
                try:
                    await page.goto(f"{BASE}/api/user/point", wait_until="domcontentloaded", timeout=60000)
                    picked = await check_card_lists(page, items)
                    await check_pages(page, items, picked)
                    await check_hidden(page, items)
                finally:
                    await page.close()
    except Exception as e:
        report("pack/list", "❌", f"{type(e).__name__}: {e}")
    await check_store()
    await check_tabs(items)

    print("\n================ ERGEBNIS ================")
    bad = [r for r in results if r[1] == "❌"]
    warn = [r for r in results if r[1] == "⚠️"]
    info = [r for r in results if r[1] == "ℹ️"]
    print(f"✅ {sum(1 for r in results if r[1] == '✅')}   ⚠️ {len(warn)}   ❌ {len(bad)}")
    for area, status, text in bad + warn + info:
        print(f"{status} {area}: {text}")
    if not bad and not warn:
        print("Alles kommt an wie vorher.")


class Tee:
    """Ausgabe zusätzlich in data/site_check.txt schreiben."""
    def __init__(self, *streams):
        self.streams = streams

    def write(self, text):
        for st in self.streams:
            st.write(text)

    def flush(self):
        for st in self.streams:
            st.flush()


OUT = "/app/data/site_check.txt"
with open(OUT, "w", encoding="utf-8") as out:
    stdout, sys.stdout = sys.stdout, Tee(sys.stdout, out)
    try:
        print(f"Seiten-Prüfung {datetime.now():%d.%m.%Y %H:%M}")
        asyncio.run(main())
    finally:
        sys.stdout = stdout
post_to_discord(OUT)
