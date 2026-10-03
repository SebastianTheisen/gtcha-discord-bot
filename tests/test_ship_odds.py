"""Wahrscheinlichkeiten für ❓-Gruppen aus dem zeitlichen Ablauf und Lernen aus Bannern."""

import asyncio
import json
from datetime import datetime, timedelta

import aiosqlite

from utils import ship_odds
from utils.card_pool import summarize_cards

H = 3600
T0 = 1_790_000_000


def test_density_is_a_distribution_and_learns():
    edges = ship_odds.BUCKETS
    total = sum(ship_odds.density((edges[i] + edges[i + 1]) / 2) * (edges[i + 1] - edges[i])
                for i in range(len(edges) - 1))
    assert abs(total - 1) < 1e-9
    learned = ship_odds.counts_from([100, 120, 130, 150] * 10)    # meist 3-7 Tage
    assert ship_odds.density(100, learned) > ship_odds.density(100) * 3
    assert ship_odds.density(-1) == 0


def test_medal_just_before_shipment_vs_unknown_pull():
    moves = [(T0 + i * H, 10) for i in range(48)]                    # 2 Tage gleichmäßig verkauft
    ship_t = T0 + 48 * H
    # T3 kurz vorher per Medaille gemeldet, T2 ohne Medaille
    odds = ship_odds.group_odds(["2", "3"], 1, ship_t, {"3": ship_t - 0.3 * H}, moves, 500)
    assert abs(sum(odds.values()) - 1) < 1e-9 and odds["3"] > odds["2"]
    # gelernt: Versand meist erst Tage später -> die frische Medaille spricht kaum noch für T3
    late = ship_odds.counts_from([100] * 50)
    odds = ship_odds.group_odds(["2", "3"], 1, ship_t + 4 * 24 * H, {"3": ship_t - 0.3 * H}, moves, 500, late)
    assert odds["3"] > 0.5                                           # Medaille 4 Tage vorher passt genau
    assert ship_odds.group_odds(["2"], 1, None, {}, moves, 500) == {}
    assert ship_odds.fmt_odds({"2": 0.8, "3": 0.2}, {"2": "Gold", "3": "Katsumi"}) == "Gold ~80 % / Katsumi ~20 %"


def test_observations_from_medal_and_certain_shipment():
    pool = summarize_cards([{"id": 1, "name": "Pikachu", "buy_point": 17710, "duplication": 1, "action_type": 2},
                            {"id": 2, "name": "Normal", "buy_point": 330, "duplication": 99, "action_type": 0}])
    shipments = [{"t": T0 + 30 * H, "cards": 1, "coins": 16100}]
    assert ship_odds.observations(pool, shipments, {"1": T0}) == [30.0]
    assert ship_odds.observations(pool, shipments, {}) == []


def test_learning_job_and_app_show_odds(tmp_path, monkeypatch):
    monkeypatch.setenv("DISCORD_TOKEN", "x")
    monkeypatch.setenv("GUILD_ID", "1")

    async def run():
        from bot.learning import LearningMixin
        from database.db import Database, store_thread_id
        db = Database(str(tmp_path / "b.db"))
        await db.init()
        pool = summarize_cards([
            {"id": 2, "name": "Luffy Gold", "buy_point": 98780, "duplication": 1, "action_type": 2},
            {"id": 3, "name": "Luffy Katsumi", "buy_point": 98560, "duplication": 1, "action_type": 2},
            {"id": 6, "name": "Normal", "buy_point": 330, "duplication": 400, "action_type": 0},
            {"id": 7, "name": "Normal 2", "buy_point": 220, "duplication": 100, "action_type": 0}])
        await db.upsert_store_pack(24152, "Pack", 1200, 500, 500, None, None, None, "x")
        await db.save_card_pool(24152, pool)
        now = datetime.utcnow()
        async with aiosqlite.connect(db.db_path) as conn:
            for i in range(10):
                await conn.execute("INSERT INTO pack_history (banner_id, old_count, new_count, changed_at) "
                                   "VALUES (24152, ?, ?, ?)", (500 - i * 10, 490 - i * 10,
                                                               (now - timedelta(hours=20 - i)).isoformat()))
            await conn.execute("INSERT INTO shipment_history (banner_id, old_cards, new_cards, old_coins, new_coins, "
                               "old_players, new_players, changed_at) VALUES (24152, 0, 1, 0, 95800, 0, 1, ?)",
                               (now.isoformat(),))
            await conn.execute("INSERT INTO medals (thread_id, tier, user_id, created_at) VALUES (?, 'T2', 1, ?)",
                               (store_thread_id(24152), (now - timedelta(minutes=16)).isoformat()))
            await conn.commit()

        class Bot(LearningMixin):
            def __init__(self):
                self.db = db

        await Bot()._learn_ship_odds()
        assert json.loads(await db.get_meta("ship_delay_counts"))["cases"] == 0   # läuft noch: kein Fall

        from webapp.view import BannerView
        detail = await BannerView(db).detail(24152)
        text = " ".join(line["text"] for s in detail["shipments"] for line in s["explain"])
        assert "~" in text and "%" in text, text                    # ❓ mit Wahrscheinlichkeit

        hits = {h["tier"]: h for h in detail["hits"]}
        assert hits["T2"]["origin"]["via"] == "discord" and hits["T2"]["origin"]["at"]   # Medaille mit Zeit
        await db.mark_banner_inactive(24152)
        await Bot()._learn_ship_odds()
        case = (await db.get_cases())[24152]
        assert case["shipments"] and case["medals"] and "observations" in case

    asyncio.run(run())
