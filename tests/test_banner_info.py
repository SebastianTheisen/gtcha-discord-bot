import json

import pytest

from utils.banner_info import banner_conditions, chance_at_least_one, format_conditions, to_int


def test_to_int_reads_api_values():
    assert to_int("4497") == 4497
    assert to_int(1333.0) == 1333
    assert to_int(None) == 0
    assert to_int("abc") == 0


def test_chance_single_pull_is_hits_over_packs():
    assert chance_at_least_one(500, 5, 1) == pytest.approx(1.0)


def test_chance_edge_cases():
    assert chance_at_least_one(500, 0, 10) == 0.0
    assert chance_at_least_one(10, 3, 8) == 100.0
    assert 0 < chance_at_least_one(5000, 10, 50) < 100


@pytest.mark.parametrize("badges, min_charge, password, expected", [
    (["all"], 0, False, "Alle Mitgliedsränge"),
    (["gold", "rainbow", "black"], 0, False, "Ab Mitgliedsrang **Gold**"),
    (["silver", "gold", "rainbow", "black"], 5000, True,
     "Ab Mitgliedsrang **Silber**\nMindest-Aufladung: 5.000 Coins im Monat\n🔒 Nur mit Passwort"),
])
def test_format_conditions(badges, min_charge, password, expected):
    item = {"badges": badges, "min_charge_amount": min_charge, "password_flag": password}
    assert format_conditions(json.dumps(banner_conditions(item))) == expected


def test_format_conditions_without_data():
    assert format_conditions(None) is None


def test_format_shipping():
    from utils.banner_info import format_shipping, shipping_stats
    item = {"total_sendcount": 5, "total_sendprice": 702050, "total_sendpeople": 1}
    assert format_shipping(json.dumps(shipping_stats(item))) == "5 Karten · 702.050 Coins · 1 Spieler"
    assert format_shipping(json.dumps(shipping_stats({}))) == "Noch nichts verschickt"
    assert format_shipping(None) is None
