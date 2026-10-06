import asyncio
from datetime import datetime

from utils.app_bridge import AppBridge
from webapp.history import (build_history, ingest, merge_newest_first, parse_cards, parse_coins, parse_member, profile,
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
        assert [r["action"] for r in await bridge.pending()] == ["claim"]

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
