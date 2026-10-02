"""Push-Benachrichtigungen der Web-App (Web Push, auf dem iPhone ab iOS 16.4 für installierte Apps).

Abos und der zuletzt gesehene Stand liegen in einer eigenen kleinen Datenbank (data/webapp.db),
der VAPID-Schlüssel in data/webapp_vapid.pem - beides wird beim ersten Start angelegt.
"""

import asyncio
import base64
import json
import os
from typing import Dict, List

import aiosqlite
from loguru import logger

from utils.card_pool import fmt_coins, fmt_pct
from utils.hot_list import ALERT_PCT, ALERT_RESET_PCT, new_alerts

# Allgemein (für alle Banner): neuer Banner, über 100 % (nur ziehbare, wie Top 10) und dieselben
# Banner-Ereignisse wie beim Beobachten. Pack-Bewegung für alle wird zu einem Push zusammengefasst.
EVENTS = ("new", "value", "hit", "packs", "ship", "low", "end")
DEFAULTS = {"new": True, "value": True, "hit": True, "packs": False, "ship": False, "low": True, "end": False}
# pro beobachtetem Banner: Hit raus, Packs weniger, Versandschub, über 100 %, Endspurt, beendet
WATCH_EVENTS = ("hit", "packs", "ship", "ev", "low", "end")
MAX_WATCHED = 50
WISH_EVENTS = ("wish_new", "wish_out")
MAX_WISH = 100
MAX_PACK_LINES = 6
INBOX_KEEP = 300      # je Gerät
INBOX_DAYS = 30


def _watch_events(b: Dict, old: Dict) -> List[tuple]:
    """Ereignisse eines Banners seit dem letzten Stand: [(Art, Titel, Text), ...]."""
    out, bid = [], b["id"]
    if b["remaining"] < old.get("remaining", b["remaining"]):
        diff = old["remaining"] - b["remaining"]
        out.append(("packs", f"📉 Banner {bid}: {fmt_coins(old['remaining'])} → {fmt_coins(b['remaining'])} Packs",
                    f"−{fmt_coins(diff)} · {fmt_coins(b['remaining'])} von {fmt_coins(b['total'])} übrig"))
    gone = [h for h in b.get("out") or [] if h["name"] + str(h["value"]) not in set(old.get("out", []))]
    for h in gone:
        out.append(("hit", f"🎯 Hit raus · Banner {bid}", f"{h['name']} ({fmt_coins(h['value'])} Coins)"))
    if (b.get("ship_cards") or 0) > old.get("ship_cards", b.get("ship_cards") or 0):
        n = b["ship_cards"] - old["ship_cards"]
        value = (b.get("ship_value") or 0) - old.get("ship_value", 0)
        out.append(("ship", f"📦 Versand · Banner {bid}",
                    f"+{n} {'Karte' if n == 1 else 'Karten'} · {fmt_coins(value)} Coins Kartenwert"))
    pct = b.get("ev_pct")
    if pct is not None and pct > ALERT_PCT and not old.get("ev_high"):
        out.append(("ev", f"💰 Banner {bid} über 100 %", f"Ø {fmt_pct(pct)} % zurück · "
                                                          f"{fmt_coins(b['remaining'])} Packs übrig"))
    if b.get("status") == "endspurt" and not old.get("endspurt"):
        out.append(("low", f"⚡ Endspurt · Banner {bid}", f"nur noch {fmt_coins(b['remaining'])} Packs"))
    if b["remaining"] <= 0 < old.get("remaining", 0):
        out.append(("end", f"🏁 Banner {bid} ausverkauft", b.get("title") or ""))
    return out


def _banner_state(b: Dict, old: Dict) -> Dict:
    pct = b.get("ev_pct")
    ev_high = old.get("ev_high", False)
    if pct is not None:
        ev_high = pct > ALERT_PCT or (ev_high and pct >= ALERT_RESET_PCT)
    return {"remaining": b["remaining"], "ship_cards": b.get("ship_cards") or 0,
            "ship_value": b.get("ship_value") or 0, "title": b.get("title"),
            "out": [h["name"] + str(h["value"]) for h in b.get("out") or []],
            "ev_high": ev_high, "endspurt": b.get("status") == "endspurt"}


def build_events(banners: List[Dict], hot: List[Dict], state: Dict) -> tuple:
    """(Nachrichten, neuer Stand) aus dem aktuellen und dem zuletzt gesehenen Stand.

    Nachricht = (Art, Titel, Text, Banner-ID, Banner-Ereignis). Banner-Ereignisse (Hit raus, Packs,
    Versand, über 100 %, Endspurt, beendet) entstehen für jeden Banner; wer sie bekommt, entscheidet
    recipients() - je Gerät "für alle" oder nur für beobachtete Banner. Beim ersten Durchlauf und für
    Banner ohne bisherigen Stand wird nur gemerkt, nichts gemeldet.
    """
    first = not state
    by_id = {b["id"]: b for b in banners}
    messages = []

    known = set(state.get("known", []))
    for b in banners:
        if b["id"] not in known and not first:
            price = f"{fmt_coins(b['price'])} Coins" if b["price"] else "gratis"
            messages.append(("new", f"🆕 Neuer Banner {b['id']}", f"{b['title']} · {price}", b["id"], False))

    candidates = [{**e, "pack_id": e["id"]} for e in hot]
    fresh, alerted = new_alerts(candidates, set(state.get("alerted", [])))
    if not first:
        for e in fresh:
            messages.append(("value", f"💰 Banner {e['id']} lohnt sich",
                             f"Ø {fmt_pct(e['pct'])} % zurück · {fmt_coins(e['price'])} Coins · "
                             f"{fmt_coins(e['remaining'])} Packs übrig", e["id"], False))

    old_banners = state.get("banners", {})
    new_banners = {}
    for b in banners:
        old = old_banners.get(str(b["id"]))
        if old is not None:
            messages += [(kind, title, body, b["id"], True) for kind, title, body in _watch_events(b, old)]
        new_banners[str(b["id"])] = _banner_state(b, old or {})
    for bid, old in old_banners.items():
        if int(bid) not in by_id and old.get("remaining", 0) > 0:
            messages.append(("end", f"🏁 Banner {bid} beendet", f"{old.get('title') or ''} ist nicht mehr online",
                             int(bid), True))

    wish, cards, outc = wish_events(banners, state, first)
    messages += wish
    new_state = {"known": sorted(by_id), "alerted": sorted(alerted), "banners": new_banners,
                 "cards": cards, "outc": outc}
    return messages, new_state


def wish_events(banners: List[Dict], state: Dict, first: bool) -> tuple:
    """Wunschkarten: ("wish_new", …) wenn ein Banner eine Karte neu enthält (neuer Banner oder Kartenpool
    erst später geladen), ("wish_out", …) wenn sie in einem Banner sicher gezogen wurde. Fünftes Feld =
    Karten-ID; wer es bekommt, entscheidet recipients() anhand der Wunschliste des Geräts.
    Beim ersten Mal (auch nach dem Update auf diese Version) wird nur gemerkt."""
    old_cards, old_out = state.get("cards"), state.get("outc") or {}
    remember_only = first or old_cards is None
    cards, outc, messages = {}, {}, []
    for b in banners:
        bid = str(b["id"])
        brief = b.get("cards_brief") or {}
        if not brief:
            continue
        cards[bid] = sorted(brief)
        outc[bid] = sorted(b.get("out_ids") or [])
        if remember_only:
            continue
        price = f"{fmt_coins(b['price'])} Coins" if b.get("price") else "gratis"
        if bid not in old_cards:
            for cid, (name, value, _, _) in brief.items():
                messages.append(("wish_new", "⭐ Wunschkarte in neuem Banner",
                                 f"{name} ({fmt_coins(value)} Coins) · {b.get('title') or 'Banner ' + bid} · {price}",
                                 b["id"], cid))
        for cid in set(outc[bid]) - set(old_out.get(bid, [])):
            if bid in old_out and cid in brief:
                name, value = brief[cid][0], brief[cid][1]
                messages.append(("wish_out", "🎯 Wunschkarte gezogen",
                                 f"{name} ({fmt_coins(value)} Coins) ist in {b.get('title') or 'Banner ' + bid} raus",
                                 b["id"], cid))
    return messages, cards, outc


def clean_prefs(prefs: Dict) -> Dict:
    """Einstellungen eines Geräts: allgemeine Pushes an/aus und beobachtete Banner mit Ereignissen."""
    clean = {e: bool(prefs.get(e, DEFAULTS[e])) for e in EVENTS}
    watch = {}
    for bid, kinds in list((prefs.get("watch") or {}).items())[:MAX_WATCHED]:
        try:
            bid = str(int(bid))
        except (TypeError, ValueError):
            continue
        watch[bid] = [k for k in WATCH_EVENTS if k in (kinds or [])]
    clean["watch"] = watch
    clean["wish"] = [str(c) for c in (prefs.get("wish") or []) if str(c).isdigit()][:MAX_WISH]
    return clean


def recipients(messages: List[tuple], prefs: Dict) -> List[tuple]:
    """Welche Nachrichten ein Gerät bekommt.

    Beobachtete Banner: die dort gewählten Ereignisse, einzeln. Sonst die allgemeinen Schalter;
    Pack-Bewegung aller übrigen Banner kommt als ein zusammengefasster Push. Jede Meldung nur einmal.
    """
    watch = prefs.get("watch") or {}
    wish = set(prefs.get("wish") or [])
    on = lambda e: prefs.get(e, DEFAULTS.get(e, False))
    picked, packs = [], []
    for m in messages:
        kind, _, _, bid, banner_event = m
        if kind in WISH_EVENTS:
            if banner_event in wish:
                picked.append(m)
            continue
        if not banner_event:
            continue
        if kind in watch.get(str(bid), []):
            picked.append(m)
        elif kind == "packs" and on("packs"):
            packs.append(m)
        elif kind != "packs" and kind != "ev" and on(kind):
            picked.append(m)
    covered = {(m[3], m[0]) for m in picked}
    for m in messages:
        if m[0] not in WISH_EVENTS and not m[4] and on(m[0]) and not (m[0] == "value" and (m[3], "ev") in covered):
            picked.append(m)
    if packs:
        lines = [m[1].split(": ", 1)[-1].replace(" Packs", "") for m in packs]
        body = " · ".join(f"{m[3]}: {line}" for m, line in zip(packs[:MAX_PACK_LINES], lines))
        if len(packs) > MAX_PACK_LINES:
            body += f" · +{len(packs) - MAX_PACK_LINES} weitere"
        picked.append(("packs", f"📉 Pack-Bewegung bei {len(packs)} Banner{'n' if len(packs) > 1 else ''}",
                       body, packs[0][3] if len(packs) == 1 else None, True))
    return picked


class PushService:
    def __init__(self, data_dir: str, contact: str):
        self.db_path = os.path.join(data_dir, "webapp.db")
        self.key_path = os.path.join(data_dir, "webapp_vapid.pem")
        self.contact = contact
        self._vapid = None

    async def init(self):
        from py_vapid import Vapid02
        if not os.path.exists(self.key_path):
            vapid = Vapid02()
            vapid.generate_keys()
            vapid.save_key(self.key_path)
            logger.info("Push: neuer VAPID-Schlüssel angelegt")
        self._vapid = Vapid02.from_file(self.key_path)
        async with aiosqlite.connect(self.db_path) as db:
            await db.executescript("""
                CREATE TABLE IF NOT EXISTS subscriptions (
                    endpoint TEXT PRIMARY KEY, data TEXT, prefs TEXT, created_at TEXT DEFAULT CURRENT_TIMESTAMP);
                CREATE TABLE IF NOT EXISTS state (key TEXT PRIMARY KEY, value TEXT);
                CREATE TABLE IF NOT EXISTS push_log (
                    id INTEGER PRIMARY KEY AUTOINCREMENT, endpoint TEXT, kind TEXT, title TEXT, body TEXT,
                    banner_id INTEGER, created_at TEXT DEFAULT CURRENT_TIMESTAMP, read INTEGER DEFAULT 0);
                CREATE INDEX IF NOT EXISTS push_log_endpoint ON push_log (endpoint, id);
            """)
            try:   # Gerät gehört zu einer Discord-Verknüpfung (für eigene Pushes: Medaillen, Erinnerung)
                await db.execute("ALTER TABLE subscriptions ADD COLUMN user_id TEXT")
            except aiosqlite.OperationalError:
                pass
            await db.commit()

    def public_key(self) -> str:
        """Öffentlicher Schlüssel als base64url (applicationServerKey im Browser)."""
        from cryptography.hazmat.primitives import serialization
        raw = self._vapid.public_key.public_bytes(serialization.Encoding.X962,
                                                  serialization.PublicFormat.UncompressedPoint)
        return base64.urlsafe_b64encode(raw).rstrip(b"=").decode()

    async def subscribe(self, subscription: Dict, prefs: Dict, user_id: str = None):
        prefs = clean_prefs(prefs)
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute(
                "INSERT INTO subscriptions (endpoint, data, prefs, user_id) VALUES (?, ?, ?, ?) "
                "ON CONFLICT(endpoint) DO UPDATE SET data = excluded.data, prefs = excluded.prefs, "
                "user_id = COALESCE(excluded.user_id, subscriptions.user_id)",
                (subscription["endpoint"], json.dumps(subscription), json.dumps(prefs), user_id))
            await db.commit()

    async def set_user(self, endpoint: str, user_id: str):
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute("UPDATE subscriptions SET user_id = ? WHERE endpoint = ? AND user_id IS NOT ?",
                             (str(user_id), endpoint, str(user_id)))
            await db.commit()

    async def send_user(self, user_id: str, kind: str, title: str, body: str, url: str = "/") -> int:
        """Eigene Nachricht an alle Geräte einer Discord-Verknüpfung (landet auch in der Glocke)."""
        async with aiosqlite.connect(self.db_path) as db:
            cur = await db.execute("SELECT endpoint, data FROM subscriptions WHERE user_id = ?", (str(user_id),))
            rows = await cur.fetchall()
        for endpoint, sub in rows:
            await self._push(endpoint, json.loads(sub), title, body, None, kind, url=url)
        return len(rows)

    async def unsubscribe(self, endpoint: str):
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute("DELETE FROM subscriptions WHERE endpoint = ?", (endpoint,))
            await db.commit()

    async def prefs(self, endpoint: str) -> Dict:
        async with aiosqlite.connect(self.db_path) as db:
            cur = await db.execute("SELECT prefs FROM subscriptions WHERE endpoint = ?", (endpoint,))
            row = await cur.fetchone()
        return json.loads(row[0]) if row else {}

    async def _subscriptions(self) -> List[tuple]:
        async with aiosqlite.connect(self.db_path) as db:
            cur = await db.execute("SELECT endpoint, data, prefs FROM subscriptions")
            return [(e, json.loads(d), json.loads(p)) for e, d, p in await cur.fetchall()]

    async def load_state(self, key: str = "events") -> Dict:
        async with aiosqlite.connect(self.db_path) as db:
            cur = await db.execute("SELECT value FROM state WHERE key = ?", (key,))
            row = await cur.fetchone()
        return json.loads(row[0]) if row else {}

    async def save_state(self, state: Dict, key: str = "events"):
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute("INSERT OR REPLACE INTO state (key, value) VALUES (?, ?)", (key, json.dumps(state)))
            await db.commit()

    async def deliver(self, messages: List[tuple]):
        """Verteilt Nachrichten an die Geräte, je nach deren Einstellungen."""
        if not messages:
            return
        for endpoint, sub, prefs in await self._subscriptions():
            for kind, title, body, banner_id, _ in recipients(messages, prefs):
                await self._push(endpoint, sub, title, body, banner_id, kind)

    async def send(self, event: str, title: str, body: str, banner_id=None, only: str = None):
        """Einzelne Nachricht (Test-Push) an ein Gerät oder alle."""
        for endpoint, sub, prefs in await self._subscriptions():
            if not only or endpoint == only:
                await self._push(endpoint, sub, title, body, banner_id, event)

    # --- Verlauf der Pushes je Gerät (Glocke in der App) ---
    async def _log(self, endpoint: str, kind: str, title: str, body: str, banner_id) -> int:
        async with aiosqlite.connect(self.db_path) as db:
            cur = await db.execute("INSERT INTO push_log (endpoint, kind, title, body, banner_id) VALUES (?, ?, ?, ?, ?)",
                                   (endpoint, kind, title, body, banner_id))
            await db.execute("DELETE FROM push_log WHERE endpoint = ? AND (created_at < datetime('now', ?) OR id NOT IN "
                             "(SELECT id FROM push_log WHERE endpoint = ? ORDER BY id DESC LIMIT ?))",
                             (endpoint, f"-{INBOX_DAYS} days", endpoint, INBOX_KEEP))
            await db.commit()
            return cur.lastrowid

    async def inbox(self, endpoint: str, limit: int = 30, before: int = 0) -> Dict:
        """Letzte Pushes eines Geräts (neueste zuerst) und Anzahl ungelesener."""
        async with aiosqlite.connect(self.db_path) as db:
            cur = await db.execute(
                "SELECT id, kind, title, body, banner_id, created_at, read FROM push_log WHERE endpoint = ? "
                "AND (? = 0 OR id < ?) ORDER BY id DESC LIMIT ?", (endpoint, before, before, limit + 1))
            rows = await cur.fetchall()
            cur = await db.execute("SELECT count(*) FROM push_log WHERE endpoint = ? AND read = 0", (endpoint,))
            unread = (await cur.fetchone())[0]
        items = [{"id": r[0], "kind": r[1], "title": r[2], "body": r[3], "banner_id": r[4],
                  "t": r[5], "read": bool(r[6])} for r in rows[:limit]]
        return {"items": items, "unread": unread, "more": len(rows) > limit}

    async def mark_read(self, endpoint: str, ids: List[int] = None):
        """Bestimmte (ids) oder alle Pushes eines Geräts als gelesen markieren."""
        async with aiosqlite.connect(self.db_path) as db:
            if ids is None:
                await db.execute("UPDATE push_log SET read = 1 WHERE endpoint = ? AND read = 0", (endpoint,))
            else:
                await db.executemany("UPDATE push_log SET read = 1 WHERE endpoint = ? AND id = ?",
                                     [(endpoint, int(i)) for i in ids])
            await db.commit()

    async def _push(self, endpoint: str, sub: Dict, title: str, body: str, banner_id=None, kind: str = "",
                    url: str = None):
        from pywebpush import WebPushException, webpush
        log_id = await self._log(endpoint, kind, title, body, banner_id)
        if url:
            url = f"{url}{'&' if '?' in url else '?'}n={log_id}"
        else:
            url = f"/#/banner/{banner_id}?n={log_id}" if banner_id else f"/#/inbox?n={log_id}"
        unread = (await self.inbox(endpoint, 1))["unread"]
        payload = json.dumps({"title": title, "body": body, "url": url, "id": log_id, "unread": unread})
        loop = asyncio.get_running_loop()
        try:
            await loop.run_in_executor(None, lambda: webpush(
                subscription_info=sub, data=payload, vapid_private_key=self._vapid,
                vapid_claims={"sub": self.contact}, ttl=3600))
        except WebPushException as e:
            status = getattr(e.response, "status_code", None)
            if status in (404, 410):
                await self.unsubscribe(endpoint)
                logger.info("Push: abgelaufenes Abo entfernt")
            else:
                logger.warning(f"Push fehlgeschlagen ({status}): {e}")
        except Exception as e:
            logger.warning(f"Push fehlgeschlagen: {e}")
