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
from utils.hot_list import new_alerts

EVENTS = ("value", "hit", "new")   # über 100 %, Hit verschickt, neuer Banner


def build_events(banners: List[Dict], hot: List[Dict], state: Dict) -> tuple:
    """(Nachrichten, neuer Stand) aus dem aktuellen und dem zuletzt gesehenen Stand.

    Beim ersten Durchlauf (leerer Stand) wird nur gemerkt, nichts gemeldet.
    """
    first = not state
    by_id = {b["id"]: b for b in banners}
    messages = []

    known = set(state.get("known", []))
    for b in banners:
        if b["id"] not in known and not first:
            price = f"{fmt_coins(b['price'])} Coins" if b["price"] else "gratis"
            messages.append(("new", f"🆕 Neuer Banner {b['id']}", f"{b['title']} · {price}", b["id"]))

    candidates = [{**e, "pack_id": e["id"]} for e in hot]
    fresh, alerted = new_alerts(candidates, set(state.get("alerted", [])))
    if not first:
        for e in fresh:
            messages.append(("value", f"💰 Banner {e['id']} lohnt sich",
                             f"Ø {fmt_pct(e['pct'])} % zurück · {fmt_coins(e['price'])} Coins · "
                             f"{fmt_coins(e['remaining'])} Packs übrig", e["id"]))

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
                                 f"{hit['name']} ({fmt_coins(hit['value'])} Coins)", b["id"]))

    new_state = {"known": sorted(by_id), "alerted": sorted(alerted), "detected": detected}
    return messages, new_state


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
        prefs = {e: bool(prefs.get(e, True)) for e in EVENTS}
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

    async def send(self, event: str, title: str, body: str, banner_id=None, only: str = None):
        """Schickt eine Nachricht an alle Abos, die dieses Ereignis eingeschaltet haben."""
        from pywebpush import WebPushException, webpush
        payload = json.dumps({"title": title, "body": body,
                              "url": f"/#/banner/{banner_id}" if banner_id else "/"})
        loop = asyncio.get_running_loop()
        for endpoint, sub, prefs in await self._subscriptions():
            if only and endpoint != only:
                continue
            if event != "test" and not prefs.get(event, True):
                continue
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
