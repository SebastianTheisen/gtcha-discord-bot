"""Daten für die Web-App: liest die Bot-Datenbank und rechnet wie der Bot (Ø Rückgabe, Hits, Status).

Nur lesend - die App schreibt nie in die Datenbank des Bots.
"""

import json
from datetime import datetime, timezone
from typing import Dict, List, Optional

import aiosqlite

from database.db import Database
from utils.banner_info import RANK_ORDER, format_conditions, format_shipping, sale_end_timestamp, to_int
from utils.card_pool import (
    card_value, estimate, explain_batch, out_of_banner_value, fmt_coins, pool_minimum, relevant_units, resolve_pulled, tier_keys,
    tracked_units,
)
from utils.hot_list import min_rank, needs_password, rank_entries

BASE_URL = "https://gtchaxonline.com"
PACK_HISTORY_LIMIT = 400


def epoch(iso: Optional[str]) -> Optional[int]:
    """Zeitstempel der Bot-DB (naive UTC-Zeit des Containers) -> Unix-Zeit."""
    if not iso:
        return None
    try:
        return int(datetime.fromisoformat(str(iso)).replace(tzinfo=timezone.utc).timestamp())
    except ValueError:
        return None


def buy_url(row: Dict) -> str:
    return row.get('detail_page_url') or f"{BASE_URL}/pack-detail?packId={row['pack_id']}"


class BannerView:
    def __init__(self, db: Database):
        self.db = db

    async def _pulled(self, thread_id: int, pack_id: int, pool: Dict) -> tuple:
        """Wie der Bot: (gezogen inkl. Stellvertreter, sicher erkannt, Medaillen-Gewinner, offene Gruppen, unsicher)."""
        medals = await self.db.get_medals(thread_id) if thread_id else {}
        state = await self.db.get_pull_tracking(pack_id)
        keys = tier_keys(pool)
        winners = {keys[t]: user for t, user in medals.items() if t in keys}
        pulled, sure, open_groups = resolve_pulled(state["pulled"], state["unsure"], set(winners))
        return pulled, sure - set(winners), winners, open_groups, state["unsure"]

    @staticmethod
    def _status(row: Dict, thread: Dict, stats: Optional[Dict], pool: Optional[Dict],
                sure: set, winners: Dict) -> str:
        if row.get('starts_at') and not row.get('start_announced'):
            return "upcoming"
        if stats:
            if stats['tracked_hits'] and stats['hits_total'] and not stats['hits_open']:
                price = to_int(row.get('price_coins')) or None
                if all(u['key'] in sure or u['key'] in winners for u in relevant_units(pool, price)):
                    return "hits_out"
            elif not stats['tracked_hits'] and not stats['open_tiers']:
                return "hits_out"
        if thread.get('endspurt_sent'):
            return "endspurt"
        return "running"

    async def summary(self, row: Dict, with_pool: bool = False) -> Dict:
        pid = row['pack_id']
        thread = await self.db.get_thread_by_banner_id(pid) or {}
        thread_id = int(thread['thread_id']) if thread.get('thread_id') and not thread.get('is_expired') else 0
        price, remaining, total = (to_int(row.get(k)) for k in ('price_coins', 'current_packs', 'total_packs'))
        pool = json.loads(row['card_pool']) if row.get('card_pool') else None
        stats, sure, winners, open_groups, unsure, pulled, held = None, set(), {}, [], [], set(), set()
        if pool and pool.get('total_count'):
            pulled, sure, winners, open_groups, unsure = await self._pulled(thread_id, pid, pool)
            # gezogene Hits mit Medaille, die noch nicht verschickt sind - auch nicht mehr im Banner
            held = set(winners) - set((await self.db.get_pull_tracking(pid))["pulled"])
            stats = estimate(pool, row.get('current_packs'), row.get('total_packs'), pulled, price or None,
                             self._out_value(row, pool, held))
        else:
            pool = None
        low = pool_minimum(pool) if pool else None
        data = {
            "id": pid, "title": row.get('title') or f"Pack {pid}", "category": row.get('category'),
            "price": price, "remaining": remaining, "total": total,
            "per_day": row.get('entries_per_day'), "image": row.get('image_url'), "buy_url": buy_url(row),
            "end": row.get('sale_end_date'), "end_ts": sale_end_timestamp(row.get('sale_end_date')),
            "starts_at": row.get('starts_at'),
            "status": self._status(row, thread, stats, pool, sure, winners),
            "ev": round(stats['ev']) if stats else None,
            "ev_pct": round(stats['ev_pct'], 1) if stats and stats.get('ev_pct') is not None else None,
            "ev_from_site": bool(stats and stats.get('data_based')),
            "hits_open": (stats['hits_open'] if stats['tracked_hits'] else len(stats['open_tiers'])) if stats else None,
            "hits_total": (stats['hits_total'] if stats['tracked_hits'] else 3) if stats else None,
            "tracked_hits": bool(stats and stats['tracked_hits']),
            "cost_to_hit": round(stats['cost_to_hit']) if stats and stats.get('cost_to_hit') else None,
            "unsure": bool(open_groups),
            "min_value": low['value'] if low else None,
            "pool_value": to_int(pool.get('total_value')) if pool else None,
            "all_packs_cost": price * total if price and total else None,
            "conditions": format_conditions(row.get('conditions')),
            "rank": min_rank(row.get('conditions')), "password": needs_password(row.get('conditions')),
            **self._rank_info(row.get('conditions')),
            "shipped": format_shipping(row.get('site_stats')),
            **self._shipping(row.get('site_stats')),
            **self._out_of_banner(row, pool, held),
            "thread_id": thread_id or None,
            **self._out(pool, sure, winners, open_groups),
        }
        if with_pool:
            data["hits"] = self._hit_list(pool, pulled, sure, winners, unsure, price) if pool else []
            data["hit_keys_detected"] = sorted(sure)
        return data

    @staticmethod
    def _out(pool: Optional[Dict], sure: set, winners: Dict, open_groups: List[Dict]) -> Dict:
        """Welche Hits schon raus sind (Versand sicher erkannt oder per Medaille gemeldet), teuerste zuerst."""
        if not pool:
            return {"out": [], "out_unsure": 0}
        out = [{"name": u["name"], "value": u["value"]} for u in tracked_units(pool)
               if u["key"] in sure or u["key"] in winners]
        return {"out": out, "out_unsure": sum(g.get("pulled", 0) for g in open_groups)}

    @staticmethod
    def _shipping(raw: Optional[str]) -> Dict:
        """Verschickte Karten; Coins als Kartenwert (die Seite zählt ohne 10 % Steuer)."""
        st = json.loads(raw) if raw else None
        if not st:
            return {"ship_cards": None, "ship_value": None, "ship_counted": None, "ship_players": None}
        return {"ship_cards": to_int(st.get("cards")), "ship_value": card_value(to_int(st.get("coins"))),
                "ship_counted": to_int(st.get("coins")), "ship_players": to_int(st.get("players"))}

    @staticmethod
    def _converted(row: Dict) -> Optional[int]:
        """Umgewandelte Coins: eigene Spalte, bei älteren Ständen decided_value - verschickt."""
        st = json.loads(row['site_stats']) if row.get('site_stats') else {}
        if row.get('converted') is not None:
            return to_int(row['converted'])
        if row.get('decided_value') is not None and st:
            return max(0, to_int(row['decided_value']) - to_int(st.get("coins")))
        return None

    @classmethod
    def _out_value(cls, row: Dict, pool: Dict, held: set) -> Optional[int]:
        st = json.loads(row['site_stats']) if row.get('site_stats') else {}
        return out_of_banner_value(pool, cls._converted(row), to_int(st.get("coins")), held)

    @classmethod
    def _out_of_banner(cls, row: Dict, pool: Optional[Dict], held: set = frozenset()) -> Dict:
        """Was schon aus dem Banner raus ist (Werte der Seite) und was rechnerisch noch drin ist.

        umgewandelt = total_kangen (eigene Spalte); ältere Stände: decided_value - verschickt.
        Eine Anzahl umgewandelter Karten liefert die Seite nicht - nur eine Obergrenze ist bekannt:
        gezogene Packs minus verschickte Karten (darin auch Karten, die noch niemand abgeholt hat).
        Für "noch drin" wird der Versand auf Kartenwert (×1,1) gerechnet; die Umwandlung zählt die Seite
        schon mit vollem Kartenwert (bestätigt an 24060). Gemeldete, noch nicht verschickte Hits zählen als raus.
        """
        empty = {"converted": None, "converted_max_cards": None, "out_total": None,
                 "left_value": None, "left_per_pack": None}
        st = json.loads(row['site_stats']) if row.get('site_stats') else {}
        shipped = to_int(st.get("coins"))
        converted = cls._converted(row)
        if converted is None:
            return empty
        remaining, total = to_int(row.get('current_packs')), to_int(row.get('total_packs'))
        drawn = max(0, total - remaining) if total else None
        max_cards = max(0, drawn - to_int(st.get("cards"))) if drawn is not None else None
        left = per_pack = None
        if pool and pool.get('total_value'):
            left = max(0, to_int(pool['total_value']) - out_of_banner_value(pool, converted, shipped, held))
            per_pack = round(left / remaining) if remaining > 0 else None
        return {"converted": converted, "converted_max_cards": max_cards, "out_total": converted + shipped,
                "left_value": left, "left_per_pack": per_pack}

    @staticmethod
    def _rank_info(conditions: Optional[str]) -> Dict:
        """Erlaubte Mitgliedsränge (für die Rauten) und Mindest-Aufladung."""
        cond = json.loads(conditions) if conditions else {}
        ranks = [r for r in RANK_ORDER if r in (cond.get("ranks") or [])]
        if not ranks or "all" in (cond.get("ranks") or []):
            ranks = list(RANK_ORDER)
        return {"ranks": ranks if conditions else [], "min_charge": to_int(cond.get("min_charge"))}

    @staticmethod
    def _hit_list(pool: Dict, pulled: set, sure: set, winners: Dict, unsure: List[Dict], price: int) -> List[Dict]:
        """Hit-Liste wie im Discord-Thread, mit Status je Karte."""
        units = relevant_units(pool, price or None) if pool.get('hits') else tracked_units(pool)
        if pool.get('hits') and not units:
            units = tracked_units(pool)[:5]
        result = []
        for rank, u in enumerate(units[:50], 1):
            key = u["key"]
            state, note = "open", None
            if key in winners:
                state, note = "pulled", "gezogen (Medaille)"
            elif any(key in g["keys"] and g["pulled"] > 0 and not set(g["keys"]) <= pulled for g in unsure):
                g = next(g for g in unsure if key in g["keys"] and g["pulled"] > 0)
                state, note = "unsure", f"{g['pulled']} von {len(g['keys'])} ähnlich teuren gezogen"
            elif key in pulled:
                state, note = "pulled", "gezogen (erkannt)" if key in sure else "gezogen"
            elif any(key in g["keys"] and g["pulled"] == 0 for g in unsure):
                state, note = "maybe", "möglicherweise gezogen"
            result.append({"rank": rank, "key": key, "name": u["name"], "value": u["value"],
                           "image": u.get("image"), "state": state, "note": note})
        return result

    async def image_urls(self) -> List[str]:
        """Alle Bilder der aktiven Banner: Banner zuerst, dann Hits, dann alle übrigen Karten."""
        rows = (await self.db.get_active_banners()).values()
        banners, hits, cards = [], [], []
        for row in rows:
            banners.append(row.get('image_url'))
            pool = json.loads(row['card_pool']) if row.get('card_pool') else {}
            hits += [h.get('image') for h in pool.get('hits') or []] + [c.get('image') for c in pool.get('top') or []]
            cards += [c.get('image') for c in pool.get('cards') or []]
        return [u for u in dict.fromkeys(banners + hits + cards) if u]

    async def all_banners(self, with_pool: bool = False) -> List[Dict]:
        rows = await self.db.get_active_banners()
        return [await self.summary(row, with_pool=with_pool) for row in rows.values()]

    @staticmethod
    def hot(banners: List[Dict]) -> List[Dict]:
        """Top 10 wie im Hot-Banner-Kanal: ziehbar, nach Ø Rückgabe."""
        candidates = [
            {**b, "pack_id": b["id"], "pct": b["ev_pct"]} for b in banners
            if b["category"] != "Bonus" and b["price"] > 0 and b["remaining"] > 0 and not b["password"]
            and b["status"] not in ("upcoming", "hits_out") and b["ev_pct"] is not None
        ]
        return rank_entries(candidates)

    async def detail(self, pack_id: int) -> Optional[Dict]:
        row = await self.db.get_banner(pack_id)
        if not row:
            return None
        data = await self.summary(row, with_pool=True)
        async with aiosqlite.connect(self.db.db_path) as conn:
            cur = await conn.execute(
                "SELECT changed_at, new_count FROM pack_history WHERE banner_id = ? ORDER BY id DESC LIMIT ?",
                (pack_id, PACK_HISTORY_LIMIT))
            history = [{"t": epoch(t), "packs": n} for t, n in reversed(await cur.fetchall())]
            cur = await conn.execute(
                "SELECT changed_at, old_cards, new_cards, old_coins, new_coins, old_players, new_players "
                "FROM shipment_history WHERE banner_id = ? ORDER BY id DESC LIMIT 100", (pack_id,))
            shipments = [{"t": epoch(r[0]), "cards": (r[2] or 0) - (r[1] or 0), "coins": (r[4] or 0) - (r[3] or 0),
                          "players": (r[6] or 0) - (r[5] or 0), "total_cards": r[2]}
                         for r in await cur.fetchall()]
        data["history"] = history
        data["shipments"] = await self._explain_shipments(row, data, shipments)
        data.update(await self._card_list(row, data))
        return data

    async def _explain_shipments(self, row: Dict, data: Dict, shipments: List[Dict]) -> List[Dict]:
        """Jeder Versandschub mit Kartenwert (×1,1) und welche Hits darin stecken - ältester zuerst
        gerechnet, damit ein schon verschickter Hit später nicht noch einmal zählt."""
        pool = json.loads(row['card_pool']) if row.get('card_pool') else None
        if not pool or not pool.get('hits'):
            for s in shipments:
                s["value"] = card_value(max(0, s["coins"]))
                s["explain"] = []
            return shipments
        _, _, winners, _, _ = await self._pulled(data.get("thread_id") or 0, row['pack_id'], pool)
        units = {u["key"]: u for u in tracked_units(pool)}
        label = lambda k: f"{units[k]['name']} ({fmt_coins(units[k]['value'])} Coins)" if k in units else k
        sent: set = set()
        for s in reversed(shipments):
            res = explain_batch(pool, s["cards"], s["coins"], sent, claimed=set(winners) - sent)
            sent |= set(res["certain"])
            s["value"], s["kind"] = res["value"], res["kind"]
            lines = [{"icon": "✅", "text": label(k)} for k in res["certain"]]
            for g in res["groups"]:
                names = " / ".join(dict.fromkeys(units[k]["name"] for k in g["keys"] if k in units))
                lines.append({"icon": "❓", "text": f"{g['pulled']} von: {names}"})
            for g in res["maybe"]:
                names = " / ".join(dict.fromkeys(units[k]["name"] for k in g["keys"] if k in units))
                lines.append({"icon": "❓", "text": f"vielleicht {names} – oder nur normale Karten"})
            hits = len(res["certain"]) + sum(g["pulled"] for g in res["groups"])
            if res["kind"] == "hits" and s["cards"] > hits:
                rest = s['cards'] - hits
                lines.append({"icon": "·", "text": f"+ {rest} normale Karte" + ("" if rest == 1 else "n")})
            elif res["kind"] == "normal":
                lines.append({"icon": "·", "text": "nur normale Karten"})
            elif res["kind"] == "too_big":
                lines.append({"icon": "·", "text": "zu viele Karten auf einmal – nicht zerlegbar"})
            elif res["kind"] == "unclear":
                lines.append({"icon": "·", "text": "keine passende Kombination"})
            s["explain"] = lines
        return shipments

    async def _card_list(self, row: Dict, data: Dict) -> Dict:
        """Alle Karten des Banners mit Exemplaren, Anteil im Pool und wie viele davon schon gezogen sind."""
        pool = json.loads(row['card_pool']) if row.get('card_pool') else None
        if not pool or not pool.get('cards') or not pool.get('total_count'):
            return {"cards": [], "share_above_price": None}
        pulled, *_ = await self._pulled(data.get("thread_id") or 0, row['pack_id'], pool)
        total, price = pool['total_count'], data.get("price") or 0
        cards = []
        for c in pool['cards']:
            cid = str(c.get("id"))
            gone = sum(1 for k in pulled if k == cid or k.startswith(cid + "#"))
            cards.append({"name": c["name"], "value": c["value"], "copies": c["copies"], "image": c.get("image"),
                          "hit": bool(c.get("hit")), "pulled": min(gone, c["copies"]),
                          "share": round(c["copies"] / total * 100, 2)})
        above = sum(c["copies"] for c in pool['cards'] if price and c["value"] >= price)
        return {"cards": cards, "share_above_price": round(above / total * 100, 1) if price else None}
