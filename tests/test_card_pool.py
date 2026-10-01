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


def net(value):
    """Betrag, wie ihn total_sendprice zählt (ohne 10 % Steuer)."""
    return round(value / 1.1)


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
    result = match_shipped_hits(pool, 2, net(50000 + 500), set())
    assert result["certain"] == [keys_by_name(pool)["Top"]]
    assert result["groups"] == [] and result["maybe"] == []


def test_equal_valued_hits_form_a_group():
    pool = hit_pool()
    keys = keys_by_name(pool)
    result = match_shipped_hits(pool, 1, net(8000), set())
    assert result["certain"] == []
    assert result["groups"] == [{"value": 8000, "value_max": 8000,
                                 "keys": [keys["Gleich A"], keys["Gleich B"]], "pulled": 1}]


def test_both_equal_valued_hits_shipped_are_certain():
    pool = hit_pool()
    keys = keys_by_name(pool)
    assert match_shipped_hits(pool, 2, net(16000), set())["certain"] == [keys["Gleich A"], keys["Gleich B"]]


def test_hit_with_same_value_as_normal_card_is_only_possible():
    pool = hit_pool()
    result = match_shipped_hits(pool, 1, net(3000), set())
    assert result["certain"] == [] and result["groups"] == []
    assert result["maybe"] == [{"value": 3000, "value_max": 3000,
                                "keys": [keys_by_name(pool)["Klein"]], "pulled": 0}]


def test_shipment_of_normal_cards_or_unknown_value_detects_nothing():
    pool = hit_pool()
    assert match_shipped_hits(pool, 1, net(500), set()) == {"certain": [], "groups": [], "maybe": []}
    assert match_shipped_hits(pool, 1, 12345, set()) == {"certain": [], "groups": [], "maybe": []}


def test_already_pulled_hit_is_not_detected_again():
    pool = hit_pool()
    top = keys_by_name(pool)["Top"]
    assert match_shipped_hits(pool, 1, net(50000), {top})["certain"] == []


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


def test_tier_keys_follow_hit_list_order():
    pool = summarize_cards([card(i, 1000 * (20 - i), hit=True) for i in range(1, 14)] + [card(99, 10, copies=50)])
    keys = tier_keys(pool)
    assert list(keys) == [f"T{i}" for i in range(1, 14)]
    assert keys["T1"] == "1" and keys["T13"] == "13"


def test_medal_resolves_exactly_one_group():
    from utils.card_pool import resolve_pulled
    groups = [{"keys": ["a", "b", "c"], "pulled": 1}, {"keys": ["b", "c"], "pulled": 1}]
    pulled, sure, open_groups = resolve_pulled([], groups, {"b"})
    assert open_groups == [groups[0]]          # Medaille b erledigt nur die kleinere Gruppe
    assert len(pulled) == 2                    # b + ein Stellvertreter für die offene Gruppe
    pulled, _, open_groups = resolve_pulled([], groups, {"b", "a"})
    assert open_groups == [] and pulled == {"a", "b"}


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


def test_hits_below_pack_price_do_not_count():
    pool = hit_pool()
    stats = estimate(pool, 100, 100, set(), 5000)
    assert stats["hits_total"] == 4          # 3.000er-Hit liegt unter dem Packpreis
    assert [u["name"] for u in stats["open_units"]] == ["Top", "Zweiter", "Gleich A", "Gleich B"]
    assert estimate(pool, 100, 100, set(), None)["hits_total"] == 5


def test_cost_to_next_hit():
    pool = hit_pool()
    stats = estimate(pool, 99, 100, set(), 1000)
    # 5 offene Hits, 99 Packs: (99 + 1) / (5 + 1) Züge à 1.000 Coins
    assert round(stats["cost_to_hit"]) == round(1000 * 100 / 6)
    assert estimate(pool, 99, 100, set(), None)["cost_to_hit"] is None


def test_drifted_card_value_still_matches_within_tolerance():
    pool = hit_pool()
    # Rundung: Top-Hit wird mit 50.240 statt 50.000 gezählt (< 1 %)
    assert match_shipped_hits(pool, 1, net(50240), set())["certain"] == [keys_by_name(pool)["Top"]]


def test_shipment_needing_one_of_several_similar_hits_becomes_group():
    # 3 Karten, Summe nur mit genau einem Hit erreichbar, zwei gleich teure Hits kommen in Frage
    pool = summarize_cards([card(1, 26180, hit=True), card(2, 25410, hit=True), card(3, 18260, hit=True),
                            card(4, 18260, hit=True), card(5, 5000, copies=20), card(6, 300, copies=500)])
    result = match_shipped_hits(pool, 3, net(18260 + 5000 + 300), set())
    assert result["certain"] == []
    assert len(result["groups"]) == 1 and result["groups"][0]["pulled"] == 1


def test_identical_duplicate_hits_are_checked_off_one_by_one():
    # Wie 24158: 5 Exemplare derselben Karte, eins wird verschickt -> genau eins sicher abgehakt
    pool = summarize_cards([card(1, 165000, copies=5, hit=True, name="Box"), card(2, 55000, copies=10),
                            card(3, 300, copies=200)])
    result = match_shipped_hits(pool, 1, 150000, set())
    assert len(result["certain"]) == 1 and result["groups"] == []
    second = match_shipped_hits(pool, 1, 150000, set(result["certain"]))
    assert len(second["certain"]) == 1 and second["certain"] != result["certain"]


def test_shipment_values_are_counted_without_tax():
    # Echte Fälle: gezählter Betrag = Kartenwert / 1,1
    pool = summarize_cards([card(1, 18260, hit=True, name="Hit 18"), card(2, 25410, hit=True),
                            card(3, 660, copies=200), card(4, 300, copies=500)])
    assert match_shipped_hits(pool, 1, 16600, set())["certain"] == [keys_by_name(pool)["Hit 18"]]
    assert match_shipped_hits(pool, 1, 18260, set())["certain"] == []   # Bruttowert passt nicht mehr


def test_medal_claimed_card_is_preferred_for_a_shipment():
    # Wie 24149: T2 (Lugia) per Medaille gemeldet, dann 8 Karten / 37.900 verschickt.
    # Rechnerisch passen Glurak oder Lugia - die gemeldete Lugia ist es.
    from utils.card_pool import prefer_claimed
    pool = summarize_cards([card(1, 36740, hit=True, name="Glurak"), card(2, 35420, hit=True, name="Lugia"),
                            card(3, 17710, hit=True, name="Pikachu"), card(4, 1980, copies=20),
                            card(5, 660, copies=200), card(6, 330, copies=300)])
    keys = keys_by_name(pool)
    raw = match_shipped_hits(pool, 8, 37900, set())
    assert raw["certain"] == [] and len(raw["groups"]) == 1        # ohne Medaille: Glurak oder Lugia
    result = prefer_claimed(raw, {keys["Lugia"]})
    assert result["certain"] == [keys["Lugia"]] and result["groups"] == []


def _glurak_pool():
    return summarize_cards([card(1, 36740, hit=True, name="Glurak"), card(2, 35420, hit=True, name="Lugia"),
                            card(3, 17710, hit=True, name="Pikachu"), card(4, 1980, copies=20),
                            card(5, 660, copies=200), card(6, 330, copies=300)])


def test_history_later_batch_resolves_earlier_ambiguity():
    from utils.card_pool import match_shipment_history
    pool = _glurak_pool()
    keys = keys_by_name(pool)
    first = [[8, 37900]]                                   # Glurak oder Lugia
    alone = match_shipment_history(pool, first)
    assert alone["certain"] == [] and len(alone["groups"]) == 1
    # Späterer Einzelversand mit 33.400 (= Glurak / 1,1): Glurak ist jetzt weg -> erster Schub war Lugia
    both = match_shipment_history(pool, first + [[1, 33400]])
    assert sorted(both["certain"]) == sorted([keys["Glurak"], keys["Lugia"]]) and both["groups"] == []


def test_history_skips_unexplainable_and_huge_batches():
    from utils.card_pool import match_shipment_history
    pool = _glurak_pool()
    result = match_shipment_history(pool, [[1, 14900], [80, 999999], [1, 16100]])
    assert result["certain"] == [keys_by_name(pool)["Pikachu"]] and result["used_batches"] == 1


def test_history_uses_medal_to_resolve_group():
    from utils.card_pool import match_shipment_history
    pool = _glurak_pool()
    result = match_shipment_history(pool, [[8, 37900]], claimed={keys_by_name(pool)["Lugia"]})
    assert result["certain"] == [keys_by_name(pool)["Lugia"]] and result["groups"] == []


def test_estimate_for_pool_without_shipping_hits():
    # Wie 24060 (Coin-Banner, gratis): keine Versand-Hits, Preis 0
    pool = summarize_cards([card(1, 30000), card(2, 10000), card(3, 5000), card(4, 100, copies=97)])
    stats = estimate(pool, 60, 100, set(), 0)
    assert stats is not None and stats["tracked_hits"] is False
    assert stats["ev_pct"] is None and stats["cost_to_hit"] is None


def test_pool_keeps_full_card_list_for_web_app():
    pool = hit_pool()
    assert [c["value"] for c in pool["cards"]] == sorted((c["value"] for c in pool["cards"]), reverse=True)
    assert sum(c["copies"] for c in pool["cards"]) == pool["total_count"]
    assert {c["name"] for c in pool["cards"] if c["hit"]} == {"Top", "Zweiter", "Gleich A", "Gleich B", "Klein"}


def test_explain_batch_kinds():
    from utils.card_pool import explain_batch
    pool = _glurak_pool()
    keys = keys_by_name(pool)
    pika = explain_batch(pool, 1, 16100, set())
    assert pika["kind"] == "hits" and pika["certain"] == [keys["Pikachu"]] and pika["value"] == 17710
    group = explain_batch(pool, 8, 37900, set())
    assert group["kind"] == "hits" and len(group["groups"]) == 1
    medal = explain_batch(pool, 8, 37900, set(), claimed={keys["Lugia"]})
    assert medal["certain"] == [keys["Lugia"]] and medal["groups"] == []
    assert explain_batch(pool, 1, 600, set())["kind"] == "normal"
    assert explain_batch(pool, 80, 999999, set())["kind"] == "too_big"
    assert explain_batch(pool, 1, 12345, set())["kind"] == "unclear"
    # schon verschickter Hit zählt nicht noch einmal
    assert explain_batch(pool, 1, 16100, {keys["Pikachu"]})["kind"] == "unclear"
    # Hit mit gleichem Wert wie eine normale Karte: nur "vielleicht"
    maybe = explain_batch(hit_pool(), 1, net(3000), set())
    assert maybe["kind"] == "maybe" and maybe["maybe"][0]["keys"] == [keys_by_name(hit_pool())["Klein"]]


def test_average_return_from_site_numbers():
    from utils.card_pool import out_of_banner_value
    pool = _glurak_pool()
    keys = keys_by_name(pool)
    # umgewandelt voll, verschickt ohne Steuer (x1,1), Lugia per Medaille gezogen aber noch nicht verschickt
    out = out_of_banner_value(pool, 50_000, 16_100, {keys["Lugia"]})
    assert out == 50_000 + 17_710 + 35_420
    stats = estimate(pool, 100, pool["total_count"], {keys["Lugia"]}, 1000, out)
    assert stats["data_based"] and round(stats["ev"]) == round((pool["total_value"] - out) / 100)
    assert out_of_banner_value(pool, None, 16_100) is None
    # mehr raus als im Pool (z.B. neuer Pool): zurück zur Schätzung
    assert estimate(pool, 100, pool["total_count"], set(), 1000, pool["total_value"] * 2)["data_based"] is False
