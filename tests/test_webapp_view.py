import json

from webapp.view import BannerView


def test_shipping_shows_card_value_including_tax():
    # Seite zählt 93.700 Coins ohne Steuer -> Kartenwert 103.070
    data = BannerView._shipping(json.dumps({"cards": 18, "coins": 93700, "players": 5}))
    assert data == {"ship_cards": 18, "ship_value": 103070, "ship_players": 5}
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
