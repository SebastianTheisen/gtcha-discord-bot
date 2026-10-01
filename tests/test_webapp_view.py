import json

from webapp.view import BannerView


def test_shipping_shows_card_value_including_tax():
    # Seite zählt 93.700 Coins ohne Steuer -> Kartenwert 103.070
    data = BannerView._shipping(json.dumps({"cards": 18, "coins": 93700, "players": 5}))
    assert data == {"ship_cards": 18, "ship_value": 103070, "ship_players": 5}
    assert BannerView._shipping(None)["ship_cards"] is None
