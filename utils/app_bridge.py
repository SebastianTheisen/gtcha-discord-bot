"""Brücke zwischen Web-App und Bot (data/webapp.db): Discord-Verknüpfung und Medaillen-Meldungen.

Ablauf:
  1. In Discord /app-verknüpfen -> der Bot legt einen Code an (10 Minuten gültig, einmal benutzbar).
  2. In der App den Code eingeben -> die App legt ein Gerät an und gibt ihm ein geheimes Token.
  3. Medaille in der App melden -> die App legt eine Meldung an; der Bot holt sie ab, prüft sie wie
     eine Medaille in Discord, postet im Thread und trägt das Ergebnis ein (ok / abgelehnt + Grund).
"""

import hashlib
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
"""


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
            await db.commit()

    # --- Verknüpfung (Bot legt Code an, App löst ihn ein) ---
    async def create_code(self, user_id: int, name: str) -> str:
        code = "".join(secrets.choice(CODE_ALPHABET) for _ in range(CODE_LENGTH))
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute("DELETE FROM link_codes WHERE discord_user_id = ? AND used = 0", (str(user_id),))
            await db.execute("INSERT OR REPLACE INTO link_codes (code, discord_user_id, discord_name, created_at) "
                             "VALUES (?, ?, ?, ?)", (code, str(user_id), name, _now()))
            await db.commit()
        return code

    async def redeem_code(self, code: str) -> Optional[Dict]:
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
            await db.execute("INSERT INTO devices (token_hash, discord_user_id, discord_name, created_at) "
                             "VALUES (?, ?, ?, ?)", (_hash(token), row[0], row[1], _now()))
            await db.commit()
        return {"token": token, "user_id": row[0], "name": row[1]}

    async def device(self, token: Optional[str]) -> Optional[Dict]:
        if not token:
            return None
        async with aiosqlite.connect(self.db_path) as db:
            cur = await db.execute("SELECT discord_user_id, discord_name FROM devices WHERE token_hash = ?",
                                   (_hash(token),))
            row = await cur.fetchone()
        return {"user_id": row[0], "name": row[1]} if row else None

    async def unlink(self, token: str):
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute("DELETE FROM devices WHERE token_hash = ?", (_hash(token),))
            await db.commit()

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
