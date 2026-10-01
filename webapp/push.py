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

EVENTS = ("value", "hit", "new")   # allgemein: über 100 %, Hit verschickt, neuer Banner
# pro beobachtetem Banner: Hit raus, Packs weniger, Versandschub, über 100 %, Endspurt, beendet
WATCH_EVENTS = ("hit", "packs", "ship", "ev", "low", "end")
MAX_WATCHED = 50


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


def build_events(banners: List[Dict], hot: List[Dict], state: Dict, watched=frozenset()) -> tuple:
    """(Nachrichten, neuer Stand) aus dem aktuellen und dem zuletzt gesehenen Stand.

    Nachricht = (Art, Titel, Text, Banner-ID, nur_für_Beobachter). Ereignisse einzelner Banner werden
    nur für beobachtete Banner (watched) erzeugt. Beim ersten Durchlauf - und für Banner, die noch
    keinen Stand haben - wird nur gemerkt, nichts gemeldet.
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

    seen = {str(k): set(v) for k, v in state.get("detected", {}).items()}
    detected = {}
    for b in banners:
        keys = set(b.get("hit_keys_detected") or [])
        detected[str(b["id"])] = sorted(keys)
        before = seen.get(str(b["id"]))
        if first or before is None:
            continue
        for hit in b.get("hits") or []:
            if hit["key"] in keys - before:
                messages.append(("hit", f"📦 Hit verschickt · Banner {b['id']}",
                                 f"{hit['name']} ({fmt_coins(hit['value'])} Coins)", b["id"], False))

    # Beobachtete Banner
    old_banners = state.get("banners", {})
    new_banners = {}
    for b in banners:
        old = old_banners.get(str(b["id"]))
        if old is not None and b["id"] in watched:
            messages += [(kind, title, body, b["id"], True) for kind, title, body in _watch_events(b, old)]
        new_banners[str(b["id"])] = _banner_state(b, old or {})
    for bid, old in old_banners.items():
        if int(bid) not in by_id and int(bid) in watched and old.get("remaining", 0) > 0:
            messages.append(("end", f"🏁 Banner {bid} beendet", f"{old.get('title') or ''} ist nicht mehr online",
                             int(bid), True))

    new_state = {"known": sorted(by_id), "alerted": sorted(alerted), "detected": detected, "banners": new_banners}
    return messages, new_state


def clean_prefs(prefs: Dict) -> Dict:
    """Einstellungen eines Geräts: allgemeine Pushes an/aus und beobachtete Banner mit Ereignissen."""
    clean = {e: bool(prefs.get(e, True)) for e in EVENTS}
    watch = {}
    for bid, kinds in list((prefs.get("watch") or {}).items())[:MAX_WATCHED]:
        try:
            bid = str(int(bid))
        except (TypeError, ValueError):
            continue
        watch[bid] = [k for k in WATCH_EVENTS if k in (kinds or [])]
    clean["watch"] = watch
    return clean


def recipients(messages: List[tuple], prefs: Dict) -> List[tuple]:
    """Welche Nachrichten ein Gerät bekommt; doppelte (allgemein + beobachtet) nur einmal."""
    watch = prefs.get("watch") or {}
    picked = [m for m in messages if m[4] and m[0] in watch.get(str(m[3]), [])]
    covered = {(m[3], m[0]) for m in picked}
    same = {"hit": "hit", "value": "ev"}   # allgemein -> entsprechendes Banner-Ereignis
    for m in messages:
        if not m[4] and prefs.get(m[0], True) and (m[3], same.get(m[0])) not in covered:
            picked.append(m)
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
            """)
            await db.commit()

    def public_key(self) -> str:
        """Öffentlicher Schlüssel als base64url (applicationServerKey im Browser)."""
        from cryptography.hazmat.primitives import serialization
        raw = self._vapid.public_key.public_bytes(serialization.Encoding.X962,
                                                  serialization.PublicFormat.UncompressedPoint)
        return base64.urlsafe_b64encode(raw).rstrip(b"=").decode()

    async def subscribe(self, subscription: Dict, prefs: Dict):
        prefs = clean_prefs(prefs)
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute("INSERT OR REPLACE INTO subscriptions (endpoint, data, prefs) VALUES (?, ?, ?)",
                             (subscription["endpoint"], json.dumps(subscription), json.dumps(prefs)))
            await db.commit()

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

    async def load_state(self) -> Dict:
        async with aiosqlite.connect(self.db_path) as db:
            cur = await db.execute("SELECT value FROM state WHERE key = 'events'")
            row = await cur.fetchone()
        return json.loads(row[0]) if row else {}

    async def save_state(self, state: Dict):
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute("INSERT OR REPLACE INTO state (key, value) VALUES ('events', ?)", (json.dumps(state),))
            await db.commit()

    async def watched(self) -> set:
        """Alle Banner, die irgendein Gerät beobachtet."""
        ids = set()
        for _, _, prefs in await self._subscriptions():
            ids |= {int(b) for b, kinds in (prefs.get("watch") or {}).items() if kinds}
        return ids

    async def deliver(self, messages: List[tuple]):
        """Verteilt Nachrichten an die Geräte, je nach deren Einstellungen."""
        if not messages:
            return
        for endpoint, sub, prefs in await self._subscriptions():
            for _, title, body, banner_id, _ in recipients(messages, prefs):
                await self._push(endpoint, sub, title, body, banner_id)

    async def send(self, event: str, title: str, body: str, banner_id=None, only: str = None):
        """Einzelne Nachricht (Test-Push) an ein Gerät oder alle."""
        for endpoint, sub, prefs in await self._subscriptions():
            if not only or endpoint == only:
                await self._push(endpoint, sub, title, body, banner_id)

    async def _push(self, endpoint: str, sub: Dict, title: str, body: str, banner_id=None):
        from pywebpush import WebPushException, webpush
        payload = json.dumps({"title": title, "body": body,
                              "url": f"/#/banner/{banner_id}" if banner_id else "/"})
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
