"""Riesiger Banner wie 24164 (284 Versand-Hits): die Versand-Auswertung wird fertig (früher lief sie endlos)."""

import random
import time

from utils import card_pool as cp


def _big_banner(seed: int):
    rnd = random.Random(seed)
    hits = [{"id": f"h{i}", "name": f"Hit {i}", "value": int(rnd.choice([rnd.uniform(1500, 6000), rnd.uniform(6000, 30000),
                                                                         rnd.uniform(30000, 300000)]))}
            for i in range(284)]
    normal = {str(int(rnd.uniform(200, 3000))): rnd.randint(10, 60) for _ in range(110)}
    pool = {"version": 2, "total_count": 5000, "hits": hits, "normal_values": normal, "cards": []}
    units = [u for u in cp.tracked_units(pool) if u["shipping_only"]]
    batches = []
    for _ in range(12):
        n = rnd.randint(1, 10)
        picked = rnd.sample(units, rnd.randint(0, min(3, n)))
        val = sum(u["value"] for u in picked) + sum(int(rnd.choice(list(normal))) for _ in range(n - len(picked)))
        batches.append([n, round(val / cp.TAX_FACTOR), None])
    return pool, batches


def test_huge_banner_shipment_history_finishes():
    pool, batches = _big_banner(1)
    started = time.monotonic()
    result = cp.match_shipment_history(pool, batches, cp.VALUE_TOLERANCE, {}, 3000)
    assert time.monotonic() - started < 60   # lokal ~7 s; vorher nach Stunden nicht fertig
    assert "certain" in result


def test_single_huge_batch_is_quick():
    pool, _ = _big_banner(1)
    classes = cp._value_classes([u for u in cp.tracked_units(pool) if u["shipping_only"]], cp.VALUE_TOLERANCE)
    started = time.monotonic()
    cp._batch_options(pool, classes, 8, 226745, cp.VALUE_TOLERANCE)
    assert time.monotonic() - started < 30   # lokal ~3 s; vorher > 10 s und unbegrenzt


def test_cheap_shipping_cards_count_as_normal_cards():
    """Wie 24164: 26 echte Versand-Hits, dazu hunderte günstige Versand-Karten unter dem Packpreis. Die günstigen
    zählen wie normale Karten - Rechnung in Sekunden statt Minuten, alle Schübe auswertbar, Hit erkannt."""
    rnd = random.Random(7)
    cards = [{"id": f"big{i}", "name": "x", "buy_point": int(rnd.uniform(49_000, 400_000)),
              "duplication": rnd.choice([1, 1, 3]), "action_type": cp.HIT_ACTION_TYPE} for i in range(12)]
    cards += [{"id": f"cheap{i}", "name": "x", "buy_point": int(rnd.uniform(300, 2_100)),
               "duplication": rnd.randint(1, 4), "action_type": cp.HIT_ACTION_TYPE} for i in range(110)]
    cards += [{"id": f"n{i}", "name": "x", "buy_point": int(rnd.uniform(200, 3_000)),
               "duplication": rnd.randint(10, 60), "action_type": 0} for i in range(100)]
    pool = cp.summarize_cards(cards)
    big = next(u for u in cp.tracked_units(pool) if u["key"].startswith("big0"))
    # ein Schub: genau dieser Hit plus zwei normale Karten
    normal = sorted(int(v) for v in pool["normal_values"])[:2]
    batches = [[3, round((big["value"] + sum(normal)) / cp.TAX_FACTOR), 1_790_000_000]]
    started = time.monotonic()
    result = cp.match_shipment_history(pool, batches, cp.VALUE_TOLERANCE, {}, 2222)
    assert time.monotonic() - started < 5
    assert result["used_batches"] == 1
    assert not any(k.startswith("cheap") for k in result["certain"])   # günstige werden nicht einzeln verfolgt
    pool_for_ship = cp.shipping_pool(pool, 2222)
    assert all(h["value"] >= 2222 for h in pool_for_ship["hits"])
    assert sum(pool_for_ship["normal_values"].values()) > sum(pool["normal_values"].values())
