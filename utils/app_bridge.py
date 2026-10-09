"""Brücke zwischen Web-App und Bot (data/webapp.db): Discord-Verknüpfung und Medaillen-Meldungen.

Ablauf:
  1. In Discord /tracker-verknüpfen -> der Bot legt einen Code an (10 Minuten gültig, einmal benutzbar).
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
CREATE TABLE IF NOT EXISTS app_settings (key TEXT PRIMARY KEY, value TEXT);
CREATE TABLE IF NOT EXISTS app_admins (discord_user_id TEXT PRIMARY KEY, added_at TEXT);
CREATE TABLE IF NOT EXISTS blocked_users (discord_user_id TEXT PRIMARY KEY, discord_name TEXT, blocked_at TEXT);
CREATE TABLE IF NOT EXISTS sync_marks (discord_user_id TEXT PRIMARY KEY, synced_at TEXT);
-- je GTCHA-Konto (Fingerabdruck der Mitglieds-ID aus dem Lesezeichen; "default" = Lesezeichen ohne Kennung)
CREATE TABLE IF NOT EXISTS sync_accounts (discord_user_id TEXT, account TEXT, first_at TEXT, synced_at TEXT,
    PRIMARY KEY (discord_user_id, account));
-- wie viele GTCHA-Konten eine Person hat (vom Admin eingestellt, Standard 1)
CREATE TABLE IF NOT EXISTS account_counts (discord_user_id TEXT PRIMARY KEY, expected INTEGER);
CREATE TABLE IF NOT EXISTS auto_claims (
    discord_user_id TEXT, card_key TEXT, pack_id INTEGER, tier TEXT, request_id INTEGER, created_at TEXT,
    PRIMARY KEY (discord_user_id, card_key));
"""
MAX_IMPORT_BYTES = 8_000_000
# Was Discord zu sehen bekommt (App und VPS haben immer alles):
#   discord_mode  "slim" = abgespeckt, "full" = alles wie früher
#   discord_delay Minuten, um die automatisch erkannte Hits und in der App gemeldete Medaillen in Discord später erscheinen
#   premium_mode  Premium-Foren: "full" = alle Infos wie in der App, "slim" = abgespeckt (immer mit Verzögerung)
DEFAULT_SETTINGS = {"discord_mode": "minimal", "discord_delay": "30", "premium_mode": "full"}
DISCORD_MODES = ("minimal", "slim", "full")
PREMIUM_MODES = ("slim", "full")
MAX_DELAY_MINUTES = 24 * 60
KEEP_IMPORTS = 24    # je Person (~3 Läufe; der Verlauf liegt zusammengeführt in user_history)


def _hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def _merge_areas(area: str, parts: List[Dict]) -> Dict:
    """Gleichen Bereich mehrerer GTCHA-Konten zusammenführen (ein Konto: unverändert)."""
    if len(parts) == 1:
        return parts[0]
    parts = sorted(parts, key=lambda p: p.get("updated_at") or "")
    merged = {"updated_at": parts[-1].get("updated_at"), "gap": any(p.get("gap") for p in parts)}
    if any("items" in p for p in parts):
        items = [i for p in parts for i in p.get("items") or []]
        key = "t" if area == "coins" else "date"
        merged["items"] = sorted(items, key=lambda i: str(i.get(key) or ""), reverse=True)
    if any("info" in p for p in parts):
        merged["info"] = {k: v for p in parts for k, v in (p.get("info") or {}).items()}   # neuester Stand gewinnt
    return merged


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
            # Einmalig: wer vor den Konto-Kennungen übertragen hat, bekommt einen festen Eintrag "default"
            # (danach zählt nur noch sync_accounts - so lässt sich ein Konto auch wirklich zurücksetzen)
            cur = await db.execute("SELECT value FROM app_settings WHERE key = 'sync_accounts_migrated'")
            if not await cur.fetchone():
                await db.execute("""
                    INSERT OR IGNORE INTO sync_accounts (discord_user_id, account, first_at, synced_at)
                    SELECT u, 'default', t, t FROM (
                        SELECT discord_user_id AS u, max(t) AS t FROM (
                            SELECT discord_user_id, updated_at AS t FROM user_history
                            UNION ALL SELECT discord_user_id, synced_at FROM sync_marks)
                        GROUP BY discord_user_id)
                    WHERE t IS NOT NULL AND u NOT IN (SELECT discord_user_id FROM sync_accounts)""")
                await db.execute("INSERT INTO app_settings (key, value) VALUES ('sync_accounts_migrated', '1')")
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
                                   "WHERE code = ? AND used = 0 AND created_at >= ? AND discord_user_id NOT IN "
                                   "(SELECT discord_user_id FROM blocked_users)", (code, cutoff))
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

    # --- Einstellungen (Discord-Ausgabe) und Admins ---
    async def settings(self) -> Dict[str, str]:
        async with aiosqlite.connect(self.db_path) as db:
            cur = await db.execute("SELECT key, value FROM app_settings")
            return {**DEFAULT_SETTINGS, **{k: v for k, v in await cur.fetchall()}}

    async def discord_view(self) -> Dict:
        """{"mode", "slim", "minimal", "delay": Sekunden} - Verzögerung nur im schlanken/minimalen Modus.
        minimal: nur Grundinfos, Pack-Updates und Medaillen von euch (keine Auswertungen, keine Hit-Liste)."""
        s = await self.settings()
        mode = s.get("discord_mode") if s.get("discord_mode") in DISCORD_MODES else "minimal"
        slim = mode != "full"
        try:
            minutes = max(0, min(MAX_DELAY_MINUTES, int(s.get("discord_delay") or 0)))
        except ValueError:
            minutes = 0
        premium = s.get("premium_mode") if s.get("premium_mode") in PREMIUM_MODES else "full"
        return {"mode": mode, "slim": slim, "minimal": mode == "minimal", "delay": minutes * 60 if slim else 0,
                "delay_minutes": minutes, "premium_mode": premium}

    async def set_setting(self, key: str, value: str):
        if key not in DEFAULT_SETTINGS:
            raise ValueError(key)
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute("INSERT OR REPLACE INTO app_settings (key, value) VALUES (?, ?)", (key, str(value)))
            await db.commit()

    async def add_admin(self, user_id) -> None:
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute("INSERT OR IGNORE INTO app_admins (discord_user_id, added_at) VALUES (?, ?)",
                             (str(user_id), _now()))
            await db.commit()

    async def set_admins(self, user_ids) -> None:
        """Admin-Liste ersetzen (nicht ergänzen)."""
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute("DELETE FROM app_admins")
            await db.executemany("INSERT INTO app_admins (discord_user_id, added_at) VALUES (?, ?)",
                                 [(str(u), _now()) for u in dict.fromkeys(user_ids)])
            await db.commit()

    async def admins(self) -> List[Dict]:
        """Admins aus der Tabelle (nur noch zur Anzeige; entschieden wird über APP_ADMIN_IDS)."""
        async with aiosqlite.connect(self.db_path) as db:
            cur = await db.execute("SELECT discord_user_id FROM app_admins")
            ids = [r[0] for r in await cur.fetchall()]
        return await self.names(ids)

    async def names(self, user_ids) -> List[Dict]:
        """Discord-IDs mit dem Namen aus der Verknüpfung (falls verknüpft)."""
        out = []
        async with aiosqlite.connect(self.db_path) as db:
            for u in user_ids:
                cur = await db.execute("SELECT discord_name FROM devices WHERE discord_user_id = ? "
                                       "ORDER BY created_at DESC LIMIT 1", (str(u),))
                row = await cur.fetchone()
                out.append({"user_id": str(u), "name": row[0] if row else None})
        return out

    # --- Sperren (Admin) ---
    async def block(self, user_id: str, name: Optional[str] = None) -> None:
        """Alle Geräte abmelden, offene Codes löschen, neues Verknüpfen verhindern."""
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute("INSERT OR REPLACE INTO blocked_users (discord_user_id, discord_name, blocked_at) "
                             "VALUES (?, ?, ?)", (str(user_id), name, _now()))
            await db.execute("DELETE FROM devices WHERE discord_user_id = ?", (str(user_id),))
            await db.execute("DELETE FROM link_codes WHERE discord_user_id = ?", (str(user_id),))
            await db.commit()

    async def unblock(self, user_id: str) -> None:
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute("DELETE FROM blocked_users WHERE discord_user_id = ?", (str(user_id),))
            await db.commit()

    async def is_blocked(self, user_id) -> bool:
        async with aiosqlite.connect(self.db_path) as db:
            cur = await db.execute("SELECT 1 FROM blocked_users WHERE discord_user_id = ?", (str(user_id),))
            return await cur.fetchone() is not None

    async def known_users(self) -> List[Dict]:
        """Alle Personen mit verknüpftem Gerät oder übertragenen Daten: ID, Name, Geräte, zuletzt aktiv."""
        async with aiosqlite.connect(self.db_path) as db:
            cur = await db.execute(
                "SELECT discord_user_id, max(discord_name), count(*), max(COALESCE(last_seen, created_at)) "
                "FROM devices GROUP BY discord_user_id")
            users = {u: {"user_id": u, "name": n, "devices": c, "last_seen": s} for u, n, c, s in await cur.fetchall()}
            cur = await db.execute("SELECT DISTINCT discord_user_id FROM user_history")
            for (u,) in await cur.fetchall():
                users.setdefault(u, {"user_id": u, "name": None, "devices": 0, "last_seen": None})
            cur = await db.execute("SELECT discord_user_id, discord_name FROM blocked_users")
            for u, n in await cur.fetchall():
                users.setdefault(u, {"user_id": u, "name": n, "devices": 0, "last_seen": None})
                users[u]["blocked"] = True
                users[u]["name"] = users[u]["name"] or n
            # Name auch für Personen ohne Gerät (z. B. gesperrt) aus früheren Codes
            for u in users.values():
                if not u["name"]:
                    cur = await db.execute("SELECT discord_name FROM link_codes WHERE discord_user_id = ? "
                                           "ORDER BY created_at DESC LIMIT 1", (u["user_id"],))
                    row = await cur.fetchone()
                    u["name"] = row[0] if row else None
        return list(users.values())

    async def is_admin(self, user_id) -> bool:
        async with aiosqlite.connect(self.db_path) as db:
            cur = await db.execute("SELECT 1 FROM app_admins WHERE discord_user_id = ?", (str(user_id),))
            return await cur.fetchone() is not None

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

    async def raw_import_users(self) -> List[str]:
        async with aiosqlite.connect(self.db_path) as db:
            cur = await db.execute("SELECT DISTINCT discord_user_id FROM user_imports")
            return [r[0] for r in await cur.fetchall()]

    async def delete_raw_imports(self):
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute("DELETE FROM user_imports")
            await db.commit()
            await db.execute("VACUUM")

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
    # Je GTCHA-Konto eigene Bereiche ("shipped@id:94"); ohne Konto-Kennung ohne Zusatz. Gelesen wird für die
    # Anzeige alles zusammen (mehrere Konten einer Person), geschrieben je Konto - sonst überschreibt ein Konto
    # beim vollständigen Übertragen die Karten des anderen.
    async def get_history(self, user_id: str) -> Dict:
        """Alle Konten einer Person zusammen: Karten und Buchungen vereint (neueste zuerst)."""
        async with aiosqlite.connect(self.db_path) as db:
            cur = await db.execute("SELECT area, data, updated_at FROM user_history WHERE discord_user_id = ?",
                                   (str(user_id),))
            rows = await cur.fetchall()
        groups: Dict[str, List[Dict]] = {}
        for area, data, updated in rows:
            groups.setdefault(area.split("@", 1)[0], []).append({**json.loads(data), "updated_at": updated})
        return {area: _merge_areas(area, parts) for area, parts in groups.items()}

    async def get_account_history(self, user_id: str, account: Optional[str]) -> Dict:
        """Nur die Bereiche eines Kontos (ohne Zusatz im Namen) - Grundlage für "nur Neues anhängen"."""
        suffix = f"@{account}" if account else None
        async with aiosqlite.connect(self.db_path) as db:
            cur = await db.execute("SELECT area, data, updated_at FROM user_history WHERE discord_user_id = ?",
                                   (str(user_id),))
            rows = await cur.fetchall()
        out = {}
        for area, data, updated in rows:
            if (suffix and area.endswith(suffix)) or (not suffix and "@" not in area):
                out[area.split("@", 1)[0]] = {**json.loads(data), "updated_at": updated}
        return out

    async def adopt_default_history(self, user_id: str, account: str) -> None:
        """Erstes Übertragen mit Konto-Kennung: der bisherige Verlauf ohne Kennung gehört ab jetzt zu diesem Konto
        (nur wenn noch kein Konto eigene Bereiche hat)."""
        async with aiosqlite.connect(self.db_path) as db:
            cur = await db.execute("SELECT 1 FROM user_history WHERE discord_user_id = ? AND area LIKE '%@%' LIMIT 1",
                                   (str(user_id),))
            if not await cur.fetchone():
                await db.execute("UPDATE user_history SET area = area || ? WHERE discord_user_id = ? "
                                 "AND area NOT LIKE '%@%'", (f"@{account}", str(user_id)))
                await db.commit()

    async def set_history(self, user_id: str, areas: Dict[str, Dict], account: Optional[str] = None):
        async with aiosqlite.connect(self.db_path) as db:
            for area, data in areas.items():
                data = {k: v for k, v in data.items() if k != "updated_at"}
                name = f"{area}@{account}" if account else area
                await db.execute("INSERT OR REPLACE INTO user_history (discord_user_id, area, data, updated_at) "
                                 "VALUES (?, ?, ?, ?)", (str(user_id), name, json.dumps(data, ensure_ascii=False), _now()))
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

    async def auto_claims_for(self, pack_id: int) -> Dict[str, Dict]:
        """Per Lesezeichen gemeldete Medaillen eines Banners: Stufe -> {user_id, pulled_on (Anfragedatum)}."""
        async with aiosqlite.connect(self.db_path) as db:
            cur = await db.execute("SELECT tier, discord_user_id, card_key FROM auto_claims WHERE pack_id = ?",
                                   (pack_id,))
            rows = await cur.fetchall()
        return {tier: {"user_id": str(u), "pulled_on": (key.split("@", 1)[1].split("#")[0] if "@" in key else None)}
                for tier, u, key in rows}

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

    async def mark_synced(self, user_id: str, account: Optional[str] = None) -> None:
        """Erfolgreiches Übertragen merken (auch wenn nichts Neues dabei war - zählt für die 7-Tage-Pflicht).
        account: Fingerabdruck des GTCHA-Kontos; ohne (altes Lesezeichen) zählt es als "default"."""
        now, acc = _now(), account or "default"
        async with aiosqlite.connect(self.db_path) as db:
            if account:   # mit Kennung: der Eintrag ohne Kennung (altes Lesezeichen) ist damit überholt
                await db.execute("DELETE FROM sync_accounts WHERE discord_user_id = ? AND account = 'default'",
                                 (str(user_id),))
            await db.execute("INSERT INTO sync_accounts (discord_user_id, account, first_at, synced_at) "
                             "VALUES (?, ?, ?, ?) ON CONFLICT(discord_user_id, account) DO UPDATE SET "
                             "synced_at = excluded.synced_at", (str(user_id), acc, now, now))
            await db.execute("INSERT OR REPLACE INTO sync_marks (discord_user_id, synced_at) VALUES (?, ?)",
                             (str(user_id), now))
            await db.commit()

    async def sync_overview(self) -> Dict[str, Dict]:
        """Discord-ID -> {"expected": Anzahl Konten, "accounts": [{account, first_at, synced_at}, ...]} (älteste
        Konten zuerst). Wer vor den Konto-Kennungen übertragen hat, hat beim Start ein Konto "default" bekommen."""
        async with aiosqlite.connect(self.db_path) as db:
            cur = await db.execute("SELECT discord_user_id, account, first_at, synced_at FROM sync_accounts "
                                   "ORDER BY first_at")
            out: Dict[str, Dict] = {}
            for u, acc, first, synced in await cur.fetchall():
                out.setdefault(u, {"expected": 1, "accounts": []})["accounts"].append(
                    {"account": acc, "first_at": first, "synced_at": synced})
            cur = await db.execute("SELECT discord_user_id, expected FROM account_counts")
            for u, n in await cur.fetchall():
                out.setdefault(u, {"expected": 1, "accounts": []})["expected"] = max(1, int(n or 1))
            return out

    async def rename_account(self, user_id: str, old: str, new: str) -> None:
        """Konto, das bisher nur als Fingerabdruck bekannt war, unter der Mitglieds-ID weiterführen."""
        async with aiosqlite.connect(self.db_path) as db:
            cur = await db.execute("SELECT 1 FROM sync_accounts WHERE discord_user_id = ? AND account = ?",
                                   (str(user_id), new))
            if not await cur.fetchone():
                await db.execute("UPDATE sync_accounts SET account = ? WHERE discord_user_id = ? AND account = ?",
                                 (new, str(user_id), old))
            else:
                await db.execute("DELETE FROM sync_accounts WHERE discord_user_id = ? AND account = ?",
                                 (str(user_id), old))
            await db.commit()

    async def set_expected_accounts(self, user_id: str, expected: int) -> None:
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute("INSERT OR REPLACE INTO account_counts (discord_user_id, expected) VALUES (?, ?)",
                             (str(user_id), max(1, min(5, int(expected)))))
            await db.commit()

    async def remove_account(self, user_id: str, account: str) -> None:
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute("DELETE FROM sync_accounts WHERE discord_user_id = ? AND account = ?",
                             (str(user_id), account))
            await db.commit()

    async def last_syncs(self) -> Dict[str, str]:
        """Discord-ID -> Zeitpunkt des letzten Übertragens (nur wer schon einmal übertragen hat)."""
        async with aiosqlite.connect(self.db_path) as db:
            cur = await db.execute("SELECT discord_user_id, max(updated_at) FROM user_history GROUP BY discord_user_id")
            out = {u: t for u, t in await cur.fetchall() if t}
            cur = await db.execute("SELECT discord_user_id, synced_at FROM sync_marks")
            for u, t in await cur.fetchall():
                if t and t > out.get(u, ""):
                    out[u] = t
            return out

    async def last_sync(self, user_id: str) -> Optional[str]:
        return (await self.last_syncs()).get(str(user_id))

    async def sync_state(self, user_id: str) -> Dict:
        return (await self.sync_overview()).get(str(user_id)) or {"expected": 1, "accounts": []}

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
