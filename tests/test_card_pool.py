from utils.card_pool import (
    detect_jump_pulls, estimate, hit_still_in, match_shipped_hits, medal_units, summarize_cards, tier_keys, tracked_units,
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


def test_hit_still_in_uses_learned_hidden_rate():
    assert hit_still_in(1.0, 0.4) == 1.0
    assert abs(hit_still_in(0.5, 0.4) - 0.5 / 0.7) < 1e-9
    assert hit_still_in(0.5, None) == 1.0


def test_estimate_counts_unseen_hits_only_by_chance_still_in():
    pool = hit_pool()
    plain = estimate(pool, 50, 100, set(), 1000)
    hidden = estimate(pool, 50, 100, set(), 1000, hidden_rate=0.4)
    assert hidden["ev"] < plain["ev"] and hidden["hits_left"] < plain["hits_left"]
    assert abs(hidden["hit_still_in"] - 0.714) < 0.001
    # mit echten Zahlen der Seite: unsichtbar gezogene Hits zusätzlich abziehen
    seen = estimate(pool, 50, 100, set(), 1000, out_value=10000)
    both = estimate(pool, 50, 100, set(), 1000, out_value=10000, hidden_rate=0.4)
    assert both["data_based"] and both["ev"] < seen["ev"]


def test_tier_keys_follow_hit_list_order():
    pool = summarize_cards([card(i, 1000 * (20 - i), hit=True) for i in range(1, 14)] + [card(99, 10, copies=50)])
    keys = tier_keys(pool)
    # zuerst die 13 Versand-Hits nach Wert, danach normale Karten (hier 50 Exemplare -> bis T50)
    assert list(keys)[:13] == [f"T{i}" for i in range(1, 14)] and len(keys) == 50
    assert keys["T1"] == "1" and keys["T13"] == "13" and keys["T14"] == "99"


def test_medal_does_not_resolve_group():
    # Wie 24152: Versand passt zu T2 oder T3, T3 hat eine Medaille - verschickt war aber T2.
    # Die Medaille klärt die Gruppe also nicht: ❓ bleibt, die übrige Karte zählt als Stellvertreter.
    from utils.card_pool import resolve_pulled
    groups = [{"keys": ["a", "b", "c"], "pulled": 1}, {"keys": ["b", "c"], "pulled": 1}]
    pulled, sure, open_groups = resolve_pulled([], groups, {"b"})
    assert open_groups == [groups[1], groups[0]] and pulled == {"a", "b", "c"}
    pulled, _, open_groups = resolve_pulled([], [{"keys": ["b", "c"], "pulled": 1}], {"b"})
    assert len(open_groups) == 1 and pulled == {"b", "c"}
    # erst wenn alle Kandidaten gemeldet sind, ist nichts mehr offen
    pulled, _, open_groups = resolve_pulled([], [{"keys": ["b", "c"], "pulled": 1}], {"b", "c"})
    assert open_groups == [] and pulled == {"b", "c"}


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
    assert explain_batch(pool, 1, 600, set())["kind"] == "normal"
    assert explain_batch(pool, 80, 999999, set())["kind"] == "too_big"
    # einzelne Karte über allen normalen, passt zu keinem Wert: Hit mit anderem Wert der Seite (Spanne ×1,6)
    odd = explain_batch(pool, 1, 12345, set())
    assert odd["kind"] == "hits" and odd["certain"] == [keys["Pikachu"]] and odd["off_value"]
    assert explain_batch(pool, 1, 5000, set())["kind"] == "unclear"
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


def test_top_three_count_copies_without_shipping_hits():
    # Wie 24177: 3 Packs, dieselbe Coin-Karte 3x - das sind T1, T2 und T3
    pool = summarize_cards([card(1, 250000, copies=3, name="Coin")])
    units = tracked_units(pool)
    assert [u["key"] for u in units] == ["1", "1#2", "1#3"]
    assert list(tier_keys(pool).values()) == ["1", "1#2", "1#3"]
    # ein Exemplar gezogen (alter Schlüssel "1"): noch 2 von 3 drin, nicht "Hits raus"
    stats = estimate(pool, 2, 3, {"1"}, 200000)
    assert stats["open_tiers"] == ["T2", "T3"]
    # gemischt: 2x teuerste Karte, dann die nächste
    pool = summarize_cards([card(1, 9000, copies=2), card(2, 5000), card(3, 100, copies=50)])
    assert [u["key"] for u in tracked_units(pool)] == ["1", "1#2", "2"]


def test_card_value_changes_between_pools():
    from utils.card_pool import card_value_changes
    old = summarize_cards([card(1, 14000, name="A"), card(2, 990, copies=10), card(3, 500)])
    new = summarize_cards([card(1, 15400, name="A"), card(2, 990, copies=10), card(4, 700)])
    assert card_value_changes(old, new) == [{"id": "1", "name": "A", "old": 14000, "new": 15400}]
    assert card_value_changes(None, new) == [] and card_value_changes({"hits": []}, new) == []


def test_medal_hits_already_shipped_are_not_counted_twice():
    # Wie 24149: alle 3 Hits per Medaille gemeldet und verschickt, Versand aber keinem Hit zugeordnet
    from utils.card_pool import out_of_banner_value
    pool = _glurak_pool()
    keys = keys_by_name(pool)
    medals = {keys["Glurak"], keys["Lugia"], keys["Pikachu"]}          # 89.870 Kartenwert
    out = out_of_banner_value(pool, 337_370, 94_300, medals)           # verschickt: 103.730 Kartenwert
    assert out == 337_370 + 103_730                                     # Hits stecken im Versand
    # Hits per Medaille, aber kaum etwas verschickt: dann zählen sie zusätzlich
    out = out_of_banner_value(pool, 10_000, 600, {keys["Lugia"]})
    assert out == 10_000 + 660 + 35_420
    # sicher zugeordnete Hits verbrauchen den Versandwert
    out = out_of_banner_value(pool, 0, 32_200, {keys["Glurak"]}, {keys["Lugia"]})   # 35.420 = Lugia
    assert out == 35_420 + 36_740


def test_claimable_cards_from_pack_price():
    from utils.card_pool import claimable_units, medal_units
    # ohne Versand-Hits: alle Karten ab Packpreis melden, Platz = Medaille
    pool = summarize_cards([card(1, 9000, copies=2), card(2, 5000), card(3, 2000), card(4, 300, copies=50)])
    assert [u["key"] for u in claimable_units(pool, 1000)] == ["1", "1#2", "2", "3"]
    assert list(tier_keys(pool).values())[:4] == ["1", "1#2", "2", "3"]
    assert len(claimable_units(pool, None)) == 3                       # gratis: wie bisher T1-T3
    assert len(medal_units(pool)) == 50                                # höchstens T50
    # mit Versand-Hits: die Hits ab Packpreis
    assert [u["name"] for u in claimable_units(hit_pool(), 5000)] == ["Top", "Zweiter", "Gleich A", "Gleich B"]


def test_claimed_normal_cards_are_not_counted_twice():
    # Normale Karten werden umgewandelt und stecken dann in "umgewandelt" - nicht extra zählen
    from utils.card_pool import out_of_banner_value
    pool = summarize_cards([card(1, 9000), card(2, 5000), card(4, 300, copies=50)])
    assert out_of_banner_value(pool, 9000, 0, {"1"}) == 9000


def test_medal_places_strictly_by_value():
    # Wie 24168: 3 Versand-Hits, dazu normale Karten über dem Packpreis
    from utils.card_pool import claimable_units
    pool = summarize_cards([card(1, 125180, hit=True), card(2, 85580, hit=True), card(3, 1000, hit=True),
                            card(4, 26740), card(5, 22220, copies=2), card(6, 990, copies=50)])
    units = claimable_units(pool, 2500)
    assert [(u["tier"], u["key"]) for u in units] == [("T1", "1"), ("T2", "2"), ("T3", "4"), ("T4", "5"), ("T5", "5#2")]
    # Hit unter Packpreis nicht meldbar; Hit-Liste/Erkennung bleibt bei den Versand-Hits
    assert [u["key"] for u in tracked_units(pool)] == ["1", "2", "3"]
    # normale Karte teurer als ein Hit: streng nach Wert, Hits behalten ihren Platz in der Hit-Liste
    pool = summarize_cards([card(1, 50000, hit=True), card(7, 90000), card(2, 30000, hit=True)])
    assert [u["key"] for u in claimable_units(pool, 1000)] == ["7", "1", "2"]
    assert [(u["key"], u["tier"]) for u in tracked_units(pool)] == [("1", "T2"), ("2", "T3")]


def test_medal_units_stay_fast_for_huge_pools():
    # Wie 24060: über 100.000 Exemplare - es dürfen nur die nötigen Einheiten erzeugt werden
    import time
    from utils.card_pool import medal_units
    pool = summarize_cards([card(i, 100 + i, copies=1000) for i in range(116)])
    started = time.perf_counter()
    for _ in range(20):
        units = medal_units(pool)
    assert len(units) == 50 and time.perf_counter() - started < 0.5


def test_order_model_shipment_counts_orders_not_cards():
    """Banner 24152: die Seite zählt 1 Versand mit 105.380 Coins Kartenwert. Als 1 Karte passt nichts -
    als 1 Auftrag passt genau T2 (98.780) + 20 normale Karten à 330."""
    from utils.card_pool import order_options, summarize_cards
    pool = summarize_cards([
        {"id": 1, "name": "Luffy Dortmund", "buy_point": 118580, "duplication": 1, "action_type": 2},
        {"id": 2, "name": "Luffy Gold Frame", "buy_point": 98780, "duplication": 1, "action_type": 2},
        {"id": 3, "name": "Luffy Katsumi", "buy_point": 98560, "duplication": 1, "action_type": 2},
        {"id": 4, "name": "Tashigi", "buy_point": 10940, "duplication": 1, "action_type": 2},
        {"id": 5, "name": "Shirahoshi", "buy_point": 10560, "duplication": 1, "action_type": 2},
        {"id": 6, "name": "Normal", "buy_point": 330, "duplication": 400, "action_type": 0},
        {"id": 7, "name": "Normal 2", "buy_point": 660, "duplication": 60, "action_type": 0},
        {"id": 8, "name": "Normal 3", "buy_point": 990, "duplication": 30, "action_type": 0},
    ])
    assert order_options(pool, 1, 105380) == [frozenset({"2"})]           # eindeutig T2
    assert order_options(pool, 2, 550) == []                              # passt zu keinem Modell
    assert frozenset() in order_options(pool, 1, 6600)                    # nur normale Karten möglich
    assert order_options(pool, 0, 1320) == [frozenset()]                  # Karten zu bestehendem Auftrag
    assert frozenset({"4", "5"}) in order_options(pool, 1, 10940 + 10560 + 330)


def test_shipment_history_falls_back_to_order_model():
    """Schub, der als Karten nicht aufgeht, wird als Versand-Auftrag ausgewertet (Zahlen von 24152)."""
    from utils.card_pool import match_shipment_history, summarize_cards
    pool = summarize_cards([
        {"id": 2, "name": "Luffy Gold Frame", "buy_point": 98780, "duplication": 1, "action_type": 2},
        {"id": 3, "name": "Luffy Katsumi", "buy_point": 98560, "duplication": 1, "action_type": 2},
        {"id": 4, "name": "Tashigi", "buy_point": 10940, "duplication": 1, "action_type": 2},
        {"id": 6, "name": "Normal", "buy_point": 330, "duplication": 400, "action_type": 0},
        {"id": 7, "name": "Normal 2", "buy_point": 220, "duplication": 100, "action_type": 0},
    ])
    batches = [[1, 95800]]                       # 1 gezählt, 105.380 Kartenwert: als 1 Karte unmöglich
    res = match_shipment_history(pool, batches)
    assert res["used_batches"] == 1
    assert res["groups"] and set(res["groups"][0]["keys"]) == {"2", "3"} and res["groups"][0]["pulled"] == 1
    # Schub, der schon als Karten aufgeht, wird wie bisher gewertet
    assert match_shipment_history(pool, [[1, round(10940 / 1.1)]])["certain"] == ["4"]


def test_medal_deadline_resolves_shipment():
    # Regel der Gruppe: Medaille gesetzt = Versand angefordert -> spätestens im ersten Schub danach.
    # Wie 24152: 105.380 Kartenwert passt zu T2 (98.780 + Rest) oder T3 (98.560 + Rest)
    from utils.card_pool import batch_deadlines, explain_batch, match_shipment_history, summarize_cards
    pool = summarize_cards([
        {"id": 2, "name": "Luffy Gold Frame", "buy_point": 98780, "duplication": 1, "action_type": 2},
        {"id": 3, "name": "Luffy Katsumi", "buy_point": 98560, "duplication": 1, "action_type": 2},
        {"id": 6, "name": "Normal", "buy_point": 330, "duplication": 400, "action_type": 0},
        {"id": 7, "name": "Normal 2", "buy_point": 220, "duplication": 100, "action_type": 0},
    ])
    batches = [[1, 95800, 1000]]
    assert match_shipment_history(pool, batches)["groups"]                      # ohne Medaille: ❓
    due = batch_deadlines(batches, {"3": 900})                                # T3-Medaille vor dem Schub
    assert due == {"3": 0}
    res = match_shipment_history(pool, batches, deadlines=due)
    assert res["certain"] == ["3"] and res["groups"] == [] and not res["ignored_deadlines"]
    # Medaille erst nach dem letzten Schub: noch keine Frist
    assert batch_deadlines(batches, {"3": 2000}) == {}
    # Schübe ohne Zeit (vor der Aufzeichnung) sind nie Frist-Schübe
    assert batch_deadlines([[1, 95800, None], [1, 95800, 1000]], {"3": 0}) == {"3": 1}
    # Wert passt nicht zur Medaillen-Karte: Medaille hat Vorrang (Seite zählt manchmal einen anderen Wert)
    res = match_shipment_history(pool, [[1, 300, 1000]], deadlines={"3": 0})
    assert res["certain"] == ["3"] and res["value_mismatch"] == ["3"]
    # ohne Karte im Frist-Schub (nur Wert geändert) gibt es nichts zu erzwingen
    res = match_shipment_history(pool, [[0, 300, 1000]], deadlines={"3": 0})
    assert res["certain"] == [] and res["ignored_deadlines"] == ["3"]
    # Einzelansicht eines Schubs genauso
    one = explain_batch(pool, 1, 95800, set(), required={"3"})
    assert one["certain"] == ["3"] and one["groups"] == []


def test_orders_model_prefers_shipping_hits_only():
    # Wie 24136, Schub 9: 2 Aufträge, 1.466.740 Kartenwert. Mit beliebig vielen normalen Karten passt fast alles,
    # allein aus Versand-Hits nur T1 + T4 + T5 + T7 - normale Karten wandelt praktisch jeder um.
    from utils.card_pool import match_shipment_history, order_options, summarize_cards
    hits = [727520, 453130, 438810, 415000, 271210, 64960, 52580]
    cards = [{"id": i + 1, "name": f"T{i + 1}", "buy_point": v, "duplication": 1, "action_type": 2}
             for i, v in enumerate(hits)]
    cards += [{"id": 100 + i, "name": f"N{i}", "buy_point": v, "duplication": 30, "action_type": 0}
              for i, v in enumerate([3300, 4400, 6600, 9900, 13200, 22000, 33000, 49940])]
    pool = summarize_cards(cards)
    net = round(1466740 / 1.1)
    assert len(order_options(pool, 2, 1466740, prefer_hits=False)) > 1          # mit normalen Karten mehrdeutig
    assert order_options(pool, 2, 1466740) == [frozenset({"1", "4", "5", "7"})]
    res = match_shipment_history(pool, [[2, net, 1000]])
    assert sorted(res["certain"]) == ["1", "4", "5", "7"] and res["groups"] == []
    # geht es nur mit normalen Karten (T2 + 195.540), bleiben die normalen Karten erlaubt
    assert order_options(pool, 2, 648670)


def test_cheap_normal_cards_only_when_needed():
    # Billige Karten (unter 3× Packpreis) werden fast immer umgewandelt: erst ohne sie erklären
    from utils.card_pool import match_shipment_history, summarize_cards
    pool = summarize_cards([
        {"id": 1, "name": "H1", "buy_point": 100000, "duplication": 1, "action_type": 2},
        {"id": 2, "name": "H2", "buy_point": 105000, "duplication": 1, "action_type": 2},
        {"id": 5, "name": "Wertvoll", "buy_point": 40000, "duplication": 2, "action_type": 0},
        {"id": 6, "name": "Billig", "buy_point": 4000, "duplication": 50, "action_type": 0},
    ])
    batch = [[1, round(140000 / 1.1), 1000]]       # 1 Auftrag: H1 + 40.000 oder nur billige/normale Karten
    assert match_shipment_history(pool, batch)["certain"] == []                 # ohne Packpreis: mehrdeutig
    assert match_shipment_history(pool, batch, price=10000)["certain"] == ["1"]
    # geht es nur mit billigen Karten, sind sie erlaubt
    res = match_shipment_history(pool, [[1, round(113000 / 1.1), 1000]], price=10000)   # H2 + 2 × 4.000
    assert res["certain"] == ["2"]


def test_24188_medal_wins_over_counted_value():
    # 24188: Mewtwo (T2, 26.740) um 17:49 gezogen + Medaille + Versand angefordert; der Schub um 18:00
    # (+1 Karte, neuer Spieler) wurde als 19.580 gezählt. Um 23:00 kam nochmal 19.580 = Squirtle (T3).
    from utils.card_pool import batch_deadlines, explain_batch, match_shipment_history, summarize_cards
    pool = summarize_cards([
        {"id": 1, "name": "Pikachu", "buy_point": 39380, "duplication": 1, "action_type": 2},
        {"id": 2, "name": "Mewtwo", "buy_point": 26740, "duplication": 1, "action_type": 2},
        {"id": 3, "name": "Squirtle", "buy_point": 19580, "duplication": 1, "action_type": 2},
        {"id": 9, "name": "Normal", "buy_point": 550, "duplication": 497, "action_type": 0}])
    batches = [[1, 17800, 1800], [1, 17800, 2300], [1, 35800, 2700]]
    due = batch_deadlines(batches, {"2": 1749})
    assert due == {"2": 0}
    res = match_shipment_history(pool, batches, deadlines=due, price=1000)
    assert sorted(res["certain"]) == ["1", "2", "3"] and res["value_mismatch"] == ["2"]
    one = explain_batch(pool, 1, 17800, set(), required={"2"}, price=1000)
    assert one["certain"] == ["2"] and one["mismatch"] == ["2"]


def test_medal_batch_with_more_hits():
    # Medaille auf T3 (17.000): im ersten Schub danach werden weitere passende Hits mit erkannt
    from utils.card_pool import explain_batch, match_shipment_history, summarize_cards
    pool = summarize_cards([
        {"id": 1, "name": "Gross", "buy_point": 100000, "duplication": 1, "action_type": 2},
        {"id": 2, "name": "Mittel", "buy_point": 50000, "duplication": 1, "action_type": 2},
        {"id": 3, "name": "Medaille", "buy_point": 17000, "duplication": 1, "action_type": 2},
        {"id": 9, "name": "Normal", "buy_point": 700, "duplication": 500, "action_type": 0}])
    # Schub deutlich höher als die Medaillen-Karte: der zweite Hit (T1) wird mit gefunden
    batches = [[2, round(117000 / 1.1), 1000]]
    res = match_shipment_history(pool, batches, deadlines={"3": 0}, price=1000)
    assert sorted(res["certain"]) == ["1", "3"] and res["value_mismatch"] == []
    one = explain_batch(pool, 2, round(117000 / 1.1), set(), required={"3"}, price=1000)
    assert sorted(one["certain"]) == ["1", "3"]
    # dritter Hit: 3 Karten · Medaille + T1 + T2
    res = match_shipment_history(pool, [[3, round(167000 / 1.1), 1000]], deadlines={"3": 0}, price=1000)
    assert sorted(res["certain"]) == ["1", "2", "3"]
    # passt gar nicht zur Medaillen-Karte (Seite zählt anderen Wert): Medaille trotzdem, Rest weiter ausgewertet
    res = match_shipment_history(pool, [[1, round(50000 / 1.1), 1000]], deadlines={"3": 0}, price=1000)
    assert res["certain"] == ["3"] and res["value_mismatch"] == ["3"]
    # Erklärung mit Medaillen-Karte erst in der Stufe "alle Karten": wird gefunden, keine Ausnahme nötig
    cheap = round((17000 + 700) / 1.1)
    res = match_shipment_history(pool, [[2, cheap, 1000]], deadlines={"3": 0}, price=1000)
    assert res["certain"] == ["3"] and res["value_mismatch"] == []


def test_single_card_above_all_normals_is_hit_with_other_value():
    """24082: Box 88.000 (Versand-Hit) zählt die Seite als 120.000 (×1,1 = 132.000) - höher als jede normale Karte."""
    from utils.card_pool import explain_batch, match_shipment_history
    pool = summarize_cards([card(1, 88000, copies=3, hit=True, name="Box"), card(2, 9000, hit=True, name="Klein"),
                            card(3, 25100, copies=100, name="Coin")])
    res = match_shipment_history(pool, [[1, 120000, 1000]])
    names = {u["key"]: u["name"] for u in tracked_units(pool)}
    assert [names[k] for k in res["certain"]] == ["Box"] and res["off_value"] == [0]
    one = explain_batch(pool, 1, 120000, set())
    assert one["kind"] == "hits" and [names[k] for k in one["certain"]] == ["Box"]
    # unter der teuersten normalen Karte: keine Annahme
    assert match_shipment_history(pool, [[1, 20000, 1000]])["certain"] == []


def test_pool_keeps_card_number_and_rarity():
    pool = summarize_cards([
        {"id": 1, "name": "Mega Charizard Xex", "buy_point": 171160, "duplication": 1, "action_type": 2,
         "model_number": "M2110-080", "rarity": "SAR"},
        {"id": 2, "name": "Mew LV.23", "buy_point": 921360, "duplication": 1, "action_type": 2,
         "model_number": "Old Back Old Back", "rarity": "★"},
        {"id": 3, "name": "Normal", "buy_point": 300, "duplication": 5, "action_type": 0},
    ])
    by_id = {c["id"]: c for c in pool["cards"]}
    assert by_id["1"]["model"] == "M2110-080" and by_id["1"]["rarity"] == "SAR"
    assert by_id["2"]["model"] == "Old Back"          # doppelt geliefert -> einmal
    assert "model" not in by_id["3"] and "rarity" not in by_id["3"]   # nichts geliefert -> nicht gespeichert
    assert {h["id"]: h.get("model") for h in pool["hits"]} == {"2": "Old Back", "1": "M2110-080"}


def _coin_pool():
    cards = [(1, "Coin", 30000, 1), (2, "Coin", 15000, 1), (3, "Coin", 10000, 1), (4, "Coins", 5000, 1),
             (5, "Coins", 2000, 1), (6, "Coin", 1500, 195)]
    return summarize_cards([{"id": i, "name": n, "buy_point": v, "duplication": c, "action_type": 1}
                            for i, n, v, c in cards])


def test_coin_banner_hits_from_conversions():
    """Bonus-Banner nur aus Coins: alles über Packs × 1.500 sind die Aufschläge der teureren Coins."""
    from utils.card_pool import is_coin_pool, match_coin_conversions
    pool = _coin_pool()
    tiers = {k: t for t, k in tier_keys(pool).items()}
    assert is_coin_pool(pool) and not is_coin_pool(hit_pool())
    # 5 Packs, 7.500 umgewandelt: nur normale Coins
    assert match_coin_conversions(pool, 7500, 5, 5)["certain"] == []
    # 4 Packs, 38.000: 30.000 + 5.000 + 2 × 1.500 -> T1 und T4 sicher, auch wenn eine Karte noch offen sein könnte
    for n_min in (4, 3):   # auch wenn eine Karte noch nicht umgewandelt sein könnte
        assert sorted(tiers[k] for k in match_coin_conversions(pool, 38000, n_min, 4)["certain"]) == ["T1", "T4"]
    # 10 Packs, 15.500: T5 - mit mehreren noch offenen Karten nur noch ❓ (T4 oder T5)
    assert [tiers[k] for k in match_coin_conversions(pool, 15500, 10, 10)["certain"]] == ["T5"]
    unsure = match_coin_conversions(pool, 15500, 7, 10)
    assert unsure["certain"] == [] and len(unsure["groups"]) == 1
    # passt zu nichts (z. B. Karte zurückbehalten oder Fehlwert): None statt falscher Haken
    assert match_coin_conversions(pool, 1000, 5, 5) is None


def test_old_coin_pool_recognized_by_names():
    from utils.card_pool import is_coin_pool
    pool = _coin_pool()
    del pool["coin_only"]
    assert is_coin_pool(pool)



def test_mixed_coin_and_shipping_banner():
    """Coins + Versand-Hits ohne normale Karten: Umwandlung kommt nur von Coins, Versand-Hits stören nicht."""
    from utils.card_pool import is_coin_mixed_pool, match_coin_conversions
    pool = summarize_cards(
        [{"id": 1, "name": "Glurak", "buy_point": 90000, "duplication": 1, "action_type": 2},
         {"id": 2, "name": "Pikachu", "buy_point": 40000, "duplication": 1, "action_type": 2}]
        + [{"id": i, "name": "Coin", "buy_point": v, "duplication": c, "action_type": 1}
           for i, v, c in ((3, 10000, 1), (4, 3000, 2), (5, 1000, 96))])
    keys = {u["name"] + str(u["value"]): u["key"] for u in medal_units(pool)}
    assert is_coin_mixed_pool(pool) and not is_coin_mixed_pool(hit_pool())
    # 12 Packs gezogen, 0 verschickt, 2 Versand-Hits evtl. gezogen: 10-12 Coin-Karten; 21.000 = 10.000 + 11 × 1.000
    res = match_coin_conversions(pool, 21000, 10, 12)
    assert res["certain"] == [keys["Coin10000"]]
