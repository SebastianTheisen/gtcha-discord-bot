"""Coin-Banner Schub für Schub: Packs im Fenster zwischen zwei Läufen der Seite gegen die Umwandlung im Lauf."""

from utils.card_pool import summarize_cards, tier_keys
from utils.coin_history import coin_intervals, match_coin_history


def _pool():
    cards = [(1, 30000, 1), (2, 15000, 1), (3, 10000, 1), (4, 5000, 1), (5, 2000, 1), (6, 1500, 195)]
    return summarize_cards([{"id": i, "name": "Coin", "buy_point": v, "duplication": c, "action_type": 1}
                            for i, v, c in cards])


def _moves(times, start=200):
    out, n = [], start
    for t in times:
        out.append((f"2026-10-07T{t}:00", n, n - 1))
        n -= 1
    return out


def test_intervals_follow_site_runs():
    moves = _moves(["17:03", "17:06", "17:19", "17:24", "17:27", "17:38", "17:50", "17:52"])
    conv = [("2026-10-07T17:30:21", 0, 7500), ("2026-10-07T18:00:26", 7500, 12000)]
    iv = coin_intervals(conv, moves, 200, 192)
    assert [(i["drawn"], i["coins"]) for i in iv] == [(5, 7500), (3, 4500)]


def test_exact_windows_and_one_with_remainder():
    pool = _pool()
    tiers = {k: t for t, k in tier_keys(pool).items()}
    iv = [{"drawn": 5, "coins": 7500, "t": 0}, {"drawn": 3, "coins": 4500, "t": 1}, {"drawn": 2, "coins": 3500, "t": 2}]
    res = match_coin_history(pool, iv)
    assert [tiers[k] for k in res["certain"]] == ["T5"] and not res["groups"]
    # 4 Packs, 38.000 in einem Schub: 30.000 + 5.000 + 2 × 1.500
    res = match_coin_history(pool, [{"drawn": 4, "coins": 38000, "t": 0}])
    assert sorted(tiers[k] for k in res["certain"]) == ["T1", "T4"]


def test_pull_at_boundary_is_merged_with_next_window():
    """Zug kurz vor dem Lauf erst danach erkannt: Fenster passt allein nicht, zusammen mit dem nächsten schon."""
    pool = _pool()
    iv = [{"drawn": 3, "coins": 1500, "t": 0}, {"drawn": 1, "coins": 4500, "t": 1}]
    res = match_coin_history(pool, iv)
    assert res["certain"] == [] and res["used"] == 1 and res["skipped"] == 0


def test_mixed_banner_window_limits_hits():
    """Gemischte Banner: in einem Fenster mit 2 Packs können höchstens 2 Versand-Hits stecken."""
    pool = _pool()
    tiers = {k: t for t, k in tier_keys(pool).items()}
    # 2 Packs, 2.000 umgewandelt: entweder der 2.000er (+ 1 Versand-Hit) - mit 1.500ern geht 2.000 nicht auf
    res = match_coin_history(pool, [{"drawn": 2, "coins": 2000, "t": 0}], hits_possible=5)
    assert [tiers[k] for k in res["certain"]] == ["T5"]
