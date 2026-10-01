import json

from utils.hot_list import hot_line, min_rank, needs_password, new_alerts, rank_entries


def entry(pid, pct, remaining=100, **extra):
    return {"pack_id": pid, "thread_id": pid * 10, "price": 1000, "pct": pct, "remaining": remaining,
            "tracked_hits": True, "hits_open": 1, "hits_total": 3, **extra}


def test_ranking_by_return_then_fewer_packs():
    ranked = rank_entries([entry(1, 90), entry(2, 150, 300), entry(3, 150, 50)] +
                          [entry(10 + i, 50) for i in range(12)])
    assert [e["pack_id"] for e in ranked[:3]] == [3, 2, 1]
    assert len(ranked) == 10


def test_hot_line_shows_key_facts():
    line = hot_line(1, entry(24149, 226.5, 61, cost_to_hit=6000, unsure=True, endspurt=True, rank="gold"),
                    "https://discord.com/channels/1/2")
    assert line == ("1. ⚡ [ID 24149](https://discord.com/channels/1/2) · 1.000 Coins · Ø **226,5 %** zurück"
                    " · 1/3 Hits offen ❓ · 61 Packs übrig · Ø 6.000 bis Hit · ab Gold")


def test_conditions():
    assert min_rank(json.dumps({"ranks": ["white", "gold"]})) is None
    assert min_rank(json.dumps({"ranks": ["gold", "black"]})) == "gold"
    assert min_rank(None) is None
    assert needs_password(json.dumps({"password": True})) and not needs_password(None)


def test_alerts_only_once_with_hysteresis():
    fresh, alerted = new_alerts([entry(1, 120), entry(2, 80)], set())
    assert [e["pack_id"] for e in fresh] == [1] and alerted == {1}
    fresh, alerted = new_alerts([entry(1, 99)], alerted)        # knapp darunter: bleibt gemeldet
    assert fresh == [] and alerted == {1}
    fresh, alerted = new_alerts([entry(1, 90)], alerted)        # deutlich darunter: wieder scharf
    assert alerted == set()
    fresh, _ = new_alerts([entry(1, 110)], alerted)
    assert [e["pack_id"] for e in fresh] == [1]
