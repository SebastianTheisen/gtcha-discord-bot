import asyncio
from datetime import datetime

import pytest

from utils.app_bridge import AppBridge
from webapp.history import (build_history, parse_card_tiles, ingest, merge_newest_first, parse_cards, parse_coins, parse_member, profile,
                            plan_claims)

HEAD = "949\n1\n×\nMitglieds-ID\nTransaktionen\nGacha\nMünzen\nVerkauf\nTicket\n"
FOOT = "Xero Place Co., Ltd.\n\n〒330-0854\nTEL : +81 00\nMy Page"

COINS = HEAD + "Datum\nMünzen\nPreis\nHinweise (optional)\n" + "\n".join([
    "2026/10/01 21:08", "660", "¥0", "In Münzen umwandeln",
    "2026/10/01 21:07", "-1.000", "¥0", "Öffnen",
    "2026/10/01 21:06", "1.320", "¥0", "In Münzen umwandeln",
    "2026/10/01 21:05", "-2.000", "¥0", "Öffnen",
    "2026/10/01 20:49", "10.000", "¥8.000", "Münzen erhalten",
    "2026/09/30 02:31", "-10", "¥0", "Öffnen",
]) + "\n" + FOOT

PENDING = HEAD + ("Anfragedatum: 2026/09/28\n\n【30th】Pikachu ex\nSAR\nM6a126-103\n"
                  "Anfragedatum: 2026/09/24\n\nBuggy\nSP\nP-084\n\n[SAR Specification] Mega Meganium ex\n-\nMC761-742\n"
                  + FOOT)
SHIPPED = HEAD + ("Liste der versendeten Artikel: 2026/08/03\nSendungsnummer: 111\n\nMega Chandelure ex\nSAR\nM5113-081\n"
                  "Liste der versendeten Artikel: 2026/04/29\nSendungsnummer: 222\n\nOverall Lorcana\n\"SealedBOX\"\n"
                  "Rise of the Floodborn\nFactory-Sealed Box\nDisney Lorcana\n\nNative\n" + FOOT)
IMG = "https://gtchaxonline.com/card/{}_small.jpg?d=1"


def test_parse_coins_kinds_and_amounts():
    events = parse_coins([{"text": COINS}, {"text": COINS}])   # doppelt eingelesene Seite zählt einmal
    assert [e["kind"] for e in events] == ["convert", "open", "convert", "open", "buy", "open"]
    assert events[1]["amount"] == -1000 and events[2]["amount"] == 1320
    assert events[4]["yen"] == 8000 and events[0]["t"] == datetime(2026, 10, 1, 21, 8)


def test_parse_cards_blocks_and_images():
    pending = parse_cards([{"text": PENDING, "images": [IMG.format(1), IMG.format(2), IMG.format(3)]}])
    assert [(c["name"], c["date"], c["card_id"]) for c in pending] == [
        ("【30th】Pikachu ex", "2026-09-28", "1"), ("Buggy", "2026-09-24", "2"),
        ("[SAR Specification] Mega Meganium ex", "2026-09-24", "3")]
    shipped = parse_cards([{"text": SHIPPED, "images": [IMG.format(i) for i in (7, 8, 9)]}])
    assert len(shipped) == 3 and shipped[0]["tracking"] == "111" and shipped[0]["rarity"] == "SAR"
    assert shipped[1]["name"].startswith("Overall Lorcana") and shipped[1]["card_id"] == "8"
    # Seite auf Englisch/Japanisch: Kopfzeilen mit Datum und Sendungsnummer werden genauso erkannt
    en = HEAD + ("Request date: 2026/10/04\n\nMewtwo ex\nSAR\nSV2a-205\n" + FOOT)
    ja = HEAD + ("申請日：2026/10/05\n\nピカチュウex\nSAR\nM1-001\n" + FOOT)
    assert [(c["name"], c["date"]) for c in parse_cards([{"text": en}])] == [("Mewtwo ex", "2026-10-04")]
    assert [(c["name"], c["date"]) for c in parse_cards([{"text": ja}])] == [("ピカチュウex", "2026-10-05")]
    ship = HEAD + ("Shipped: 2026/10/01\nTracking number: 333\n\nCharizard\nSAR\nX-1\n" + FOOT)
    assert parse_cards([{"text": ship}])[0]["tracking"] == "333"
    # Anzahl Bilder passt nicht -> keine falsche Zuordnung
    assert "image" not in parse_cards([{"text": SHIPPED, "images": [IMG.format(7)]}])[0]


def test_parse_cards_chrome_android_without_blank_lines():
    """Chrome auf Android: kein Leerzeichen zwischen den Artikeln, je Artikel Name / Rarität / Nummer."""
    text = (HEAD + "Liste der versendeten Artikel: 2026/02/20\nSendungsnummer: 889\n"
            "Buby\nAR\nSV4K068-066\nMega Sharpedo ex\nSAR\nM2113-080\n"
            "Liste der versendeten Artikel: 2026/02/19\nSendungsnummer: 870\n"
            "Roar of Fury\nFactory-Sealed Box\nDragon Ball\n"
            "Xero Place Co., Ltd.\n〒330-0854\nTEL : +81 00\nMy Page")
    cards = parse_cards([{"text": text, "images": [IMG.format(i) for i in (1, 2, 3)]}])
    assert [(c["name"], c["rarity"], c["number"], c["date"], c["tracking"]) for c in cards] == [
        ("Buby", "AR", "SV4K068-066", "2026-02-20", "889"),
        ("Mega Sharpedo ex", "SAR", "M2113-080", "2026-02-20", "889"),
        ("Roar of Fury", "Factory-Sealed Box", "Dragon Ball", "2026-02-19", "870")]
    assert [c["card_id"] for c in cards] == ["1", "2", "3"]
    pending = HEAD + "Anfragedatum: 2026/09/28\n【30th】Pikachu ex\nSAR\nM6a126-103\nBuggy\nSP\nP-084\n" + FOOT.replace("\n\n", "\n")
    assert [c["name"] for c in parse_cards([{"text": pending}])] == ["【30th】Pikachu ex", "Buggy"]


def test_parse_member_month_spending():
    info = parse_member([{"text": "949\n1\nAusgaben in diesem Monat\n8.000円\nCoin"}])
    assert info == {"coins": 949, "spent_month_yen": 8000}


def test_attribution_unique_only():
    banners = {
        1: {"price": 1000, "values": {660, 5000}, "title": "A"},
        2: {"price": 1000, "values": {700}, "title": "B"},       # Wert 660 gibt es hier nicht
        3: {"price": 500, "values": {660}, "title": "C"},        # Preis teilt 1.000, aber keine Bewegung
    }
    utc = lambda h, m: datetime(2026, 10, 1, h - 9, m, 30)
    moves = {1: [utc(21, 9), utc(21, 6)], 2: [utc(21, 9), utc(21, 6)]}
    data = build_history({"buy-point-history": {"pages": [{"text": COINS}]}}, banners, moves)
    ev = data["events"]
    assert ev[1]["banner"] == 1 and ev[1]["pulls"] == 1    # 1 Zug, Umwandlung 660 nur in Banner 1
    assert ev[0]["banner"] == 1                             # Umwandlung danach zählt zum Banner
    # 2er-Zug: Banner 1 und 2 möglich -> über die Nachbar-Öffnung (gleiche Sitzung) Banner 1
    assert ev[3]["banner"] == 1 and ev[3]["guessed"] and ev[3]["pulls"] == 2
    assert "banner" not in ev[5]                            # keine Bewegung -> nicht zugeordnet
    s = data["summary"]
    assert s["total"]["spent"] == 3010 and s["total"]["returned"] == 1980 and s["total"]["bought_yen"] == 8000
    assert s["unassigned_opens"] == 1
    assert {k: s["banners"][0][k] for k in ("banner", "spent", "pulls", "returned", "balance", "title", "price")} == {
        "banner": 1, "spent": 3000, "pulls": 3, "returned": 1980, "balance": -1020, "title": "A", "price": 1000}


def test_plan_claims_only_when_unique_and_free():
    cards = [{"card_id": "10", "date": "2026-09-28", "name": "Pikachu"},
             {"card_id": "10", "date": "2026-09-28", "name": "Pikachu"},
             {"card_id": "20", "date": "2026-09-28", "name": "In zwei Bannern"},
             {"card_id": "30", "date": "2026-09-01", "name": "Vor Bannerstart"},
             {"card_id": "40", "date": "2026-09-28", "name": "Schon eigene Medaille"}]
    banners = {1: {"card_ids": {"10", "20", "40"}, "created": "2026-09-20"},
               2: {"card_ids": {"20"}, "created": "2026-09-10"},
               3: {"card_ids": {"30"}, "created": "2026-09-15"}}
    units = [{"key": "10#1", "tier": "T1"}, {"key": "10#2", "tier": "T2"}, {"key": "10#3", "tier": "T3"},
             {"key": "40", "tier": "T4"}]
    targets = {1: {"units": units, "medals": {"T1": 555, "T4": 42}}, 3: {"units": [], "medals": {}}}
    planned = plan_claims(cards, banners, targets, "42", done=set())
    assert [(p["pack_id"], p["tier"]) for p in planned] == [(1, "T2"), (1, "T3")]
    assert len({p["key"] for p in planned}) == 2
    # schon erledigt -> nicht noch einmal
    assert plan_claims(cards, banners, targets, "42", done={p["key"] for p in planned}) == []
    # Zeit: nur wenn die Person Banner 1 laut eigenem Münzverlauf kurz vor der Anfrage geöffnet hat
    assert len(plan_claims(cards, banners, targets, "42", set(), opens={1: ["2026-09-27"]})) == 2
    assert plan_claims(cards, banners, targets, "42", set(), opens={1: ["2026-09-01"]}) == []    # zu lange her
    assert plan_claims(cards, banners, targets, "42", set(), opens={1: ["2026-09-29"]}) == []    # erst danach
    assert plan_claims(cards, banners, targets, "42", set(), opens={}) == []                     # nie geöffnet


def test_history_per_gtcha_account_does_not_overwrite(tmp_path):
    async def run():
        bridge = AppBridge(str(tmp_path / "w.db"))
        await bridge.init()
        await bridge.set_history("7", {"shipped": {"items": [{"date": "2026-09-22", "name": "Alt"}], "gap": False}})
        await bridge.adopt_default_history("7", "id:1")            # erstes Übertragen mit Kennung: übernehmen
        assert (await bridge.get_account_history("7", "id:1"))["shipped"]["items"][0]["name"] == "Alt"
        # Konto 2 überträgt vollständig - Konto 1 bleibt erhalten
        await bridge.set_history("7", {"shipped": {"items": [{"date": "2026-10-04", "name": "Neu"}], "gap": False}},
                                 "id:2")
        await bridge.adopt_default_history("7", "id:2")            # ändert nichts mehr
        merged = await bridge.get_history("7")
        assert [c["name"] for c in merged["shipped"]["items"]] == ["Neu", "Alt"]   # beide, neueste zuerst
        assert (await bridge.get_account_history("7", "id:2"))["shipped"]["items"][0]["name"] == "Neu"

    asyncio.run(run())


def test_bridge_latest_sync_and_auto_claims(tmp_path):
    async def run():
        bridge = AppBridge(str(tmp_path / "w.db"))
        await bridge.init()
        user = {"user_id": "42", "name": "Basti"}
        await bridge.add_import(user, "sync", "https://gtchaxonline.com/pending-detail", '{"pages": [1]}')
        await bridge.add_import(user, "sync", "https://gtchaxonline.com/pending-detail", '{"pages": [2]}')
        await bridge.add_import({"user_id": "7", "name": "X"}, "sync", "https://gtchaxonline.com/shipped-detail", "{}")
        areas = await bridge.latest_sync("42")
        assert list(areas) == ["pending-detail"] and areas["pending-detail"]["pages"] == [2]
        await bridge.add_auto_claim(user, "10@2026-09-28#0", 1, "T2")
        assert await bridge.auto_claim_keys("42") == {"10@2026-09-28#0"}
        claims = await bridge.auto_claims("42")
        assert claims[0]["status"] == "pending" and claims[0]["tier"] == "T2"
        assert [r["action"] for r in await bridge.pending()] == ["auto_claim"]

    asyncio.run(run())


def test_merge_newest_first_cuts_overlap():
    key = ("t",)
    old = [{"t": x} for x in "EDCBA"]
    merged, ok = merge_newest_first(old, [{"t": x} for x in "GFED"], key)    # Seite mit E, D am Ende
    assert ok and [e["t"] for e in merged] == list("GFEDCBA")
    merged, ok = merge_newest_first(old, [{"t": x} for x in "ED"], key)      # nichts Neues
    assert ok and [e["t"] for e in merged] == list("EDCBA")
    merged, ok = merge_newest_first(old, [{"t": x} for x in "ZY"], key)      # kein Überlapp -> Lücke
    assert not ok and [e["t"] for e in merged] == list("ZYEDCBA")
    # ohne Zeitstempel (z. B. Karten): zählen statt Reihenfolge - andere Reihenfolge, nichts Neues, keine Doppelten
    key = ("x",)
    old = [{"x": x} for x in "DCCBA"]
    merged, ok = merge_newest_first(old, [{"x": x} for x in "CDC"], key)
    assert ok and [e["x"] for e in merged] == list("DCCBA")
    merged, ok = merge_newest_first(old, [{"x": x} for x in "ECDCC"], key)    # E neu, ein drittes C neu
    assert ok and [e["x"] for e in merged] == list("ECDCCBA")


def test_ingest_incremental_coins_and_member():
    page = lambda rows: {"text": HEAD + "Datum\n" + "\n".join(
        f"2026/10/0{d} 12:00\n-1.000\n¥0\nÖffnen" for d in rows) + "\n" + FOOT}
    stored = ingest({}, [{"path": "buy-point-history", "pages": [page("54"), page("321")]},
                         {"path": "change-member", "pages": [{"text": "949\nAusgaben in diesem Monat\n8.000円"}]}])
    assert len(stored["coins"]["items"]) == 5 and stored["member"]["info"]["spent_month_yen"] == 8000
    # nur Neues: Seite 1 enthält schon den alten neuesten Eintrag (5)
    changed = ingest(stored, [{"path": "buy-point-history", "pages": [page("7654")], "partial": True},
                              {"path": "change-member", "pages": [{"text": "1.200\nCoin"}]}])
    assert [e["t"][:10] for e in changed["coins"]["items"]] == [
        "2026-10-07", "2026-10-06", "2026-10-05", "2026-10-04", "2026-10-03", "2026-10-02", "2026-10-01"]
    assert changed["coins"]["gap"] is False
    info = changed["member"]["info"]
    assert info["coins"] == 1200 and info["spent_month_yen"] == 8000   # alter ¥-Wert bleibt
    # vollständiger Lauf ersetzt
    assert len(ingest(stored, [{"path": "buy-point-history", "pages": [page("9")]}])["coins"]["items"]) == 1


def test_profile_rank_and_monthly_charge():
    rank_page = {"text": "949\nMitgliedschaftsrang\nBis zum nächsten Rang 39.999Coin\nWeiß\n*Rangbasierte Boni"}
    member = {"text": "949\nAusgaben in diesem Monat\n8.000円"}
    oct_ = datetime(2026, 10, 2, 3, 0)
    stored = ingest({}, [{"path": "pending-detail", "pages": [rank_page]},
                         {"path": "change-member", "pages": [member]}], now=oct_)
    assert stored["member"]["info"]["rank"] == "white"
    assert profile(stored, now=oct_)["charge_yen"] == 8000 and profile(stored, now=oct_)["rank"] == "white"
    assert profile(stored, now=oct_)["charge"] is None          # ohne Münzverlauf keine Coins-Angabe
    # Aufladung in Coins = diesen Monat gekaufte Coins (COINS: 10.000 am 01.10. für ¥8.000)
    stored.update(ingest(stored, [{"path": "buy-point-history", "pages": [{"text": COINS}]}], now=oct_))
    assert profile(stored, now=oct_)["charge"] == 10000
    # neuer Monat (JST): Aufladung zählt nicht mehr
    nov = datetime(2026, 10, 31, 15, 30)
    assert profile(stored, now=nov)["charge"] == 0 and profile(stored, now=nov)["charge_yen"] == 0
    # Rang bleibt erhalten, wenn ein späterer Lauf keinen liefert
    later = ingest(stored, [{"path": "change-member", "pages": [{"text": "1.000\nCoin"}]}], now=oct_)
    assert later["member"]["info"]["rank"] == "white" and later["member"]["info"]["coins"] == 1000
    assert profile({})["rank"] is None and profile({})["charge"] is None


def test_attribution_prefers_near_window():
    """Pack-Zahlen kommen alle 15 s: eine Bewegung 1 Min danach schlägt eine 5 Min danach."""
    from webapp.history import attribute_opens
    banners = {1: {"price": 1000, "values": set()}, 2: {"price": 1000, "values": set()}}
    t = datetime(2026, 10, 1, 21, 7)
    utc = t - __import__("datetime").timedelta(hours=9)
    moves = {1: [utc.replace(minute=8)], 2: [utc.replace(minute=12)]}
    ev = [{"t": t, "amount": -1000, "kind": "open", "note": ""}]
    attribute_opens(ev, banners, moves)
    assert ev[0]["banner"] == 1
    # nur der 5-Minuten-Scrape: weites Fenster als Rückfall
    ev = [{"t": t, "amount": -1000, "kind": "open", "note": ""}]
    attribute_opens(ev, banners, {2: [utc.replace(minute=12)]})
    assert ev[0]["banner"] == 2


def test_archive_keeps_ended_banners_for_history(tmp_path):
    import json as _json

    from database.db import Database

    async def run():
        db = Database(str(tmp_path / "b.db"))
        await db.init()
        import aiosqlite
        pool = {"cards": [{"id": 7, "name": "A", "value": 660, "copies": 3}], "hits": [{"id": 9, "name": "H", "value": 9000}]}
        async with aiosqlite.connect(db.db_path) as conn:
            await conn.execute("INSERT INTO banners (pack_id, title, best_hit, price_coins, is_active, created_at, updated_at, "
                               "card_pool) VALUES (5, NULL, 'Glurak', 1000, 0, '2026-09-01', '2000-01-01', ?)",
                               (_json.dumps(pool),))
            await conn.execute("INSERT INTO pack_history (banner_id, old_count, new_count, changed_at) "
                               "VALUES (5, 10, 9, ?), (5, 9, 8, '2000-01-01')", (datetime.now().isoformat(),))
            await conn.commit()
        assert await db.purge_archived_data(max_age_hours=1) == 1
        assert await db.purge_old_history() == 1            # nur die uralte Bewegung
        from webapp.view import BannerView
        banners, moves = await BannerView(db).history_context(datetime(2026, 1, 1))
        assert banners[5]["title"] == "Glurak" and banners[5]["values"] == {660} and banners[5]["card_ids"] == {"7", "9"}
        assert not banners[5]["active"] and len(moves[5]) == 1

    asyncio.run(run())


def test_real_balance_with_hits_luck_and_months():
    from webapp.history import build_from_stored
    page = {"text": COINS}
    stored = ingest({}, [{"path": "buy-point-history", "pages": [page]},
                         {"path": "pending-detail", "pages": [{"text": PENDING, "images": [IMG.format(1), IMG.format(2), IMG.format(3)]}]}])
    # Banner 1: Ø 800 je Pack, enthält Karte 1 (Pikachu, 45.000) - Anfragedatum 28.09. liegt aber vor dem Münzverlauf
    # (ab 30.09.), Karte 2 (Buggy) ist in keinem Banner -> zählt nicht
    banners = {1: {"price": 1000, "values": {660}, "title": "A", "created": "2026-09-01", "avg": 800,
                   "card_ids": {"1"}, "card_values": {"1": 45000}}}
    utc = lambda h, m: datetime(2026, 10, 1, h - 9, m, 30)
    h = build_from_stored(stored, banners, {1: [utc(21, 6), utc(21, 9)]})
    t = h["summary"]["total"]
    assert t["hits"] == 0 and t["balance_with_hits"] == t["balance"]
    assert h["pending"][0]["value"] == 45000 and h["pending"][1]["value"] is None
    # gleiche Karte, aber angefordert am 01.10. (im Münzverlauf) -> zählt als Hit von Banner 1
    stored["pending"]["items"][0]["date"] = "2026-10-01"
    h = build_from_stored(stored, banners, {1: [utc(21, 6), utc(21, 9)]})
    t, row = h["summary"]["total"], h["summary"]["banners"][0]
    assert t["hits"] == 1 and t["balance_with_hits"] == t["balance"] + 45000
    assert row["hits_value"] == 45000 and row["luck_pct"] == round((row["returned"] + 45000) / (row["pulls"] * 800) * 100)
    m = {x["month"]: x for x in h["summary"]["months"]}
    assert m["2026-10"]["bought_yen"] == 8000 and m["2026-09"]["spent"] == 10 and m["2026-10"]["opens"] == 2


def test_import_result_page_is_standalone():
    from webapp.server import import_result_page
    res = import_result_page(["✅ 8 Bereiche", "<script>x</script>"])
    assert res.content_type == "text/html" and res.headers["Cache-Control"] == "no-store"
    assert "✅ 8 Bereiche" in res.text and "<script>x" not in res.text and "/static/" not in res.text


def test_local_time_is_german_time():
    from webapp.history import local_time
    assert local_time("2026-10-02T19:51:12.123456") == "2026-10-02 21:51"     # Sommerzeit UTC+2
    assert local_time("2026-12-02T19:51:00") == "2026-12-02 20:51"            # Winterzeit UTC+1
    assert local_time(None) is None


def test_failed_area_keeps_stored_history(tmp_path):
    import json as _json

    from aiohttp.test_utils import TestClient, TestServer

    from database.db import Database
    from webapp.server import App, make_app

    async def run():
        await Database(str(tmp_path / "gtcha_bot.db")).init()
        app = App(str(tmp_path / "gtcha_bot.db"), str(tmp_path), "x")
        await app.bridge.init()
        code = await app.bridge.create_code(42, "Basti")
        token = (await app.bridge.redeem_code(code))["token"]
        web_app = make_app(app)
        web_app.on_startup.clear()
        web_app.on_cleanup.clear()
        client = TestClient(TestServer(web_app))
        await client.start_server()
        post = lambda pages: client.post("/api/import-form", data={"d": _json.dumps({"t": token, "ms": 7400, "pages": pages})})
        res = await post([{"path": "buy-point-history", "pages": [{"text": COINS}]}])
        assert "in 7 Sekunden" in await res.text()
        res = await post([{"path": "buy-point-history", "error": "kein Zugriff"},
                          {"path": "pending-detail", "pages": [{"text": PENDING}]}])
        text = await res.text()
        assert "Nicht geladen: Münzen" in text and "kein Zugriff" in text
        stored = await app.bridge.get_history("42")
        assert len(stored["coins"]["items"]) == 6 and len(stored["pending"]["items"]) == 3
        # neues Layout: Kartenbilder auf der Seite, aber keine Karte erkannt -> gespeicherte Karten bleiben
        res = await post([{"path": "pending-detail", "pages": [{"text": HEAD + "Neues Layout\n" + FOOT,
                                                               "images": [IMG.format(1)]}]}])
        assert "keine Karten erkannt" in await res.text()
        assert len((await app.bridge.get_history("42"))["pending"]["items"]) == 3
        # kompletter Lauf mit viel weniger Buchungen (nur Seite 1): gespeicherter Verlauf bleibt
        many = HEAD + "\n".join(f"2026/09/{d:02d} 10:{m:02d}\n-100\n¥0\nÖffnen" for d in range(1, 29) for m in (1, 2)) + "\n" + FOOT
        await post([{"path": "buy-point-history", "pages": [{"text": many}]}])
        assert len((await app.bridge.get_history("42"))["coins"]["items"]) == 56
        res = await post([{"path": "buy-point-history", "pages": [{"text": COINS}]}])
        assert "statt bisher 56" in await res.text()
        assert len((await app.bridge.get_history("42"))["coins"]["items"]) == 56
        # wirklich leer (keine Kartenbilder): darf leer werden (alles verschickt)
        await post([{"path": "pending-detail", "pages": [{"text": HEAD + "Keine Daten\n" + FOOT}]}])
        assert (await app.bridge.get_history("42")).get("pending", {}).get("items") in ([], None)
        await client.close()

    asyncio.run(run())


def test_bookmarklet_version_matches_app():
    import re as _re
    from pathlib import Path

    from webapp.server import BOOKMARKLET_VERSION
    js = (Path(__file__).parent.parent / "webapp" / "static" / "app.js").read_text()
    assert int(_re.search(r"const SYNC_VERSION = (\d+);", js).group(1)) == BOOKMARKLET_VERSION


def test_paypal_purchase_and_missing_note():
    """Käufe heißen je nach Zahlungsweg anders (z. B. "PayPal"); der Hinweis ist optional und darf die
    nächste Buchung nicht verschlucken."""
    text = HEAD + "Datum\n" + "\n".join([
        "2026/10/02 10:00", "50.000", "¥45.000", "PayPal",
        "2026/10/01 09:00", "1.000", "¥0",                      # ohne Hinweis
        "2026/10/01 08:00", "-1.000", "¥0", "Öffnen",
        "2026/10/01 07:00", "300", "¥0", "Behalte deinen Rang",   # Gratis-Coins
    ]) + "\n" + FOOT
    ev = parse_coins([{"text": text}])
    assert [(e["kind"], e["note"]) for e in ev] == [("buy", "PayPal"), ("other", ""), ("open", "Öffnen"),
                                                     ("other", "Behalte deinen Rang")]
    stored = ingest({}, [{"path": "buy-point-history", "pages": [{"text": text}]}])
    assert profile(stored, now=datetime(2026, 10, 2, 3, 0))["charge"] == 50000
    # schon gespeicherte, früher falsch eingeordnete Buchung wird beim Auswerten korrigiert
    stored["coins"]["items"][0]["kind"] = "other"
    from webapp.history import build_from_stored
    assert build_from_stored(stored, {}, {})["summary"]["total"]["bought_yen"] == 45000


def test_parse_card_tiles_new_layout():
    """Neues Layout (Oktober 2026): Kartenknöpfe mit Namen im aria-label, Datum in der Gruppen-Kopfzeile."""
    src = "https://gtchaxonline.com/card/{}_small.jpg?d=2026101105"
    pending = [{"text": "Datum der Versandanfrage\n03.10.2026\n1 Karte", "cards": [
        {"src": src.format(6004610), "alt": "[Full Art Relief Specification] Sabo (Hatori Kyoka) ",
         "label": "[Full Art Relief Specification] Sabo (Hatori Kyoka) ", "group": "Datum der Versandanfrage 03.10.2026 1 Karte"},
        {"src": src.format(1034580), "alt": "【30th】Pikachu ex", "label": "【30th】Pikachu ex",
         "group": "Datum der Versandanfrage 28.09.2026 1 Karte"}]}]
    cards = parse_cards(pending)
    assert [(c["name"], c["date"], c["card_id"]) for c in cards] == [
        ("[Full Art Relief Specification] Sabo (Hatori Kyoka)", "2026-10-03", "6004610"),
        ("【30th】Pikachu ex", "2026-09-28", "1034580")]
    # Sammlung: TIER-Vitrine überspringen; gibt es eine Ansicht mit Gruppen (Sendungen), zählt nur die
    collection = {"text": "Meine Sammlung", "cards": [
        {"src": src.format(1034417), "alt": "", "label": "TIER 1", "showcase": True},
        {"src": src.format(1034417), "alt": "Mega Chandelure ex", "label": "Mega Chandelure ex"},
        {"src": src.format(1001662), "alt": "[25th] Mewtwo EX", "label": "[25th] Mewtwo EX"}]}
    assert [c["name"] for c in parse_cards([collection])] == ["Mega Chandelure ex", "[25th] Mewtwo EX"]
    shipments = {"text": "Sendungen", "view": "sendungen", "cards": [
        {"src": src.format(1034417), "label": "Mega Chandelure ex", "group": "Versanddatum 2026/02/20 Sendungsnummer: 8895751"}]}
    assert [(c["name"], c["date"], c["tracking"]) for c in parse_cards([collection, shipments])] == [
        ("Mega Chandelure ex", "2026-02-20", "8895751")]
    # altes Layout ohne Kartenknöpfe: wie bisher über den Seitentext
    assert parse_cards([{"text": SHIPPED}])[0]["tracking"] == "111"


def test_parse_coins_new_date_and_amount_format():
    """Neues Layout: deutsches Datum und Beträge mit Vorzeichen/Einheit werden genauso gelesen."""
    text = HEAD + "\n".join(["11.10.2026 10:29", "+1.320 Coins", "¥0", "In Münzen umwandeln",
                             "11.10.2026 10:20", "−1.000", "¥0", "Öffnen",
                             "01.10.2026 09:00:15", "10.000", "¥8.000", "Münzen erhalten"]) + "\n" + FOOT
    events = parse_coins([{"text": text}])
    assert [(e["t"].strftime("%d.%m. %H:%M"), e["amount"], e["kind"]) for e in events] == [
        ("11.10. 10:29", 1320, "convert"), ("11.10. 10:20", -1000, "open"), ("01.10. 09:00", 10000, "buy")]
    assert parse_coins([{"text": COINS}])[1]["amount"] == -1000      # altes Format wie bisher


def test_parse_coins_new_grouped_layout():
    """Coin-Verlauf seit dem Umbau (Screenshot 11.10.2026): Datumszeile, darunter Text / Uhrzeit / Betrag."""
    text = ("HISTORY\nTransaktionen\nGacha\nCoins\nTicket\n11.10.2026\nIn Coins umwandeln\n00:56\n+100\n"
            "10.10.2026\nIn Coins umwandeln\n00:03\n+300\n09.10.2026\nIn Coins umwandeln\n04:30\n+10\n"
            "Kaufen\n04:30\n−88\nCoins kaufen\n04:00\n+10.000\n¥8.000\nStart\nShop")
    events = parse_coins([{"text": text}])
    assert [(e["t"].strftime("%d.%m. %H:%M"), e["amount"], e["kind"]) for e in events] == [
        ("11.10. 00:56", 100, "convert"), ("10.10. 00:03", 300, "convert"), ("09.10. 04:30", 10, "convert"),
        ("09.10. 04:30", -88, "open"), ("09.10. 04:00", 10000, "buy")]
    assert events[-1]["yen"] == 8000


def test_parse_coins_grouped_counts_repeated_list_once():
    """Dieselbe Liste mehrfach eingelesen (Nachladen beim Scrollen): jede Buchung nur einmal; zwei gleiche
    Buchungen in derselben Minute (zwei Packs gezogen) bleiben zwei."""
    first = "11.10.2026\nKaufen\n04:30\n−88\nKaufen\n04:30\n−88\nIn Coins umwandeln\n04:29\n+10"
    more = first + "\n10.10.2026\nIn Coins umwandeln\n00:03\n+300"
    events = parse_coins([{"text": first}, {"text": more}, {"text": more}])
    assert [(e["t"].strftime("%d.%m. %H:%M"), e["amount"]) for e in events] == [
        ("11.10. 04:30", -88), ("11.10. 04:30", -88), ("11.10. 04:29", 10), ("10.10. 00:03", 300)]


def test_parse_card_tiles_page_transition():
    """Beim Umblättern zeigt die Seite alte und neue Karten zugleich: Reste ohne Sendung (Sammlung) zählen nicht,
    eine Sendung auf zwei eingelesenen Seiten nur einmal; zwei gleiche Karten in einer Sendung bleiben zwei."""
    src = "https://gtchaxonline.com/card/{}_small.jpg"
    g1 = "Versendet 01.08.2026 Sendungsnummer: 876350975697 Sendung verfolgen"
    g2 = "Versendet 29.07.2026 Sendungsnummer: 876350975697 Sendung verfolgen"
    page1 = {"view": "sendungen", "cards": [
        {"src": src.format(1), "label": "Rest aus der Sammlung", "group": ""},
        {"src": src.format(2), "label": "Yamato", "group": g1},
        {"src": src.format(3), "label": "Booster", "group": g1}, {"src": src.format(3), "label": "Booster", "group": g1},
        {"src": src.format(4), "label": "Storm Emeralda", "group": g2}]}
    page2 = {"cards": [{"src": src.format(4), "label": "Storm Emeralda", "group": g2},
                       {"src": src.format(5), "label": "Mew", "group": g2}]}
    cards = parse_card_tiles([page1, page2])
    assert [(c["name"], c["date"]) for c in cards] == [
        ("Yamato", "2026-08-01"), ("Booster", "2026-08-01"), ("Booster", "2026-08-01"),
        ("Storm Emeralda", "2026-07-29"), ("Mew", "2026-07-29")]
    assert cards[0]["tracking"] == "876350975697"


def test_merge_coins_replaces_covered_time_span():
    """Münzverlauf: im Zeitraum der neuen Seite gilt nur sie - früher entstandene Doppelte verschwinden, ältere
    Buchungen bleiben; in der ältesten Minute der Seite (evtl. abgeschnitten) bleibt die größere Anzahl."""
    key = ("t", "amount", "note")
    e = lambda t, a: {"t": t, "amount": a, "note": "Kaufen"}
    old = [e("2026-10-11 14:10", -88), e("2026-10-11 14:10", -88), e("2026-10-11 14:10", -88),   # 1 Doppelter
           e("2026-10-11 14:05", -88), e("2026-10-11 14:05", -88), e("2026-10-10 09:00", 100)]
    new = [e("2026-10-11 14:12", -88), e("2026-10-11 14:10", -88), e("2026-10-11 14:10", -88), e("2026-10-11 14:05", -88)]
    merged, ok = merge_newest_first(old, new, key)
    assert ok and [(x["t"][-5:], x["amount"]) for x in merged] == [
        ("14:12", -88), ("14:10", -88), ("14:10", -88), ("14:05", -88), ("14:05", -88), ("09:00", 100)]


def test_diagnose_only_for_admins(tmp_path, monkeypatch):
    """Diagnose-Lesezeichen: nur Admins; Ergebnis landet in data/api_diag.txt (Discord ohne Zugang übersprungen)."""
    import json as _json

    from aiohttp.test_utils import TestClient, TestServer

    from database.db import Database
    from webapp.server import App, make_app
    monkeypatch.delenv("DISCORD_TOKEN", raising=False)

    async def run():
        await Database(str(tmp_path / "gtcha_bot.db")).init()
        app = App(str(tmp_path / "gtcha_bot.db"), str(tmp_path), "x")
        await app.bridge.init()
        token = (await app.bridge.redeem_code(await app.bridge.create_code(42, "Basti")))["token"]
        web_app = make_app(app)
        web_app.on_startup.clear()
        web_app.on_cleanup.clear()
        client = TestClient(TestServer(web_app))
        await client.start_server()
        try:
            body = {"t": token, "areas": {"history/shipped/1": {"status": 200, "shape": {"list": [{"id": "int"}]}}}}
            monkeypatch.setenv("APP_ADMIN_IDS", "7")
            res = await client.post("/api/import-diag", data={"d": _json.dumps(body)})
            assert "nur für Admins" in await res.text() and not (tmp_path / "api_diag.txt").exists()
            monkeypatch.setenv("APP_ADMIN_IDS", "42")
            res = await client.post("/api/import-diag", data={"d": _json.dumps(body)})
            assert "1 Bereiche" in await res.text()
            assert "history/shipped/1" in (tmp_path / "api_diag.txt").read_text()
        finally:
            await client.close()

    asyncio.run(run())


# --- ab Lesezeichen v19: Daten, die die Seiten selbst laden ---
SHIPPED_JSON = {"page": [], "state": 200, "list": {
    "2026/08/03_876408943256": {"delivery_code": "876408943256", "date": "2026/08/03", "url": "x", "cards": [
        {"id": 1034580, "name": "Pikachu ex", "model_number": "SV-P 001", "rarity": "SAR", "num": 2,
         "image": "/card/1034580_small.jpg"}]},
    "2026/04/14_Narita": {"delivery_code": "Narita", "date": "2026/04/14", "url": "", "cards": [
        {"id": 7, "name": "Nami", "model_number": "OP01-016", "rarity": "SR", "num": 1, "image": "https://x/7.jpg"}]}}}
PENDING_JSON = {"page": [], "state": 200, "list": {"2026/10/03": [
    {"id": 9, "name": "Luffy", "model_number": "OP16-015", "rarity": "SR", "num": 1, "image": "/card/9.jpg"}]}}


def test_cards_from_site_json():
    from webapp.history import cards_from_json
    cards = cards_from_json([SHIPPED_JSON, SHIPPED_JSON])   # doppelt mitgelesen: nur einmal
    assert [(c["date"], c["tracking"], c["name"], c["number"]) for c in cards] == [
        ("2026-08-03", "876408943256", "Pikachu ex", "SV-P 001"), ("2026-08-03", "876408943256", "Pikachu ex", "SV-P 001"),
        ("2026-04-14", "Narita", "Nami", "OP01-016")]
    assert cards[0]["image"] == "https://gtchaxonline.com/card/1034580_small.jpg" and cards[0]["card_id"] == "1034580"
    pending = cards_from_json([PENDING_JSON])
    assert [(c["date"], c["tracking"], c["name"]) for c in pending] == [("2026-10-03", None, "Luffy")]


def test_coins_from_site_json_keeps_same_minute_bookings():
    """Gleiche Buchungen in derselben Minute (mehrere Packs) bleiben getrennt - sie haben eigene IDs."""
    from webapp.history import coins_from_json, coins_total, ingest
    page1 = {"page": {"all": 4, "all_page": 2}, "list": [
        {"id": 13, "bonusPoint": 500, "money": 0, "memo": "コインへ還元", "created": "2026/10/12 01:46"},
        {"id": 12, "bonusPoint": -88, "money": 0, "memo": "ガチャ", "created": "2026/10/12 01:40"},
        {"id": 11, "bonusPoint": -88, "money": 0, "memo": "ガチャ", "created": "2026/10/12 01:40"}]}
    page2 = {"page": {"all": 4}, "list": [
        {"id": 10, "bonusPoint": 10000, "money": 10000, "memo": "コイン購入", "created": "2026/10/11 23:00"}]}
    coins = coins_from_json([page1, page2, page1])
    assert [(c["id"], c["t"], c["amount"], c["kind"]) for c in coins] == [
        (13, "2026-10-12 01:46", 500, "convert"), (12, "2026-10-12 01:40", -88, "open"),
        (11, "2026-10-12 01:40", -88, "open"), (10, "2026-10-11 23:00", 10000, "buy")]
    assert coins_total([page1]) == 4
    stored = ingest({}, [{"path": "buy-point-history", "pages": [{"text": ""}], "json": [page1, page2]}])
    assert len(stored["coins"]["items"]) == 4
    # danach nur Neues: neue Buchung oben, Rest per ID erkannt
    page0 = {"list": [{"id": 14, "bonusPoint": -88, "money": 0, "memo": "ガチャ", "created": "2026/10/12 02:00"}] + page1["list"]}
    again = ingest(stored, [{"path": "buy-point-history", "pages": [{"text": ""}], "json": [page0], "partial": True}])
    assert [c["id"] for c in again["coins"]["items"]] == [14, 13, 12, 11, 10] and not again["coins"]["gap"]


DETAIL = {"hid": 1136902, "id": "1136902", "pack_name": "成田　全員", "created": "2026/10/12 05:10", "reduction_point": 50,
          "cards": [], "send_card": [{"card_id": 1, "name": "Hit &amp; Co", "num": 1, "buy_point": 98780, "image": "/card/1.jpg"}],
          "reduction_card": [{"card_id": 2, "name": "10 coin", "num": 5, "buy_point": 10, "rarity_label": "C"}],
          "delivery_code": "", "status": "1"}


def test_pulls_from_purchase_details():
    from webapp.history import ingest, merge_pulls, pulls_from_details
    pulls = pulls_from_details([DETAIL, DETAIL])
    assert len(pulls) == 1 and pulls[0]["t"] == "2026-10-12 05:10" and pulls[0]["coins_back"] == 50
    assert [(c["name"], c["state"], c["num"], c["value"]) for c in pulls[0]["cards"]] == [
        ("Hit & Co", "angefordert", 1, 98780), ("10 coin", "umgewandelt", 5, 10)]
    assert pulls[0]["cards"][0]["image"] == "https://gtchaxonline.com/card/1.jpg"
    older = {**DETAIL, "hid": 5, "created": "2026/10/01 10:00"}
    assert [p["hid"] for p in merge_pulls(pulls, pulls_from_details([older]))] == [1136902, 5]
    changed = ingest({}, [{"path": "buy-point-history", "pages": [{"text": ""}], "json": [], "details": [DETAIL]}])
    assert changed["pulls"]["items"][0]["hid"] == 1136902


def test_my_pulls_per_banner(tmp_path):
    from aiohttp.test_utils import TestClient, TestServer

    from database.db import Database
    from scraper.models import ScrapedBanner
    from utils.card_pool import summarize_cards
    from webapp.history import pulls_from_details
    from webapp.server import App, make_app

    async def run():
        db = Database(str(tmp_path / "gtcha_bot.db"))
        await db.init()
        await db.save_banner(ScrapedBanner(pack_id=24247, category="MIX", price_coins=10, current_packs=9, total_packs=10))
        await db.save_card_pool(24247, summarize_cards([
            {"id": 1, "name": "Hit", "buy_point": 98780, "duplication": 1, "action_type": 2},
            {"id": 2, "name": "10 coin", "buy_point": 10, "duplication": 9, "action_type": 1}]))
        await db.set_start(24247, int(datetime(2026, 10, 1).timestamp()), announced=True)   # lief beim Kauf schon
        app = App(str(tmp_path / "gtcha_bot.db"), str(tmp_path), "x")
        await app.bridge.init()
        token = (await app.bridge.redeem_code(await app.bridge.create_code(42, "Basti")))["token"]
        await app.bridge.set_history("42", {"pulls": {"items": pulls_from_details([DETAIL]), "gap": False}})
        web_app = make_app(app)
        web_app.on_startup.clear()
        web_app.on_cleanup.clear()
        client = TestClient(TestServer(web_app))
        await client.start_server()
        try:
            h = {"X-Device-Token": token}
            data = await (await client.get("/api/me/pulls?banner=24247", headers=h)).json()
            assert (data["purchases"], data["cards"], data["converted"], data["requested"], data["coins_back"]) == (1, 6, 5, 1, 50)
            other = await (await client.get("/api/me/pulls?banner=1", headers=h)).json()
            assert other["purchases"] == 0
        finally:
            await client.close()

    asyncio.run(run())


def test_shipped_card_without_pool_shows_site_value():
    from webapp.history import build_from_stored
    card = {"date": "2026-08-03", "tracking": "1", "name": "Mega Chandelure ex", "number": "M5113-081",
            "card_id": "999", "price": 45980}
    out = build_from_stored({"shipped": {"items": [card]}}, {}, {})
    assert out["shipped"][0]["value"] is None and out["shipped"][0]["site_value"] == 45980


def test_shipped_card_gets_coin_value_from_purchase_detail():
    """Versendete Karte mit history_id: Coin-Wert beim Ziehen aus den Kauf-Details (auch ohne Kartenliste)."""
    from webapp.history import build_from_stored, cards_from_json, pulls_from_details
    ship = {"list": {"2026/08/03_1": {"delivery_code": "1", "date": "2026/08/03", "cards": [
        {"id": 77, "name": "Mega Chandelure ex", "num": 1, "history_id": 900, "stock_sale_price": 40000}]}}}
    cards = cards_from_json([ship])
    assert cards[0]["hid"] == 900
    det = {"hid": 900, "created": "2026/08/01 10:00", "pack_name": "x", "reduction_point": 0,
           "send_card": [{"card_id": 77, "name": "Mega Chandelure ex", "num": 1, "buy_point": 45980}]}
    out = build_from_stored({"shipped": {"items": cards}, "pulls": {"items": pulls_from_details([det])}}, {}, {})
    assert out["shipped"][0]["value"] == 45980 and out["shipped"][0]["site_value"] is None


def test_old_card_with_site_value_counts_in_balance():
    """Alte Karte ohne Kauf-Details und ohne Kartenliste: Wert laut Seite zählt in der Bilanz mit."""
    from webapp.history import add_hits
    summary = {"since": "2026-01-01", "banners": [], "total": {"balance": -1000}}
    add_hits(summary, [{"date": "2026-05-01", "card_id": "9", "price": 45980}], {})
    assert summary["total"]["hits_value"] == 45980 and summary["total"]["balance_with_hits"] == 44980


def test_plan_pull_claims_from_purchase_details():
    """Kauf-Details, streng: nur angeforderte/offene Karten und nur freie Plätze; fremde Medaillen nie überschreiben;
    Banner nur, wenn genau einer passt; umgewandelte (Coin-)Karten nie als Medaille."""
    from webapp.history import plan_pull_claims, pull_banner
    units = [{"key": "1", "tier": "T1"}, {"key": "2", "tier": "T2"}]
    pull = {"hid": 7, "t": "2026-10-12 05:10", "cards": [{"card_id": "1", "num": 1, "state": "angefordert"},
                                                         {"card_id": "9", "num": 3, "state": "umgewandelt"}]}
    cmap = {"1": [(100, 1000), (200, 1_900_000_000)], "9": [(100, 1000), (200, 1_900_000_000)]}
    assert pull_banner(pull, cmap) == 100          # 200 startete erst nach dem Kauf
    free = plan_pull_claims([pull], cmap, {100: {"units": units, "medals": {}}}, "42", set())
    assert [(p["tier"], p["replace"], p["confirm"]) for p in free] == [("T1", False, False)]   # Karte 9 (umgewandelt) zählt nicht
    taken = plan_pull_claims([pull], cmap, {100: {"units": units, "medals": {"T1": 7}}}, "42", set())
    assert taken == []                             # fremde Medaille wird nicht überschrieben
    mine = plan_pull_claims([pull], cmap, {100: {"units": units, "medals": {"T1": "42"}}}, "42", set())
    assert mine[0]["confirm"] and not plan_pull_claims([pull], cmap, {100: {"units": units, "medals": {}}}, "42",
                                                       {"pull:7:1:0"})
    # zwei Banner liefen beim Kauf -> nicht eindeutig -> gar nichts
    cmap2 = {"1": [(100, 1000), (300, 2000)], "9": [(100, 1000), (300, 2000)]}
    assert pull_banner(pull, cmap2) is None
    assert plan_pull_claims([pull], cmap2, {100: {"units": units, "medals": {}}}, "42", set()) == []


def test_outdated_bookmarklet_flag(tmp_path):
    import json as _json

    from aiohttp.test_utils import TestClient, TestServer

    from database.db import Database
    from webapp.server import BOOKMARKLET_VERSION, App, make_app

    async def run():
        await Database(str(tmp_path / "gtcha_bot.db")).init()
        app = App(str(tmp_path / "gtcha_bot.db"), str(tmp_path), "x")
        await app.bridge.init()
        token = (await app.bridge.redeem_code(await app.bridge.create_code(42, "Basti")))["token"]
        web_app = make_app(app)
        web_app.on_startup.clear()
        web_app.on_cleanup.clear()
        client = TestClient(TestServer(web_app))
        await client.start_server()
        try:
            h = {"X-Device-Token": token}
            assert not (await (await client.get("/api/me", headers=h)).json())["bookmarklet_outdated"]   # nie benutzt
            await client.post("/api/import-form", data={"d": _json.dumps({"t": token, "v": 3, "pages": []})})
            assert (await (await client.get("/api/me", headers=h)).json())["bookmarklet_outdated"]
            await client.post("/api/import-form", data={"d": _json.dumps({"t": token, "v": BOOKMARKLET_VERSION, "pages": []})})
            assert not (await (await client.get("/api/me", headers=h)).json())["bookmarklet_outdated"]
        finally:
            await client.close()

    asyncio.run(run())


def test_admin_forums(tmp_path, monkeypatch):
    from aiohttp.test_utils import TestClient, TestServer

    from database.db import Database
    from webapp.server import App, make_app
    monkeypatch.setenv("APP_ADMIN_IDS", "42")
    monkeypatch.setenv("CHANNEL_MIX", "111111111111111111")

    async def run():
        await Database(str(tmp_path / "gtcha_bot.db")).init()
        app = App(str(tmp_path / "gtcha_bot.db"), str(tmp_path), "x")
        await app.bridge.init()
        token = (await app.bridge.redeem_code(await app.bridge.create_code(42, "Basti")))["token"]
        web_app = make_app(app)
        web_app.on_startup.clear()
        web_app.on_cleanup.clear()
        client = TestClient(TestServer(web_app))
        await client.start_server()
        try:
            h = {"X-Device-Token": token}
            res = await client.post("/api/admin/forums", headers=h,
                                    json={"forums": {"Yu-Gi-Oh!": {"main": "222222222222222222", "premium": ""}}})
            forums = {f["name"]: f for f in (await res.json())["forums"]}
            assert forums["Yu-Gi-Oh!"]["main"] == "222222222222222222" and forums["MIX"]["main"] == "111111111111111111"
            assert (await client.post("/api/admin/forums", headers=h, json={"forums": {"X": {"main": "abc"}}})).status == 400
            assert await app.bridge.forum_ids() == {"Yu-Gi-Oh!": {"main": "222222222222222222"}}
        finally:
            await client.close()

    asyncio.run(run())


@pytest.mark.no_legacy
def test_legacy_text_off_keeps_stored_history():
    """Alte Text-Wege abgeschaltet: Lauf ohne Daten der Seite (altes Lesezeichen) ändert den gespeicherten Stand nicht."""
    from webapp.history import ingest
    stored = {"coins": {"items": [{"t": "2026-10-01 10:00", "amount": -88, "note": "Kaufen", "id": 1}]}}
    changed = ingest(stored, [{"path": "buy-point-history", "pages": [{"text": "11.10.2026\nKaufen\n10:00\n−88"}]},
                              {"path": "pending-detail", "pages": [{"text": "x"}]}])
    assert "coins" not in changed or changed["coins"]["items"] == stored["coins"]["items"]
    assert "pending" not in changed


def test_pull_from_older_banner_not_assigned_to_newer():
    """Karte aus einem Kauf im März steckt auch im neuen Banner vom Oktober: nicht diesem Banner zuordnen."""
    from datetime import datetime as _dt

    from webapp.history import pull_banner
    october = int(_dt(2026, 10, 10).timestamp())
    pull = {"hid": 1, "t": "2026-03-23 02:31", "cards": [{"card_id": "5"}]}
    assert pull_banner(pull, {"5": [(24238, october)]}) is None
    assert pull_banner({**pull, "t": "2026-10-11 10:00"}, {"5": [(24238, october)]}) == 24238


def test_wrong_pull_claim_is_revoked(tmp_path):
    from database.db import Database
    from scraper.models import ScrapedBanner
    from utils.card_pool import summarize_cards
    from webapp.server import App

    async def run():
        db = Database(str(tmp_path / "gtcha_bot.db"))
        await db.init()
        await db.save_banner(ScrapedBanner(pack_id=24238, category="Pokémon", price_coins=2000, current_packs=9, total_packs=10))
        await db.save_card_pool(24238, summarize_cards([{"id": 5, "name": "Mega Gengar ex", "buy_point": 60490,
                                                         "duplication": 1, "action_type": 2}]))
        await db.set_start(24238, int(datetime(2026, 10, 10).timestamp()), announced=True)
        app = App(str(tmp_path / "gtcha_bot.db"), str(tmp_path), "x")
        await app.bridge.init()
        user = {"user_id": "42", "name": "Basti"}
        old = {"hid": 9, "t": "2026-03-23 02:31", "pack_name": "x", "coins_back": 0, "status": "", "cards": [
            {"card_id": "5", "name": "Mega Gengar ex", "num": 1, "value": 60490, "state": "angefordert"}]}
        await app.bridge.set_history("42", {"pulls": {"items": [old], "gap": False}})
        rid = await app.bridge.add_auto_claim(user, "pull:9:5:0", 24238, "T1", action="pull_claim")
        await app.bridge.finish(rid, True, None)
        assert await app.revoke_wrong_pull_claims() == 1
        assert [r["action"] for r in await app.bridge.pending()] == ["pull_revoke"]
        assert await app.bridge.pull_claims_done() == []

    asyncio.run(run())


def test_hidden_banner_shows_seen_return_and_cards(tmp_path):
    """Versteckte Kartenliste: Rückgabe laut Zahlen der Seite und von Nutzern gezogene Karten (Kauf-Details)."""
    from aiohttp.test_utils import TestClient, TestServer

    from database.db import Database
    from scraper.models import ScrapedBanner
    from webapp.server import App, make_app

    async def run():
        db = Database(str(tmp_path / "gtcha_bot.db"))
        await db.init()
        await db.save_banner(ScrapedBanner(pack_id=24253, category="Dragon Ball", price_coins=3000,
                                           current_packs=900, total_packs=1000))
        await db.update_site_stats(24253, {"cards": 1, "coins": 100000, "players": 1})
        await db.update_converted(24253, 50000)
        await db.update_converted(24253, 200000)
        await db.set_site_name(24253, "DB 3000 新弾")
        app = App(str(tmp_path / "gtcha_bot.db"), str(tmp_path), "x")
        await app.bridge.init()
        await app.accuracy.init()
        token = (await app.bridge.redeem_code(await app.bridge.create_code(42, "Basti")))["token"]
        await app.bridge.mark_synced("42")
        pull = {"hid": 1, "t": "2026-10-12 10:00", "pack_name": "DB 3000 新弾", "coins_back": 300, "cards": [
            {"card_id": "7", "name": "Goku", "num": 1, "value": 110000, "state": "angefordert"},
            {"card_id": "8", "name": "Coin", "num": 3, "value": 100, "state": "umgewandelt"}]}
        await app.bridge.set_history("42", {"pulls": {"items": [pull], "gap": False}})
        web_app = make_app(app)
        web_app.on_startup.clear()
        web_app.on_cleanup.clear()
        client = TestClient(TestServer(web_app))
        await client.start_server()
        try:
            data = await (await client.get("/api/banner/24253", headers={"X-Device-Token": token})).json()
            assert data["seen_sold"] == 100 and data["seen_return"] == round((200000 + 110000) / 100)
            assert [(c["name"], c["count"], c["value"]) for c in data["seen_cards"]] == [("Goku", 1, 110000), ("Coin", 3, 100)]
            assert data["seen_purchases"] == 1
        finally:
            await client.close()

    asyncio.run(run())
