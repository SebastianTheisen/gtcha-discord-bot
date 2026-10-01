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
    assert data["out_total"] == 1_166_707 + 633_293             # wie von der Seite geliefert
    assert data["converted_max_cards"] == 885 - 45              # gezogen minus verschickt
    assert data["left_value"] == 2_922_940 - 1_166_707 - 696_622  # Versand als Kartenwert (x1,1)
    assert data["left_per_pack"] == round(data["left_value"] / 315)
    # ältere Stände ohne eigene Spalte: Summe minus Versand
    old = {"decided_value": 1_800_000, "current_packs": 315, "total_packs": 1200, "site_stats": site}
    assert BannerView._out_of_banner(old, pool)["converted"] == 1_800_000 - 633_293
    assert BannerView._out_of_banner({"site_stats": site}, pool)["out_total"] is None
