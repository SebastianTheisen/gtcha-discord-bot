"""Daten für die Web-App: liest die Bot-Datenbank und rechnet wie der Bot (Ø Rückgabe, Hits, Status).

Nur lesend - die App schreibt nie in die Datenbank des Bots.
"""

import asyncio
import json
from datetime import datetime, timedelta, timezone
from typing import Dict, List, Optional

import aiosqlite

from database.db import STORE, Database, store_thread_id
from utils.banner_info import RANK_ORDER, format_conditions, format_shipping, sale_end_timestamp, to_int
from utils.card_pool import (
    batch_deadlines, card_value, estimate, explain_batch, out_of_banner_value, fmt_coins, pool_minimum, relevant_units, resolve_pulled, tier_keys,
    tracked_units, claimable_units, medal_units,
)
from utils import ship_odds
from utils.translate import set_cache as set_translations, to_german
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


def pack_timeline(moves: List[tuple], converts: List[tuple], shipments: List[Dict], limit: int = 800) -> List[Dict]:
    """Pack-Verlauf wie in Discord: jedes Pack-Update einzeln, dazwischen jeder Lauf der Seite, in dem Versand
    oder Umwandlung gezählt wurden - mit Coins und dem Pack-Stand zu dem Zeitpunkt.

    moves: [(t, alt, neu)], converts: [(t, umgewandelt)], shipments: Versandschübe (mit t, cards, value, players,
    explain) - Zeiten als Unix-Sekunden. Neueste zuerst: [{"kind": "pack"|"out", "t", ...}]."""
    outs: List[Dict] = []
    events = [(t, "conv", c) for t, c in converts if t and c > 0] + [(s["t"], "ship", s) for s in shipments if s.get("t")]
    for t, kind, v in sorted(events, key=lambda e: e[0]):
        if not outs or t - outs[-1]["t"] > 120:   # Umwandlung und Versand desselben Laufs zusammen
            outs.append({"kind": "out", "t": t, "converted": 0, "ship_cards": 0, "ship_value": 0, "players": 0,
                         "explain": []})
        o = outs[-1]
        if kind == "conv":
            o["converted"] += v
        else:
            o["ship_cards"] += v.get("cards") or 0
            o["ship_value"] += v.get("value") or 0
            o["players"] += v.get("players") or 0
            o["explain"] += [l for l in v.get("explain") or [] if l.get("icon") in ("✅", "❓")]
    packs = [{"kind": "pack", "t": t, "old": old, "new": new} for t, old, new in moves if t and new < old]
    timeline = sorted(packs + outs, key=lambda e: (e["t"], e["kind"] == "out"))
    current = None
    for e in timeline:   # Pack-Stand beim Lauf der Seite
        if e["kind"] == "pack":
            current = e["new"]
        else:
            e["packs"] = current
    return list(reversed(timeline))[:limit]


def buy_url(row: Dict) -> str:
    return row.get('detail_page_url') or f"{BASE_URL}/pack-detail?packId={row['pack_id']}"


def banner_label(title, best_hit, category, price) -> str:
    """Lesbarer Name, auch wenn der Banner keinen Titel hat (sonst nur "Banner 24114")."""
    if title:
        return to_german(title)
    if best_hit:
        return to_german(best_hit)
    return " · ".join(x for x in (category, f"{fmt_coins(to_int(price))} Coins" if to_int(price) else None) if x) or ""


def _history_banners(rows, archived) -> Dict[int, Dict]:
    banners = {}
    for pid, title, category, best_hit, price, image, created, ended, values, ids in archived:
        try:
            values, ids = json.loads(values or "[]"), json.loads(ids or "[]")
        except ValueError:
            values, ids = [], []
        # ältere Einträge: Liste der Werte / Liste der IDs; neuere: {"values", "avg"} / {ID: Wert}
        avg = values.get("avg") if isinstance(values, dict) else None
        values = set(values.get("values") or []) if isinstance(values, dict) else set(values)
        card_values = ids if isinstance(ids, dict) else {}
        banners[pid] = {"price": to_int(price), "title": banner_label(title, best_hit, category, price), "image": image,
                        "active": False, "created": (created or "")[:10], "values": values, "card_ids": set(ids),
                        "card_values": card_values, "avg": avg}
    for pid, title, category, best_hit, price, image, active, created, pool_json in rows:
        try:
            pool = json.loads(pool_json) if pool_json else {}
        except ValueError:
            pool = {}
        cards = (pool.get("cards") or []) + (pool.get("hits") or [])
        total, count = pool.get("total_value"), pool.get("total_count")
        banners[pid] = {"price": to_int(price), "title": banner_label(title, best_hit, category, price), "image": image,
                        "active": bool(active), "created": (created or "")[:10],
                        "values": {int(c["value"]) for c in pool.get("cards") or [] if c.get("value")},
                        "card_ids": {str(c.get("id")) for c in cards if c.get("id") is not None},
                        "card_values": {str(c.get("id")): to_int(c.get("value")) for c in cards if c.get("id") is not None},
                        "avg": round(to_int(total) / to_int(count)) if total and count else None}
    return banners


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

    async def _refresh_translations(self) -> None:
        """Übersetzungen des Bots (DeepL) höchstens jede Minute neu laden."""
        now = asyncio.get_running_loop().time()
        if now - getattr(self, "_translations_at", -1e9) < 60:
            return
        self._translations_at = now
        set_translations(await self.db.get_translations())
        try:   # gelernte Zeiten vom Zug bis zum Versand (Bot, bot/learning.py)
            self._ship_delays = json.loads(await self.db.get_meta("ship_delay_counts") or "{}").get("counts")
        except (ValueError, aiosqlite.OperationalError):
            self._ship_delays = None

    async def summary(self, row: Dict, with_pool: bool = False) -> Dict:
        pid = row['pack_id']
        await self._refresh_translations()
        thread = await self.db.get_thread_by_banner_id(pid) or {}
        ended = row.get('is_active') == 0   # Archiv: Discord-Thread ist gelöscht, Medaillen bleiben gespeichert
        thread_id = (int(thread['thread_id']) if thread.get('thread_id') and (ended or not thread.get('is_expired'))
                     else 0)
        store = row.get('is_active') == STORE or (ended and row.get('category') == 'Store')
        medal_thread = store_thread_id(pid) if store else thread_id   # Store-Pack: Medaillen ohne Discord-Thread
        price, remaining, total = (to_int(row.get(k)) for k in ('price_coins', 'current_packs', 'total_packs'))
        pool = json.loads(row['card_pool']) if row.get('card_pool') else None
        stats, sure, winners, open_groups, unsure, pulled, held = None, set(), {}, [], [], set(), set()
        shipped_keys = set()
        if pool and pool.get('total_count'):
            pulled, sure, winners, open_groups, unsure = await self._pulled(medal_thread, pid, pool)
            # gezogene Hits mit Medaille, die noch nicht verschickt sind - auch nicht mehr im Banner
            shipped_keys = set((await self.db.get_pull_tracking(pid))["pulled"])
            held = set(winners) - shipped_keys
            stats = estimate(pool, row.get('current_packs'), row.get('total_packs'), pulled, price or None,
                             self._out_value(row, pool, held, shipped_keys))
        else:
            pool = None
        low = pool_minimum(pool) if pool else None
        data = {
            "headline": to_german(row.get('title') or row.get('best_hit')) or "",   # echter Name für die Anzeige
            "id": pid, "title": banner_label(row.get('title'), row.get('best_hit'), row.get('category'),
                                                     row.get('price_coins')) or f"Pack {pid}", "category": row.get('category'),
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
            **self._out_of_banner(row, pool, held, shipped_keys),
            "thread_id": thread_id or None,
            "medal_thread": medal_thread or None,
            **self._out(pool, sure, winners, open_groups),
        }
        if pool and remaining == 0 and total and data["hits_open"] is None:
            # leer gezogen: keine Schätzung mehr nötig - alle Hits sind raus
            data["tracked_hits"] = bool(pool.get('hits'))
            data["hits_total"] = (len(relevant_units(pool, price or None)) if pool.get('hits')
                                  else pool.get('hits_total') or 0)
            data["hits_open"] = 0
        if store:
            data["store"] = True   # wie ein normaler Banner, nur ohne Discord
        if ended:
            data["archived"], data["status"], data["ended_at"] = True, "ended", epoch(row.get('updated_at'))
            data["thread_id"] = None   # Thread gibt es nicht mehr
        if with_pool:
            data["hits"] = self._hit_list(pool, pulled, sure, winners, unsure, price) if pool else []
            data["hit_keys_detected"] = sorted(sure)
            # für Kartensuche und Wunschliste: alle Karten (ID -> Name, Wert, Bild, Exemplare) und sicher gezogene
            data["cards_brief"] = {str(c.get("id")): [c.get("name"), to_int(c.get("value")), c.get("image"),
                                                      to_int(c.get("copies")) or 1]
                                   for c in (pool.get("cards") or []) + (pool.get("hits") or [])
                                   if c.get("id") is not None} if pool else {}
            data["out_ids"] = sorted({str(k).split("#")[0] for k in set(sure) | set(winners)})
        return data

    @staticmethod
    def _out(pool: Optional[Dict], sure: set, winners: Dict, open_groups: List[Dict]) -> Dict:
        """Welche Hits schon raus sind (Versand sicher erkannt oder per Medaille gemeldet), teuerste zuerst."""
        if not pool:
            return {"out": [], "out_unsure": 0}
        out = [{"name": u["name"], "value": u["value"], "via": "medaille" if u["key"] in winners else "versand"}
               for u in medal_units(pool) if u["key"] in sure or u["key"] in winners]
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
    def _out_value(cls, row: Dict, pool: Dict, held: set, shipped_keys: set = frozenset()) -> Optional[int]:
        st = json.loads(row['site_stats']) if row.get('site_stats') else {}
        return out_of_banner_value(pool, cls._converted(row), to_int(st.get("coins")), held, shipped_keys)

    @classmethod
    def _out_of_banner(cls, row: Dict, pool: Optional[Dict], held: set = frozenset(),
                       shipped_keys: set = frozenset()) -> Dict:
        """Was schon aus dem Banner raus ist (Werte der Seite) und was rechnerisch noch drin ist.

        umgewandelt = total_kangen (eigene Spalte); ältere Stände: decided_value - verschickt.
        Eine Anzahl umgewandelter Karten liefert die Seite nicht - nur eine Obergrenze ist bekannt:
        gezogene Packs minus verschickte Karten (darin auch Karten, die noch niemand abgeholt hat).
        Für "noch drin" wird der Versand auf Kartenwert (×1,1) gerechnet; die Umwandlung zählt die Seite
        schon mit vollem Kartenwert (bestätigt an 24060). Gemeldete, noch nicht verschickte Hits zählen als raus.
        """
        empty = {"converted": None, "converted_max_cards": None, "out_total": None,
                 "left_value": None, "left_per_pack": None, "undecided": None}
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
            left = max(0, to_int(pool['total_value'])
                       - out_of_banner_value(pool, converted, shipped, held, shipped_keys))
            per_pack = round(left / remaining) if remaining > 0 else None
        # Kartenwert: umgewandelt zählt die Seite voll, verschickt ohne 10 % Steuer (×1,1 = Kartenwert)
        decided = converted + card_value(shipped)
        undecided = None
        if remaining == 0 and total and pool and pool.get('total_value'):
            # leer gezogen: alle Karten sind raus - was weder verschickt noch umgewandelt ist, liegt noch
            # gezogen bei den Spielern (die Seite zählt es erst, wenn sie sich entscheiden)
            undecided = max(0, to_int(pool['total_value']) - decided)
            left = per_pack = None
        return {"converted": converted, "converted_max_cards": max_cards, "out_total": decided,
                "left_value": left, "left_per_pack": per_pack, "undecided": undecided}

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
        units = claimable_units(pool, price or None)
        if not pool.get('hits'):
            units = max(units, tracked_units(pool), key=len)
        if pool.get('hits') and not units:
            units = tracked_units(pool)[:5]
        result = []
        for rank, u in enumerate(units[:50], 1):
            tier = u.get("tier") or f"T{rank}"
            key = u["key"]
            state, note = "open", None
            medal_user = winners.get(key)
            if key in winners:
                state, note = "pulled", "gezogen (Medaille)"
            elif any(key in g["keys"] and g["pulled"] > 0 and not set(g["keys"]) <= pulled for g in unsure):
                g = next(g for g in unsure if key in g["keys"] and g["pulled"] > 0)
                state, note = "unsure", f"{g['pulled']} von {len(g['keys'])} ähnlich teuren gezogen"
            elif key in pulled:
                state, note = "pulled", "gezogen (erkannt)" if key in sure else "gezogen"
            elif any(key in g["keys"] and g["pulled"] == 0 for g in unsure):
                state, note = "maybe", "möglicherweise gezogen"
            result.append({"rank": int(tier[1:]), "tier": tier, "key": key, "name": u["name"], "value": u["value"],
                           "image": u.get("image"), "state": state, "note": note,
                           # Medaille ohne bekannte Person (ältere T1-T3-Markierung) = "0"
                           "medal_user": str(medal_user) if key in winners else None})
        return result

    async def image_urls(self) -> List[str]:
        """Alle Bilder der aktiven Banner: Banner zuerst, dann Hits, dann alle übrigen Karten."""
        rows = list((await self.db.get_active_banners()).values()) + list((await self.db.get_store_banners()).values())
        banners, hits, cards = [], [], []
        # Archiv: Bannerbild und Hits behalten (übrige Karten lädt die Detailseite bei Bedarf)
        for row in (await self.db.get_ended_banners()).values():
            pool = json.loads(row['card_pool']) if row.get('card_pool') else {}
            banners.append(row.get('image_url'))
            hits += [h.get('image') for h in pool.get('hits') or []]
        for row in rows:
            banners.append(row.get('image_url'))
            pool = json.loads(row['card_pool']) if row.get('card_pool') else {}
            hits += [h.get('image') for h in pool.get('hits') or []] + [c.get('image') for c in pool.get('top') or []]
            cards += [c.get('image') for c in pool.get('cards') or []]
        return [u for u in dict.fromkeys(banners + hits + cards) if u]

    async def my_medals(self, user_id: str) -> List[Dict]:
        """Alle Medaillen eines Discord-Nutzers bei aktiven Bannern, mit Karte (neueste zuerst)."""
        async with aiosqlite.connect(self.db.db_path) as conn:
            cur = await conn.execute(
                "SELECT m.tier, t.banner_id, m.created_at FROM medals m "
                "JOIN discord_threads t ON t.thread_id = m.thread_id "
                "WHERE m.user_id = ? AND t.is_expired = 0 "
                "UNION ALL "   # Store-Packs: interne Nummer = -pack_id, nur solange der Pack läuft
                "SELECT m.tier, b.pack_id, m.created_at FROM medals m JOIN banners b ON b.pack_id = -m.thread_id "
                "WHERE m.user_id = ? AND m.thread_id < 0 AND b.is_active = ? "
                "ORDER BY 3 DESC", (int(user_id), int(user_id), STORE))
            rows = await cur.fetchall()
        result = []
        for tier, banner_id, created in rows:
            row = await self.db.get_banner(banner_id) or {}
            pool = json.loads(row['card_pool']) if row.get('card_pool') else {}
            unit = next((u for u in medal_units(pool) if u["tier"] == tier), {}) if pool else {}
            result.append({"banner_id": banner_id, "tier": tier, "name": unit.get("name") or tier,
                           "value": unit.get("value"), "image": unit.get("image"),
                           "price": to_int(row.get("price_coins")), "t": epoch(created),
                           "title": banner_label(row.get('title'), row.get('best_hit'), row.get('category'),
                                                 row.get('price_coins'))})
        return result

    async def history_context(self, since_utc: Optional[datetime]) -> tuple:
        """Für den eigenen Verlauf: alle bekannten Banner (Preis, Kartenwerte, Titel) und ihre
        Pack-Bewegungen (naive UTC-Zeiten) seit since_utc."""
        banners, moves = {}, {}
        async with aiosqlite.connect(self.db.db_path) as conn:
            cur = await conn.execute("SELECT pack_id, title, category, best_hit, price_coins, image_url, is_active, "
                                     "created_at, card_pool FROM banners")
            rows = await cur.fetchall()
            try:
                cur = await conn.execute("SELECT pack_id, title, category, best_hit, price_coins, image_url, created_at, "
                                         "ended_at, card_values, card_ids FROM banner_archive")
                archived = await cur.fetchall()
            except aiosqlite.OperationalError:   # Bot noch nicht aktualisiert
                archived = []
            # Kartenpools aller Banner einlesen dauert - außerhalb der Ereignisschleife
            banners = await asyncio.get_running_loop().run_in_executor(None, _history_banners, rows, archived)
            if since_utc:
                cur = await conn.execute("SELECT banner_id, changed_at FROM pack_history "
                                         "WHERE new_count < old_count AND changed_at >= ?",
                                         ((since_utc - timedelta(minutes=10)).isoformat(),))
                for pid, changed in await cur.fetchall():
                    try:
                        moves.setdefault(pid, []).append(datetime.fromisoformat(changed))
                    except ValueError:
                        continue
        return banners, moves

    async def claim_targets(self) -> Dict[int, Dict]:
        """Aktive Banner: meldbare Plätze (Karten ab Packpreis) und schon vergebene Medaillen."""
        result = {}
        rows = {**await self.db.get_active_banners(), **await self.db.get_store_banners()}
        for pid, row in rows.items():
            if not row.get('card_pool'):
                continue
            pool = json.loads(row['card_pool'])
            if row.get('is_active') == STORE:
                medals = await self.db.get_medals(store_thread_id(pid))
            else:
                thread = await self.db.get_thread_by_banner_id(pid) or {}
                if not thread.get('thread_id') or thread.get('is_expired'):
                    continue
                medals = await self.db.get_medals(int(thread['thread_id']))
            result[pid] = {"units": claimable_units(pool, to_int(row.get('price_coins')) or None),
                           "medals": medals}
        return result

    async def all_banners(self, with_pool: bool = False) -> List[Dict]:
        rows = list((await self.db.get_active_banners()).values()) + list((await self.db.get_store_banners()).values())
        return [await self.summary(row, with_pool=with_pool) for row in rows]

    async def archived_banners(self) -> List[Dict]:
        """Beendete Banner der letzten 30 Tage (Kategorie Archiv), zuletzt beendete zuerst."""
        return [await self.summary(row) for row in (await self.db.get_ended_banners()).values()]

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
        async with aiosqlite.connect(self.db.db_path) as conn:
            cur = await conn.execute("SELECT changed_at, old_count, new_count FROM pack_history WHERE banner_id = ? "
                                     "ORDER BY id DESC LIMIT 2000", (pack_id,))
            moves = [(epoch(t), o, n) for t, o, n in await cur.fetchall()]
            cur = await conn.execute("SELECT changed_at, old_coins, new_coins FROM convert_history WHERE banner_id = ? "
                                     "ORDER BY id DESC LIMIT 500", (pack_id,))
            converts = [(epoch(t), (n or 0) - (o or 0)) for t, o, n in await cur.fetchall()]
        data["pack_timeline"] = pack_timeline(moves, converts, data["shipments"])
        odds, shipped = data.pop("_key_odds", {}), data.pop("_key_ship", {})
        medals = await self.db.medal_rows(data.get("medal_thread") or 0)
        for h in data.get("hits") or []:
            # ❓ in der Hit-Liste: wie wahrscheinlich ist dieser Hit raus?
            if h.get("state") == "unsure" and h.get("key") in odds:
                h["note"] = f"{h['note']} · wohl ~{round(odds[h['key']] * 100)} %"
                h["odds"] = round(odds[h["key"]] * 100)
            # Herkunft: wer, wann, wie (Medaille aus Discord/App - Lesezeichen ergänzt der Server - oder Versand)
            m = medals.get(h["tier"]) if h.get("medal_user") is not None else None
            if m:
                h["origin"] = {"via": m["source"], "user": str(m["user_id"]), "at": m["at"]}
                if m["source"] == "admin":
                    h["note"] = "durch Admin abgehakt"
            elif h.get("state") in ("pulled", "unsure") and h.get("key") in shipped:
                h["origin"] = {"via": "versand", "shipped_at": shipped[h["key"]]}
        data.update(await self._card_list(row, data, odds))
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
        _, _, winners, _, _ = await self._pulled(data.get("medal_thread") or 0, row['pack_id'], pool)
        units = {u["key"]: u for u in tracked_units(pool)}
        # Wahrscheinlichkeiten für ❓-Gruppen aus dem zeitlichen Ablauf (utils/ship_odds.py)
        inputs = await self.db.odds_inputs(row['pack_id'])
        keys = tier_keys(pool)
        medal_t = {keys[t]: ts for t, ts in inputs["medals"].items() if t in keys}
        total = to_int(row.get('total_packs')) or pool.get('total_count') or 0
        learned = getattr(self, "_ship_delays", None)
        key_odds: Dict[str, float] = {}
        key_ship: Dict[str, int] = {}   # Hit -> Zeit des Versands, in dem er (sicher oder ❓) steckt
        label = lambda k: f"{units[k]['name']} ({fmt_coins(units[k]['value'])} Coins)" if k in units else k
        sent: set = set()
        # Medaille gesetzt = Versand angefordert: Hit steckt spätestens im ersten Schub danach
        ordered = list(reversed(shipments))
        due = batch_deadlines([[s["cards"], s["coins"], s.get("t")] for s in ordered], medal_t)
        for i, s in enumerate(ordered):
            required = {k for k, j in due.items() if j == i and k not in sent}   # Medaille: erster Schub danach
            res = explain_batch(pool, s["cards"], s["coins"], sent, required=required,
                                price=to_int(row.get('price_coins')) or None)
            sent |= set(res["certain"])
            s["value"], s["kind"] = res["value"], res["kind"]
            lines = [{"icon": "✅", "text": label(k) + (" – Medaille, Seite zählt anderen Wert"
                                                        if k in (res.get("mismatch") or []) else "")}
                     for k in res["certain"]]
            for k in res["certain"] + [k for g in res["groups"] for k in g["keys"]]:
                if s.get("t"):
                    key_ship.setdefault(k, s["t"])
            for g in res["groups"]:
                names = " / ".join(dict.fromkeys(units[k]["name"] for k in g["keys"] if k in units))
                odds = ship_odds.group_odds(g["keys"], g["pulled"], s.get("t"), medal_t, inputs["moves"], total,
                                            learned)
                shown = ship_odds.fmt_odds(odds, {k: units[k]["name"] for k in g["keys"] if k in units})
                for k, p in odds.items():
                    key_odds[k] = max(key_odds.get(k, 0), p)
                lines.append({"icon": "❓", "text": f"{g['pulled']} von: {shown or names}"})
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
        data["_key_odds"] = key_odds
        data["_key_ship"] = key_ship
        return shipments

    async def _card_list(self, row: Dict, data: Dict, odds: Optional[Dict[str, float]] = None) -> Dict:
        """Alle Karten des Banners mit Exemplaren, Anteil im Pool und wie viele davon schon gezogen sind."""
        pool = json.loads(row['card_pool']) if row.get('card_pool') else None
        if not pool or not pool.get('cards') or not pool.get('total_count'):
            return {"cards": [], "share_above_price": None}
        # Nur sicher Gezogenes abhaken (Medaille oder eindeutig erkannter Versand). Stellvertreter aus
        # ❓-Gruppen zählen nur für die Rechnung - im Raster steht dort "❓ x von n raus".
        _, sure, winners, open_groups, _ = await self._pulled(data.get("medal_thread") or 0, row['pack_id'], pool)
        certain = set(sure) | set(winners)
        total, price = pool['total_count'], data.get("price") or 0
        of_card = lambda keys, cid: [k for k in keys if k == cid or k.startswith(cid + "#")]
        cards = []
        for c in pool['cards']:
            cid = str(c.get("id"))
            gone = len(of_card(certain, cid))
            groups = [g for g in open_groups if of_card(g["keys"], cid)]
            unsure = (f"{groups[0]['pulled']} von {len(groups[0]['keys'])} raus" if groups else None)
            chance = max((p for k, p in (odds or {}).items() if k == cid or k.startswith(cid + "#")), default=None)
            if unsure and chance is not None:
                unsure += f" · ~{round(chance * 100)} %"
            cards.append({"id": cid, "name": c["name"], "value": c["value"], "copies": c["copies"], "image": c.get("image"),
                          "hit": bool(c.get("hit")), "pulled": min(gone, c["copies"]), "unsure": unsure,
                          "share": round(c["copies"] / total * 100, 3)})
        above = sum(c["copies"] for c in pool['cards'] if price and c["value"] >= price)
        return {"cards": cards, "share_above_price": round(above / total * 100, 1) if price else None}
