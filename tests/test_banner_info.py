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
    assert format_shipping(json.dumps(shipping_stats(item))) == "5 Karten · 772.255 Coins · 1 Spieler"
    assert format_shipping(json.dumps(shipping_stats({}))) == "Noch nichts verschickt"
    assert format_shipping(None) is None


def test_category_for_pack_list_items():
    from utils.banner_info import category_for
    assert category_for({"card_type": "2", "is_bonus": 0}) == "Pokémon"
    assert category_for({"card_type": "5", "is_bonus": 0}) == "One piece"
    assert category_for({"card_type": "9", "is_bonus": 0}) == "Dragon Ball"
    assert category_for({"card_type": "999996", "is_bonus": 0}) == "MIX"
    assert category_for({"card_type": "999996", "is_bonus": 1}) == "Bonus"
    assert category_for({"card_type": "7", "is_bonus": 0}) is None


def test_jst_timestamp_reads_site_times():
    from utils.banner_info import jst_timestamp
    assert jst_timestamp("2026-10-02 18:00:00") == 1790931600     # 09:00 UTC
    assert jst_timestamp("2026/11/01 00:00") == jst_timestamp("2026-11-01 00:00:00")
    assert jst_timestamp("") is None and jst_timestamp("kaputt") is None


def test_berlin_time_handles_summer_and_winter():
    from utils.banner_info import berlin_time
    assert berlin_time(1790931600).strftime("%d.%m. %H:%M") == "02.10. 11:00"   # 09:00 UTC, Sommerzeit
    assert berlin_time(1793922600).strftime("%d.%m. %H:%M") == "06.11. 00:50"   # 05.11. 23:50 UTC, Winterzeit


def test_thread_title_formats():
    from utils.banner_info import thread_title
    assert thread_title(24172, 30000, 300, 0, "upcoming", 1790931600) == \
        "🕒 ab 02.10. 11:00 · 30.000 Coins · 300 Packs · ID 24172"
    assert thread_title(24106, 1111, 5000, 10, "running") == "🎯 · 1.111 Coins · 5.000 Packs · 10/Tag · ID 24106"
    assert thread_title(24164, 2222, 5000, None, "hits_out").startswith("🔴 Hits raus · 2.222 Coins")


def test_parse_thread_title_new_and_old_format():
    from utils.banner_info import parse_thread_title
    assert parse_thread_title("⚡ Endspurt · 1.333 Coins · 5.000 Packs · 10/Tag · ID 24114") == \
        {"pack_id": 24114, "price": 1333, "entries": 10, "total": 5000}
    assert parse_thread_title("ID: 24106 / Kosten: 1111 Coins / Anzahl Pulls: unbegrenzt / Pulls Gesamt: 5000") == \
        {"pack_id": 24106, "price": 1111, "entries": None, "total": 5000}


def test_sale_end_formats_from_the_site():
    from datetime import datetime, timezone
    from utils.banner_info import sale_end_timestamp
    expected = int(datetime(2026, 10, 31, 14, 59, tzinfo=timezone.utc).timestamp())   # 23:59 JST
    assert sale_end_timestamp("Erhältlich bis 31/10/2026 23:59 JST") == expected
    assert sale_end_timestamp("2026/10/31 23:59 まで販売") == expected
    assert sale_end_timestamp("2026/10/31") == expected                                # ohne Uhrzeit: 23:59
    assert sale_end_timestamp("bald") is None and sale_end_timestamp(None) is None
