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
    moves = {1: [utc(21, 9)], 2: [utc(21, 9), utc(21, 6)]}
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
    assert s["banners"][0] == {"banner": 1, "spent": 3000, "pulls": 3, "returned": 1980, "balance": -1020,
                               "title": "A", "image": None, "price": 1000}


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
    assert profile(stored, now=oct_)["charge"] == 8000 and profile(stored, now=oct_)["rank"] == "white"
    # neuer Monat (JST): Aufladung zählt nicht mehr
    assert profile(stored, now=datetime(2026, 10, 31, 15, 30))["charge"] == 0
    # Rang bleibt erhalten, wenn ein späterer Lauf keinen liefert
    later = ingest(stored, [{"path": "change-member", "pages": [{"text": "1.000\nCoin"}]}], now=oct_)
    assert later["member"]["info"]["rank"] == "white" and later["member"]["info"]["coins"] == 1000
    assert profile({})["rank"] is None and profile({})["charge"] is None
