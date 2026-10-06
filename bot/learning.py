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
            "observations": await asyncio.to_thread(ship_odds.observations, pool, inputs["shipments"], medal_t,
                                                  _int(row.get('price_coins')) or None),
            "shipments": inputs["shipments"], "medals": inputs["medals"], "moves_count": len(inputs["moves"]),
            "total_packs": row.get('total_packs'), "remaining_at_end": row.get('current_packs'),
            "hits": [{"key": u["key"], "value": u["value"], "name": u["name"]} for u in tracked_units(pool)
                     if u.get("shipping_only")],
            "pulled": list(state["pulled"]), "unsure": list(state["unsure"]),
        }

    async def _reevaluate_ended(self, ended: dict, limit: int = 10) -> int:
        """Beendete Banner neu auswerten, deren Versand-Schübe nach einer neuen Auswertungs-Version fehlen (laufende
        erledigt der normale Abruf; beendete kommen dort nicht mehr vor). Ohne Discord."""
        done = 0
        for pid, row in ended.items():
            if done >= limit or row.get('ship_batches') or not row.get('card_pool') or not row.get('site_stats'):
                continue
            pool = json.loads(row['card_pool'])
            if not pool.get('hits'):
                continue
            st = json.loads(row['site_stats'])
            count, coins = _int(st.get("cards")), _int(st.get("coins"))
            if not count:
                continue
            batches = await self.db.rebuild_ship_batches(pid, count, coins)
            if row.get('category') == 'Store':
                medal_thread = store_thread_id(pid)
            else:
                thread = await self.db.get_thread_by_banner_id(pid) or {}
                medal_thread = int(thread['thread_id']) if thread.get('thread_id') else 0
            keys = tier_keys(pool)
            medal_t = {keys[t]: m["at"] for t, m in (await self.db.medal_rows(medal_thread)).items()
                       if t in keys and m.get("at") and m["source"] != "admin"} if medal_thread else {}
            deadlines = batch_deadlines(batches, medal_t)
            joint = await asyncio.to_thread(match_shipment_history, pool, batches, VALUE_TOLERANCE, deadlines,
                                            _int(row.get('price_coins')) or None)
            rejected = await self.db.get_rejects(pid)
            certain = [k for k in joint["certain"] if k not in rejected]
            await self.db.set_pull_tracking(pid, row.get('decided_value'), count, coins, certain, joint["groups"],
                                            batches)
            done += 1
        if done:
            logger.info(f"[LERNEN] {done} beendete Banner neu ausgewertet")
        return done

    async def _learn_ship_odds(self):
        try:
            cases = await self.db.get_cases()
            ended = await self.db.get_ended_banners()
            if await self._reevaluate_ended(ended):
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
