import json

from webapp.view import BannerView


def test_shipping_shows_card_value_including_tax():
    # Seite zählt 93.700 Coins ohne Steuer -> Kartenwert 103.070
    data = BannerView._shipping(json.dumps({"cards": 18, "coins": 93700, "players": 5}))
    assert data == {"ship_cards": 18, "ship_value": 103070, "ship_counted": 93700, "ship_players": 5}
    assert BannerView._shipping(None)["ship_cards"] is None


def test_out_lists_shipped_and_medal_hits_most_valuable_first():
    from utils.card_pool import summarize_cards, tracked_units
    pool = summarize_cards([
        {"id": 1, "name": "Glurak", "buy_point": 36740, "duplication": 1, "action_type": 2},
        {"id": 2, "name": "Lugia", "buy_point": 35420, "duplication": 1, "action_type": 2},
        {"id": 3, "name": "Pikachu", "buy_point": 17710, "duplication": 1, "action_type": 2},
        {"id": 4, "name": "Normal", "buy_point": 330, "duplication": 50, "action_type": 0},
    ])
    keys = {u["name"]: u["key"] for u in tracked_units(pool)}
    data = BannerView._out(pool, {keys["Pikachu"]}, {keys["Lugia"]: 1}, [{"keys": ["x"], "pulled": 1}])
    assert [h["name"] for h in data["out"]] == ["Lugia", "Pikachu"] and data["out_unsure"] == 1
    assert BannerView._out(None, set(), {}, []) == {"out": [], "out_unsure": 0}


def test_out_of_banner_uses_site_values():
    pool = {"total_value": 2_922_940}
    site = json.dumps({"cards": 45, "coins": 633_293, "players": 8})
    row = {"converted": 1_166_707, "decided_value": 1, "current_packs": 315, "total_packs": 1200, "site_stats": site}
    data = BannerView._out_of_banner(row, pool)
    assert data["converted"] == 1_166_707                       # eigene Spalte hat Vorrang
    assert data["out_total"] == 1_166_707 + 696_622             # Kartenwerte: Versand ×1,1
    assert data["undecided"] is None                            # läuft noch
    assert data["converted_max_cards"] == 885 - 45              # gezogen minus verschickt
    assert data["left_value"] == 2_922_940 - 1_166_707 - 696_622  # Versand als Kartenwert (x1,1)
    assert data["left_per_pack"] == round(data["left_value"] / 315)
    # ältere Stände ohne eigene Spalte: Summe minus Versand
    old = {"decided_value": 1_800_000, "current_packs": 315, "total_packs": 1200, "site_stats": site}
    assert BannerView._out_of_banner(old, pool)["converted"] == 1_800_000 - 633_293
    assert BannerView._out_of_banner({"site_stats": site}, pool)["out_total"] is None
    # leer gezogen: alles raus - der Rest liegt gezogen bei den Spielern
    done = {**row, "current_packs": 0}
    data = BannerView._out_of_banner(done, pool)
    assert data["undecided"] == 2_922_940 - 1_166_707 - 696_622 and data["left_value"] is None


def test_my_medals_lists_claimed_cards(tmp_path, monkeypatch):
    import asyncio
    import aiosqlite
    monkeypatch.setenv("DISCORD_TOKEN", "x")
    from database.db import Database
    from utils.card_pool import summarize_cards

    async def run():
        db = Database(str(tmp_path / "b.db"))
        await db.init()
        pool = summarize_cards([{"id": 1, "name": "Glurak", "buy_point": 36740, "duplication": 1, "action_type": 2},
                                {"id": 2, "name": "Normal", "buy_point": 300, "duplication": 99, "action_type": 0}])
        async with aiosqlite.connect(db.db_path) as conn:
            await conn.execute("INSERT INTO banners (pack_id, is_active, price_coins, card_pool) VALUES (8, 1, 1000, ?)",
                               (json.dumps(pool),))
            await conn.execute("INSERT INTO discord_threads (banner_id, thread_id) VALUES (8, 800)")
            await conn.commit()
        await db.save_medal(800, "T1", 42)
        await db.save_medal(800, "T2", 99)
        medals = await BannerView(db).my_medals("42")
        assert [(m["banner_id"], m["tier"], m["name"], m["value"]) for m in medals] == [(8, "T1", "Glurak", 36740)]

    asyncio.run(run())


def test_pack_timeline_lists_every_update_and_site_runs_with_coins():
    from webapp.view import pack_timeline
    moves = [(1000, 394, 377), (1480, 377, 347), (3000, 347, 336), (2500, 400, 410)]
    converts = [(1800, 33440), (1810, 0)]
    ships = [{"t": 1800, "cards": 1, "value": 19580, "players": 1,
              "explain": [{"icon": "✅", "text": "Mewtwo"}, {"icon": "·", "text": "+ 0"}]}]
    events = pack_timeline(moves, converts, ships)
    assert [e["kind"] for e in events] == ["pack", "out", "pack", "pack"]   # neueste zuerst, Anstieg weg
    run = events[1]
    assert run["packs"] == 347 and run["ship_value"] == 19580 and run["converted"] == 33440
    assert [l["text"] for l in run["explain"]] == ["Mewtwo"]
