"""Lernen aus komplett mitgeschnittenen Bannern (siehe utils/ship_odds.py).

Alle 5 Minuten: für laufende und beendete Banner die Beobachtungen "Hit mit Medaille -> sicher erkannter
Versand" sammeln. Beendete Banner werden als Lernfall gespeichert (banner_cases, bleibt auch nach dem Archiv),
daraus entsteht die Verteilung "Zeit vom Zug bis zum Versand" (bot_meta ship_delay_counts). Die App rechnet
damit die Wahrscheinlichkeiten in ❓-Gruppen.
"""

from bot.common import *  # noqa: F401,F403
from utils import ship_odds


class LearningMixin:
    async def _banner_case(self, pid: int, row: dict):
        pool = json.loads(row['card_pool']) if row.get('card_pool') else None
        if not pool or not pool.get('hits'):
            return None
        inputs = await self.db.odds_inputs(pid)
        keys = tier_keys(pool)
        medal_t = {keys[t]: ts for t, ts in inputs["medals"].items() if t in keys}
        state = await self.db.get_pull_tracking(pid)
        return {
            # Rechnen im Hintergrund-Thread (das Auftrags-Modell kann bei großen Schüben etwas dauern)
            "observations": await asyncio.to_thread(ship_odds.observations, pool, inputs["shipments"], medal_t),
            "shipments": inputs["shipments"], "medals": inputs["medals"], "moves_count": len(inputs["moves"]),
            "total_packs": row.get('total_packs'), "remaining_at_end": row.get('current_packs'),
            "hits": [{"key": u["key"], "value": u["value"], "name": u["name"]} for u in tracked_units(pool)
                     if u.get("shipping_only")],
            "pulled": list(state["pulled"]), "unsure": list(state["unsure"]),
        }

    async def _learn_ship_odds(self):
        try:
            cases = await self.db.get_cases()
            ended = await self.db.get_ended_banners()
            for pid, row in ended.items():
                case = await self._banner_case(pid, row)
                if case is not None:
                    await self.db.save_case(pid, row.get('updated_at'), case)
                    cases[pid] = case
            delays = [h for case in cases.values() for h in case.get("observations") or []]
            running = {**await self.db.get_active_banners(), **await self.db.get_store_banners()}
            for pid, row in running.items():
                case = await self._banner_case(pid, row)
                if case:
                    delays += case["observations"]
            counts = ship_odds.counts_from(delays)
            old = await self.db.get_meta("ship_delay_counts")
            new = json.dumps({"counts": counts, "n": len(delays), "cases": len(cases)})
            if old != new:
                await self.db.set_meta("ship_delay_counts", new)
                median = sorted(delays)[len(delays) // 2] if delays else None
                logger.info(f"[LERNEN] {len(cases)} Fälle, {len(delays)} Versand-Beobachtungen"
                            + (f", Median {median:.1f} Std vom Zug bis zum Versand" if median is not None else ""))
        except Exception as e:
            logger.warning(f"[LERNEN] fehlgeschlagen: {type(e).__name__}: {e}")
