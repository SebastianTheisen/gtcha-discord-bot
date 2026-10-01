from utils.card_pool import (
    detect_jump_pulls, estimate, match_shipped_hits, summarize_cards, tier_keys, tracked_units,
)


def card(card_id, value, copies=1, hit=False, name=None):
    return {"id": card_id, "name": name or f"Karte {card_id}", "buy_point": value,
            "duplication": copies, "action_type": 2 if hit else 0, "image_url": f"/card/{card_id}.jpg"}


def hit_pool():
    """100 Packs: 4 Versand-Hits (zwei mit gleichem Wert), eine normale Karte mit Hit-Wert, Rest normal."""
    return summarize_cards([
        card(1, 50000, hit=True, name="Top"),
        card(2, 20000, hit=True, name="Zweiter"),
        card(3, 8000, hit=True, name="Gleich A"),
        card(4, 8000, hit=True, name="Gleich B"),
        card(5, 3000, hit=True, name="Klein"),
        card(6, 3000, name="Normal wie Klein"),
        card(7, 500, copies=44),
        card(8, 300, copies=50),
    ])


def keys_by_name(pool):
    return {u["name"]: u["key"] for u in tracked_units(pool)}


def test_summarize_cards_counts_pool_and_hits():
    pool = hit_pool()
    assert pool["total_count"] == 100
    assert pool["total_value"] == 50000 + 20000 + 8000 * 2 + 3000 * 2 + 500 * 44 + 300 * 50
    assert pool["hits_total"] == 5
    assert [h["name"] for h in pool["hits"]] == ["Top", "Zweiter", "Gleich A", "Gleich B", "Klein"]
    assert pool["normal_values"] == {"3000": 1, "500": 44, "300": 50}


def test_summarize_cards_without_cards_returns_none():
    assert summarize_cards([]) is None


def test_unique_hit_is_certain_even_with_normal_cards_in_same_shipment():
    pool = hit_pool()
    result = match_shipped_hits(pool, 2, 50000 + 500, set())
    assert result["certain"] == [keys_by_name(pool)["Top"]]
    assert result["groups"] == [] and result["maybe"] == []


def test_equal_valued_hits_form_a_group():
    pool = hit_pool()
    keys = keys_by_name(pool)
    result = match_shipped_hits(pool, 1, 8000, set())
    assert result["certain"] == []
    assert result["groups"] == [{"value": 8000, "keys": [keys["Gleich A"], keys["Gleich B"]], "pulled": 1}]


def test_both_equal_valued_hits_shipped_are_certain():
    pool = hit_pool()
    keys = keys_by_name(pool)
    assert match_shipped_hits(pool, 2, 16000, set())["certain"] == [keys["Gleich A"], keys["Gleich B"]]


def test_hit_with_same_value_as_normal_card_is_only_possible():
    pool = hit_pool()
    result = match_shipped_hits(pool, 1, 3000, set())
    assert result["certain"] == [] and result["groups"] == []
    assert result["maybe"] == [{"value": 3000, "keys": [keys_by_name(pool)["Klein"]], "pulled": 0}]


def test_shipment_of_normal_cards_or_unknown_value_detects_nothing():
    pool = hit_pool()
    assert match_shipped_hits(pool, 1, 500, set()) == {"certain": [], "groups": [], "maybe": []}
    assert match_shipped_hits(pool, 1, 12345, set()) == {"certain": [], "groups": [], "maybe": []}


def test_already_pulled_hit_is_not_detected_again():
    pool = hit_pool()
    top = keys_by_name(pool)["Top"]
    assert match_shipped_hits(pool, 1, 50000, {top})["certain"] == []


def test_estimate_at_start_is_pool_average():
    pool = hit_pool()
    stats = estimate(pool, 100, 100, set(), 1000)
    assert round(stats["ev"]) == round(pool["total_value"] / 100)
    assert stats["estimated"] is False
    assert stats["hits_open"] == 5 and stats["open_tiers"] == ["T1", "T2", "T3"]


def test_estimate_drops_when_top_hit_is_pulled():
    pool = hit_pool()
    top = keys_by_name(pool)["Top"]
    with_top = estimate(pool, 90, 100, set(), 1000)
    without_top = estimate(pool, 90, 100, {top}, 1000)
    assert without_top["ev"] < with_top["ev"]
    assert without_top["hits_open"] == 4 and without_top["open_tiers"] == ["T2", "T3"]


def test_tier_keys_follow_hit_list_order_up_to_ten():
    pool = summarize_cards([card(i, 1000 * (20 - i), hit=True) for i in range(1, 14)] + [card(99, 10, copies=50)])
    keys = tier_keys(pool)
    assert list(keys) == [f"T{i}" for i in range(1, 11)]
    assert keys["T1"] == "1" and keys["T10"] == "10"


def test_jump_detection_for_banners_without_shipping_hits():
    pool = summarize_cards([card(1, 90000), card(2, 40000), card(3, 20000), card(4, 500, copies=97)])
    keys = {u["name"]: u["key"] for u in tracked_units(pool)}
    assert detect_jump_pulls(pool, 1000, set()) == []
    assert detect_jump_pulls(pool, 91000, set()) == [keys["Karte 1"]]
    assert detect_jump_pulls(pool, 61000, set()) == [keys["Karte 2"], keys["Karte 3"]]


def test_pool_minimum_from_new_and_old_pools():
    from utils.card_pool import pool_minimum
    pool = hit_pool()
    assert pool_minimum(pool) == {"value": 300, "copies": 50, "name": "Karte 8"}
    old = {k: v for k, v in pool.items() if k != "min"}
    assert pool_minimum(old) == {"value": 300, "copies": 50, "name": None}
