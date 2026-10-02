"""Brücke zwischen Web-App und Bot (data/webapp.db): Discord-Verknüpfung und Medaillen-Meldungen.

Ablauf:
  1. In Discord /app-verknüpfen -> der Bot legt einen Code an (10 Minuten gültig, einmal benutzbar).
  2. In der App den Code eingeben -> die App legt ein Gerät an und gibt ihm ein geheimes Token.
  3. Medaille in der App melden -> die App legt eine Meldung an; der Bot holt sie ab, prüft sie wie
     eine Medaille in Discord, postet im Thread und trägt das Ergebnis ein (ok / abgelehnt + Grund).
"""

import hashlib
import json
import secrets
from datetime import datetime, timedelta
from typing import Dict, List, Optional

import aiosqlite

CODE_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"   # ohne 0/O/1/I
CODE_LENGTH = 6
CODE_MINUTES = 10

SCHEMA = """
CREATE TABLE IF NOT EXISTS link_codes (
    code TEXT PRIMARY KEY, discord_user_id TEXT, discord_name TEXT, created_at TEXT, used INTEGER DEFAULT 0);
CREATE TABLE IF NOT EXISTS devices (
    token_hash TEXT PRIMARY KEY, discord_user_id TEXT, discord_name TEXT, created_at TEXT);
CREATE TABLE IF NOT EXISTS medal_requests (
    id INTEGER PRIMARY KEY AUTOINCREMENT, pack_id INTEGER, tier TEXT, discord_user_id TEXT, discord_name TEXT,
    action TEXT, status TEXT DEFAULT 'pending', reason TEXT, created_at TEXT, done_at TEXT);
CREATE TABLE IF NOT EXISTS user_imports (
    id INTEGER PRIMARY KEY AUTOINCREMENT, discord_user_id TEXT, kind TEXT, url TEXT, data TEXT, created_at TEXT);
CREATE TABLE IF NOT EXISTS user_history (
    discord_user_id TEXT, area TEXT, data TEXT, updated_at TEXT, PRIMARY KEY (discord_user_id, area));
CREATE TABLE IF NOT EXISTS auto_claims (
    discord_user_id TEXT, card_key TEXT, pack_id INTEGER, tier TEXT, request_id INTEGER, created_at TEXT,
    PRIMARY KEY (discord_user_id, card_key));
"""
MAX_IMPORT_BYTES = 8_000_000
KEEP_IMPORTS = 24    # je Person (~3 Läufe; der Verlauf liegt zusammengeführt in user_history)


def _hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def _now() -> str:
    return datetime.now().isoformat()


class AppBridge:
    def __init__(self, db_path: str):
        self.db_path = db_path

    async def init(self):
        async with aiosqlite.connect(self.db_path) as db:
            await db.executescript(SCHEMA)
            # alte Rohdaten der Lesezeichen aufräumen (früher 100 je Person) und Platz freigeben
            cur = await db.execute(
                "DELETE FROM user_imports WHERE id NOT IN (SELECT id FROM (SELECT id, ROW_NUMBER() OVER "
                "(PARTITION BY discord_user_id ORDER BY id DESC) AS n FROM user_imports) WHERE n <= ?)", (KEEP_IMPORTS,))
            removed = cur.rowcount
            for column in ("last_seen TEXT", "agent TEXT"):   # Geräte verwalten: zuletzt aktiv, Gerätetyp
                try:
                    await db.execute(f"ALTER TABLE devices ADD COLUMN {column}")
                except aiosqlite.OperationalError:
                    pass
            try:   # Push zum Ergebnis automatischer Medaillen - bisherige gelten als erledigt
                await db.execute("ALTER TABLE auto_claims ADD COLUMN notified INTEGER DEFAULT 0")
                await db.execute("UPDATE auto_claims SET notified = 1")
            except aiosqlite.OperationalError:
                pass
            await db.commit()
            if removed > 0:
                await db.execute("VACUUM")

    # --- Verknüpfung (Bot legt Code an, App löst ihn ein) ---
    async def create_code(self, user_id: int, name: str) -> str:
        code = "".join(secrets.choice(CODE_ALPHABET) for _ in range(CODE_LENGTH))
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute("DELETE FROM link_codes WHERE discord_user_id = ? AND used = 0", (str(user_id),))
            await db.execute("INSERT OR REPLACE INTO link_codes (code, discord_user_id, discord_name, created_at) "
                             "VALUES (?, ?, ?, ?)", (code, str(user_id), name, _now()))
            await db.commit()
        return code

    async def redeem_code(self, code: str, agent: str = "") -> Optional[Dict]:
        """Gültiger Code -> neues Gerät {token, user_id, name}; sonst None."""
        code = (code or "").strip().upper().replace(" ", "")
        cutoff = (datetime.now() - timedelta(minutes=CODE_MINUTES)).isoformat()
        async with aiosqlite.connect(self.db_path) as db:
            cur = await db.execute("SELECT discord_user_id, discord_name FROM link_codes "
                                   "WHERE code = ? AND used = 0 AND created_at >= ?", (code, cutoff))
            row = await cur.fetchone()
            if not row:
                return None
            token = secrets.token_urlsafe(32)
            await db.execute("UPDATE link_codes SET used = 1 WHERE code = ?", (code,))
            await db.execute("INSERT INTO devices (token_hash, discord_user_id, discord_name, created_at, last_seen, agent) "
                             "VALUES (?, ?, ?, ?, ?, ?)", (_hash(token), row[0], row[1], _now(), _now(), agent[:40]))
            await db.commit()
        return {"token": token, "user_id": row[0], "name": row[1]}

    async def device(self, token: Optional[str]) -> Optional[Dict]:
        if not token:
            return None
        async with aiosqlite.connect(self.db_path) as db:
            cur = await db.execute("SELECT discord_user_id, discord_name, last_seen FROM devices WHERE token_hash = ?",
                                   (_hash(token),))
            row = await cur.fetchone()
            # "zuletzt aktiv" höchstens stündlich schreiben
            if row and (not row[2] or row[2] < (datetime.now() - timedelta(hours=1)).isoformat()):
                await db.execute("UPDATE devices SET last_seen = ? WHERE token_hash = ?", (_now(), _hash(token)))
                await db.commit()
        return {"user_id": row[0], "name": row[1]} if row else None

    async def devices(self, user_id: str) -> List[Dict]:
        """Alle verknüpften Geräte einer Person (Kennung = Anfang des Schlüssel-Hashes, nie der Schlüssel)."""
        async with aiosqlite.connect(self.db_path) as db:
            cur = await db.execute("SELECT token_hash, created_at, last_seen, agent FROM devices "
                                   "WHERE discord_user_id = ? ORDER BY COALESCE(last_seen, created_at) DESC", (str(user_id),))
            return [{"id": h[:16], "created_at": c, "last_seen": s, "agent": a or ""} for h, c, s, a in await cur.fetchall()]

    async def remove_device(self, user_id: str, device_id: str) -> bool:
        if len(device_id) < 16:
            return False
        async with aiosqlite.connect(self.db_path) as db:
            cur = await db.execute("DELETE FROM devices WHERE discord_user_id = ? AND substr(token_hash, 1, 16) = ?",
                                   (str(user_id), device_id[:16]))
            await db.commit()
            return cur.rowcount > 0

    async def unlink(self, token: str):
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute("DELETE FROM devices WHERE token_hash = ?", (_hash(token),))
            await db.commit()

    # --- Eigene GTCHA-Daten, per Lesezeichen von der eigenen Seite übertragen ---
    async def add_import(self, user: Dict, kind: str, url: str, data: str) -> int:
        async with aiosqlite.connect(self.db_path) as db:
            cur = await db.execute(
                "INSERT INTO user_imports (discord_user_id, kind, url, data, created_at) VALUES (?, ?, ?, ?, ?)",
                (user["user_id"], kind, url[:500], data, _now()))
            await db.execute(
                "DELETE FROM user_imports WHERE discord_user_id = ? AND id NOT IN "
                "(SELECT id FROM user_imports WHERE discord_user_id = ? ORDER BY id DESC LIMIT ?)",
                (user["user_id"], user["user_id"], KEEP_IMPORTS))
            await db.commit()
            return cur.lastrowid

    async def latest_sync(self, user_id: str) -> Dict:
        """Neuester Stand je Bereich aus "Alles übertragen": path -> {pages, ..., saved_at}."""
        async with aiosqlite.connect(self.db_path) as db:
            cur = await db.execute(
                "SELECT url, data, created_at FROM user_imports WHERE id IN (SELECT max(id) FROM user_imports "
                "WHERE discord_user_id = ? AND kind = 'sync' GROUP BY url)", (str(user_id),))
            rows = await cur.fetchall()
        areas = {}
        for url, data, created in rows:
            try:
                areas[url.rsplit("/", 1)[-1]] = {**json.loads(data), "saved_at": created}
            except ValueError:
                continue
        return areas

    # --- Zusammengeführter Verlauf (nur Neues wird angehängt) ---
    async def get_history(self, user_id: str) -> Dict:
        async with aiosqlite.connect(self.db_path) as db:
            cur = await db.execute("SELECT area, data, updated_at FROM user_history WHERE discord_user_id = ?",
                                   (str(user_id),))
            rows = await cur.fetchall()
        return {area: {**json.loads(data), "updated_at": updated} for area, data, updated in rows}

    async def set_history(self, user_id: str, areas: Dict[str, Dict]):
        async with aiosqlite.connect(self.db_path) as db:
            for area, data in areas.items():
                data = {k: v for k, v in data.items() if k != "updated_at"}
                await db.execute("INSERT OR REPLACE INTO user_history (discord_user_id, area, data, updated_at) "
                                 "VALUES (?, ?, ?, ?)", (str(user_id), area, json.dumps(data, ensure_ascii=False), _now()))
            await db.commit()

    # --- Automatische Medaillen aus den angeforderten Karten (jede Karte nur einmal) ---
    async def auto_claim_keys(self, user_id: str) -> set:
        async with aiosqlite.connect(self.db_path) as db:
            cur = await db.execute("SELECT card_key FROM auto_claims WHERE discord_user_id = ?", (str(user_id),))
            return {r[0] for r in await cur.fetchall()}

    async def add_auto_claim(self, user: Dict, card_key: str, pack_id: int, tier: str) -> int:
        request_id = await self.add_request(pack_id, tier, user, "claim")
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute("INSERT OR IGNORE INTO auto_claims (discord_user_id, card_key, pack_id, tier, request_id, "
                             "created_at) VALUES (?, ?, ?, ?, ?, ?)",
                             (user["user_id"], card_key, pack_id, tier, request_id, _now()))
            await db.commit()
        return request_id

    async def auto_claims(self, user_id: str) -> List[Dict]:
        async with aiosqlite.connect(self.db_path) as db:
            db.row_factory = aiosqlite.Row
            cur = await db.execute(
                "SELECT a.card_key, a.pack_id, a.tier, a.created_at, r.status, r.reason FROM auto_claims a "
                "LEFT JOIN medal_requests r ON r.id = a.request_id WHERE a.discord_user_id = ? "
                "ORDER BY a.created_at DESC LIMIT 50", (str(user_id),))
            return [dict(r) for r in await cur.fetchall()]

    async def finished_auto_claims(self) -> List[Dict]:
        """Automatische Medaillen, die der Bot erledigt hat und zu denen noch kein Push ging."""
        async with aiosqlite.connect(self.db_path) as db:
            db.row_factory = aiosqlite.Row
            cur = await db.execute(
                "SELECT a.discord_user_id, a.card_key, a.pack_id, a.tier, r.status, r.reason FROM auto_claims a "
                "JOIN medal_requests r ON r.id = a.request_id WHERE a.notified = 0 AND r.status != 'pending'")
            return [dict(r) for r in await cur.fetchall()]

    async def mark_auto_claim_notified(self, user_id: str, card_key: str):
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute("UPDATE auto_claims SET notified = 1 WHERE discord_user_id = ? AND card_key = ?",
                             (str(user_id), card_key))
            await db.commit()

    async def last_syncs(self) -> Dict[str, str]:
        """Discord-ID -> Zeitpunkt des letzten Übertragens (nur wer schon einmal übertragen hat)."""
        async with aiosqlite.connect(self.db_path) as db:
            cur = await db.execute("SELECT discord_user_id, max(updated_at) FROM user_history GROUP BY discord_user_id")
            return {u: t for u, t in await cur.fetchall() if t}

    # --- Medaillen-Meldungen (App legt an, Bot arbeitet ab) ---
    async def add_request(self, pack_id: int, tier: str, user: Dict, action: str) -> int:
        async with aiosqlite.connect(self.db_path) as db:
            cur = await db.execute(
                "INSERT INTO medal_requests (pack_id, tier, discord_user_id, discord_name, action, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?)", (pack_id, tier, user["user_id"], user["name"], action, _now()))
            await db.commit()
            return cur.lastrowid

    async def get_request(self, request_id: int) -> Optional[Dict]:
        async with aiosqlite.connect(self.db_path) as db:
            db.row_factory = aiosqlite.Row
            cur = await db.execute("SELECT * FROM medal_requests WHERE id = ?", (request_id,))
            row = await cur.fetchone()
        return dict(row) if row else None

    async def pending(self) -> List[Dict]:
        async with aiosqlite.connect(self.db_path) as db:
            db.row_factory = aiosqlite.Row
            cur = await db.execute("SELECT * FROM medal_requests WHERE status = 'pending' ORDER BY id")
            return [dict(r) for r in await cur.fetchall()]

    async def finish(self, request_id: int, ok: bool, reason: Optional[str] = None):
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute("UPDATE medal_requests SET status = ?, reason = ?, done_at = ? WHERE id = ?",
                             ("ok" if ok else "rejected", reason, _now(), request_id))
            await db.commit()
