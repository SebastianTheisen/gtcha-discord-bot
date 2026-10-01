"""
Datenbank-Operationen
"""

import json

import aiosqlite
from pathlib import Path
from typing import Optional, Dict, Any, List
from datetime import datetime, timedelta

from loguru import logger
from config import DATABASE_PATH


class Database:
    def __init__(self, db_path: str = DATABASE_PATH):
        self.db_path = db_path
        Path(db_path).parent.mkdir(parents=True, exist_ok=True)

    async def init(self):
        """Erstellt Tabellen."""
        async with aiosqlite.connect(self.db_path) as db:
            await db.executescript("""
                CREATE TABLE IF NOT EXISTS banners (
                    pack_id INTEGER PRIMARY KEY,
                    category TEXT,
                    title TEXT,
                    best_hit TEXT,
                    price_coins INTEGER,
                    current_packs INTEGER,
                    total_packs INTEGER,
                    entries_per_day INTEGER,
                    sale_end_date TEXT,
                    image_url TEXT,
                    detail_page_url TEXT,
                    is_active INTEGER DEFAULT 1,
                    not_found_count INTEGER DEFAULT 0,
                    created_at TEXT,
                    updated_at TEXT
                );

                CREATE TABLE IF NOT EXISTS discord_threads (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    banner_id INTEGER,
                    thread_id INTEGER UNIQUE,
                    channel_id INTEGER,
                    starter_message_id INTEGER,
                    is_expired INTEGER DEFAULT 0,
                    created_at TEXT,
                    FOREIGN KEY (banner_id) REFERENCES banners(pack_id)
                );

                CREATE TABLE IF NOT EXISTS medals (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    thread_id INTEGER,
                    tier TEXT,
                    user_id INTEGER,
                    created_at TEXT,
                    UNIQUE(thread_id, tier)
                );

                CREATE TABLE IF NOT EXISTS bot_meta (
                    key TEXT PRIMARY KEY,
                    value TEXT
                );

                CREATE TABLE IF NOT EXISTS pack_history (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    banner_id INTEGER,
                    old_count INTEGER,
                    new_count INTEGER,
                    changed_at TEXT,
                    FOREIGN KEY (banner_id) REFERENCES banners(pack_id)
                );
            """)

            # Migration: Füge not_found_count Spalte hinzu falls nicht vorhanden
            try:
                await db.execute("ALTER TABLE banners ADD COLUMN not_found_count INTEGER DEFAULT 0")
                await db.commit()
                logger.info("Migration: not_found_count Spalte hinzugefügt")
            except:
                pass  # Spalte existiert bereits

            # Migration: Füge probability_message_id Spalte hinzu falls nicht vorhanden
            try:
                await db.execute("ALTER TABLE discord_threads ADD COLUMN probability_message_id INTEGER")
                await db.commit()
                logger.info("Migration: probability_message_id Spalte hinzugefügt")
            except:
                pass  # Spalte existiert bereits

            # Migration: Füge Medaillen-Spalten hinzu (t1=Gold, t2=Silber, t3=Bronze)
            for col in ['t1_claimed', 't2_claimed', 't3_claimed']:
                try:
                    await db.execute(f"ALTER TABLE discord_threads ADD COLUMN {col} INTEGER DEFAULT 0")
                    await db.commit()
                    logger.info(f"Migration: {col} Spalte hinzugefügt")
                except:
                    pass  # Spalte existiert bereits

            # Migration: Bestehende Medaillen aus medals-Tabelle in claimed-Spalten übertragen
            try:
                # T1 (Gold)
                await db.execute("""
                    UPDATE discord_threads SET t1_claimed = 1
                    WHERE thread_id IN (SELECT thread_id FROM medals WHERE tier = 'T1')
                    AND (t1_claimed IS NULL OR t1_claimed = 0)
                """)
                # T2 (Silber)
                await db.execute("""
                    UPDATE discord_threads SET t2_claimed = 1
                    WHERE thread_id IN (SELECT thread_id FROM medals WHERE tier = 'T2')
                    AND (t2_claimed IS NULL OR t2_claimed = 0)
                """)
                # T3 (Bronze)
                await db.execute("""
                    UPDATE discord_threads SET t3_claimed = 1
                    WHERE thread_id IN (SELECT thread_id FROM medals WHERE tier = 'T3')
                    AND (t3_claimed IS NULL OR t3_claimed = 0)
                """)
                await db.commit()
                logger.info("Migration: Bestehende Medaillen in claimed-Spalten übertragen")
            except Exception as e:
                logger.debug(f"Migration Medaillen-Sync: {e}")

            # Migration: Kartenpool (JSON) pro Banner, Top-5-Nachricht und Lohnt-sich-Hinweis pro Thread
            for table, col in [('banners', 'card_pool TEXT'),
                               ('banners', 'decided_value INTEGER'),
                               ('banners', 'ship_count INTEGER'),
                               ('banners', 'ship_value INTEGER'),
                               ('banners', 'pulled_cards TEXT'),
                               ('banners', 'unsure_cards TEXT'),
                               ('discord_threads', 'hit_message_ids TEXT'),
                               ('discord_threads', 'endspurt_sent INTEGER DEFAULT 0'),
                               ('banners', 'conditions TEXT'),
                               ('banners', 'site_stats TEXT'),
                               ('discord_threads', 'hit_list_sig TEXT'),
                               ('discord_threads', 'title TEXT'),
                               ('banners', 'pool_updated_at TEXT'),
                               ('banners', 'starts_at INTEGER'),
                               ('banners', 'start_announced INTEGER DEFAULT 0'),
                               ('discord_threads', 'top5_message_id INTEGER'),
                               ('discord_threads', 'value_alert_sent INTEGER DEFAULT 0')]:
                try:
                    await db.execute(f"ALTER TABLE {table} ADD COLUMN {col}")
                    await db.commit()
                    logger.info(f"Migration: {table}.{col.split()[0]} hinzugefügt")
                except Exception:
                    pass  # Spalte existiert bereits

            # Versand-Abgleich mit Toleranz: bisherige Versand-Summen einmal neu auswerten lassen
            cursor = await db.execute("SELECT value FROM bot_meta WHERE key = 'ship_match_version'")
            row = await cursor.fetchone()
            if not row or row[0] != '2':
                await db.execute("UPDATE banners SET ship_count = NULL, ship_value = NULL")
                await db.execute("INSERT OR REPLACE INTO bot_meta (key, value) VALUES ('ship_match_version', '2')")
                await db.commit()
                logger.info("Migration: Versand-Summen werden mit Toleranz neu ausgewertet")

            # Performance-Indexes hinzufügen (IF NOT EXISTS für idempotente Migration)
            await db.executescript("""
                CREATE INDEX IF NOT EXISTS idx_banners_is_active ON banners(is_active);
                CREATE INDEX IF NOT EXISTS idx_discord_threads_banner_id ON discord_threads(banner_id);
                CREATE INDEX IF NOT EXISTS idx_discord_threads_is_expired ON discord_threads(is_expired);
            """)

            await db.commit()

    async def get_banner(self, pack_id: int) -> Optional[Dict]:
        async with aiosqlite.connect(self.db_path) as db:
            db.row_factory = aiosqlite.Row
            cursor = await db.execute(
                "SELECT * FROM banners WHERE pack_id = ?", (pack_id,)
            )
            row = await cursor.fetchone()
            return dict(row) if row else None

    async def save_banner(self, banner) -> None:
        now = datetime.now().isoformat()
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute("""
                INSERT OR REPLACE INTO banners
                (pack_id, category, title, best_hit, price_coins, current_packs,
                 total_packs, entries_per_day, sale_end_date, image_url,
                 detail_page_url, is_active, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1, ?, ?)
            """, (
                banner.pack_id, banner.category, banner.title, banner.best_hit,
                banner.price_coins, banner.current_packs, banner.total_packs,
                banner.entries_per_day, banner.sale_end_date, banner.image_url,
                banner.detail_page_url, now, now
            ))
            await db.commit()

    async def update_banner_packs(self, pack_id: int, new_count: int) -> None:
        now = datetime.now().isoformat()
        async with aiosqlite.connect(self.db_path) as db:
            # Hole alten Wert
            cursor = await db.execute(
                "SELECT current_packs FROM banners WHERE pack_id = ?", (pack_id,)
            )
            row = await cursor.fetchone()
            old_count = row[0] if row else None

            # Update Banner
            await db.execute(
                "UPDATE banners SET current_packs = ?, updated_at = ? WHERE pack_id = ?",
                (new_count, now, pack_id)
            )

            # History speichern
            if old_count is not None:
                await db.execute("""
                    INSERT INTO pack_history (banner_id, old_count, new_count, changed_at)
                    VALUES (?, ?, ?, ?)
                """, (pack_id, old_count, new_count, now))

            await db.commit()

    async def get_banners_without_pool(self, limit: int, prefer_ids: List[int],
                                       max_age_hours: int = 24) -> List[int]:
        """Aktive Banner mit Thread, deren Kartenpool fehlt oder älter als max_age_hours ist.

        Reihenfolge: prefer_ids (z.B. neue Banner), dann fehlende Pools, dann die ältesten.
        """
        cutoff = (datetime.now() - timedelta(hours=max_age_hours)).isoformat()
        async with aiosqlite.connect(self.db_path) as db:
            cursor = await db.execute("""
                SELECT b.pack_id, b.card_pool IS NULL OR b.card_pool NOT LIKE '%"version": 2%' AS missing,
                       COALESCE(b.pool_updated_at, '') AS updated
                FROM banners b
                JOIN discord_threads t ON t.banner_id = b.pack_id AND t.is_expired = 0
                WHERE b.is_active = 1 AND (b.card_pool IS NULL OR b.card_pool NOT LIKE '%"version": 2%'
                                           OR b.pool_updated_at IS NULL OR b.pool_updated_at < ?)
            """, (cutoff,))
            rows = await cursor.fetchall()
        preferred = set(prefer_ids)
        rows.sort(key=lambda r: (r[0] not in preferred, not r[1], r[2]))
        return [r[0] for r in rows[:limit]]

    async def save_card_pool(self, pack_id: int, pool: Dict) -> None:
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute("UPDATE banners SET card_pool = ?, pool_updated_at = ? WHERE pack_id = ?",
                             (json.dumps(pool, ensure_ascii=False), datetime.now().isoformat(), pack_id))
            await db.commit()

    async def get_card_pool(self, pack_id: int) -> Optional[Dict]:
        async with aiosqlite.connect(self.db_path) as db:
            cursor = await db.execute("SELECT card_pool FROM banners WHERE pack_id = ?", (pack_id,))
            row = await cursor.fetchone()
        return json.loads(row[0]) if row and row[0] else None

    async def get_pull_tracking(self, pack_id: int) -> Dict:
        """Zuletzt gesehene Zähler (None = noch nie gesehen) und als gezogen erkannte Karten."""
        async with aiosqlite.connect(self.db_path) as db:
            cursor = await db.execute(
                "SELECT decided_value, ship_count, ship_value, pulled_cards, unsure_cards "
                "FROM banners WHERE pack_id = ?", (pack_id,))
            row = await cursor.fetchone()
        if not row:
            return {"decided_value": None, "ship_count": None, "ship_value": None, "pulled": [], "unsure": []}
        return {"decided_value": row[0], "ship_count": row[1], "ship_value": row[2],
                "pulled": json.loads(row[3]) if row[3] else [],
                "unsure": json.loads(row[4]) if row[4] else []}

    async def set_pull_tracking(self, pack_id: int, decided_value: Optional[int], ship_count: Optional[int],
                                ship_value: Optional[int], pulled: List[str], unsure: List[Dict]) -> None:
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute(
                "UPDATE banners SET decided_value = ?, ship_count = ?, ship_value = ?, pulled_cards = ?, "
                "unsure_cards = ? WHERE pack_id = ?",
                (decided_value, ship_count, ship_value, json.dumps(pulled), json.dumps(unsure), pack_id))
            await db.commit()

    async def get_sales_since(self, pack_id: int, since: datetime) -> tuple:
        """(verkaufte Packs seit `since`, Zeitpunkt der ersten Änderung in diesem Zeitraum oder None)."""
        async with aiosqlite.connect(self.db_path) as db:
            cursor = await db.execute(
                "SELECT COALESCE(SUM(old_count - new_count), 0), MIN(changed_at) FROM pack_history "
                "WHERE banner_id = ? AND changed_at >= ? AND new_count < old_count",
                (pack_id, since.isoformat()))
            sold, first = await cursor.fetchone()
        return int(sold or 0), datetime.fromisoformat(first) if first else None

    async def set_endspurt_sent(self, thread_id: int) -> None:
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute("UPDATE discord_threads SET endspurt_sent = 1 WHERE thread_id = ?", (thread_id,))
            await db.commit()

    async def get_meta(self, key: str) -> Optional[str]:
        async with aiosqlite.connect(self.db_path) as db:
            cursor = await db.execute("SELECT value FROM bot_meta WHERE key = ?", (key,))
            row = await cursor.fetchone()
        return row[0] if row else None

    async def set_meta(self, key: str, value: str) -> None:
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute("INSERT OR REPLACE INTO bot_meta (key, value) VALUES (?, ?)", (key, value))
            await db.commit()

    async def update_site_stats(self, pack_id: int, stats: Dict) -> bool:
        """Speichert die Versand-Zahlen der Seite; True, wenn sie sich geändert haben."""
        new = json.dumps(stats, sort_keys=True)
        async with aiosqlite.connect(self.db_path) as db:
            cursor = await db.execute("SELECT site_stats FROM banners WHERE pack_id = ?", (pack_id,))
            row = await cursor.fetchone()
            if not row or row[0] == new:
                return False
            await db.execute("UPDATE banners SET site_stats = ? WHERE pack_id = ?", (new, pack_id))
            await db.commit()
        return True

    async def update_conditions(self, pack_id: int, conditions: Dict) -> bool:
        """Speichert die Kaufbedingungen; True, wenn sie sich geändert haben."""
        new = json.dumps(conditions, sort_keys=True, ensure_ascii=False)
        async with aiosqlite.connect(self.db_path) as db:
            cursor = await db.execute("SELECT conditions FROM banners WHERE pack_id = ?", (pack_id,))
            row = await cursor.fetchone()
            if not row or row[0] == new:
                return False
            await db.execute("UPDATE banners SET conditions = ? WHERE pack_id = ?", (new, pack_id))
            await db.commit()
        return True

    async def set_thread_title(self, thread_id: int, title: str) -> None:
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute("UPDATE discord_threads SET title = ? WHERE thread_id = ?", (title, thread_id))
            await db.commit()

    async def set_start(self, pack_id: int, starts_at: Optional[int], announced: bool) -> None:
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute("UPDATE banners SET starts_at = ?, start_announced = ? WHERE pack_id = ?",
                             (starts_at, 1 if announced else 0, pack_id))
            await db.commit()

    async def update_price(self, pack_id: int, price: int) -> bool:
        """Setzt den Packpreis, wenn er fehlt oder abweicht; True bei Änderung."""
        async with aiosqlite.connect(self.db_path) as db:
            cursor = await db.execute(
                "UPDATE banners SET price_coins = ? WHERE pack_id = ? AND (price_coins IS NULL OR price_coins != ?)",
                (price, pack_id, price))
            await db.commit()
            return cursor.rowcount > 0

    async def set_hit_list_sig(self, thread_id: int, sig: str) -> None:
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute("UPDATE discord_threads SET hit_list_sig = ? WHERE thread_id = ?", (sig, thread_id))
            await db.commit()

    async def set_hit_message_ids(self, thread_id: int, message_ids: List[int]) -> None:
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute("UPDATE discord_threads SET hit_message_ids = ?, top5_message_id = ? WHERE thread_id = ?",
                             (json.dumps(message_ids), message_ids[0] if message_ids else None, thread_id))
            await db.commit()

    async def set_top5_message_id(self, thread_id: int, message_id: int) -> None:
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute("UPDATE discord_threads SET top5_message_id = ? WHERE thread_id = ?",
                             (message_id, thread_id))
            await db.commit()

    async def set_value_alert_sent(self, thread_id: int, sent: bool) -> None:
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute("UPDATE discord_threads SET value_alert_sent = ? WHERE thread_id = ?",
                             (1 if sent else 0, thread_id))
            await db.commit()

    async def update_banner_entries(self, pack_id: int, entries_per_day: int) -> None:
        """Aktualisiert entries_per_day für einen Banner."""
        now = datetime.now().isoformat()
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute(
                "UPDATE banners SET entries_per_day = ?, updated_at = ? WHERE pack_id = ?",
                (entries_per_day, now, pack_id)
            )
            await db.commit()

    async def update_banner_urls(self, pack_id: int, image_url: str, detail_page_url: str) -> None:
        """Aktualisiert image_url und detail_page_url für einen Banner (falls nicht NULL)."""
        now = datetime.now().isoformat()
        async with aiosqlite.connect(self.db_path) as db:
            # Nur updaten wenn neuer Wert nicht None ist
            if image_url:
                await db.execute(
                    "UPDATE banners SET image_url = ?, updated_at = ? WHERE pack_id = ?",
                    (image_url, now, pack_id)
                )
            if detail_page_url:
                await db.execute(
                    "UPDATE banners SET detail_page_url = ?, updated_at = ? WHERE pack_id = ?",
                    (detail_page_url, now, pack_id)
                )
            await db.commit()

    async def save_thread(self, banner_id: int, thread_id: int, channel_id: int, starter_message_id: int) -> None:
        now = datetime.now().isoformat()
        async with aiosqlite.connect(self.db_path) as db:
            # Alte Threads für denselben Banner als expired markieren (verhindert doppelte Einträge)
            await db.execute(
                "UPDATE discord_threads SET is_expired = 1 WHERE banner_id = ? AND thread_id != ?",
                (banner_id, thread_id)
            )
            await db.execute("""
                INSERT OR REPLACE INTO discord_threads
                (banner_id, thread_id, channel_id, starter_message_id, created_at)
                VALUES (?, ?, ?, ?, ?)
            """, (banner_id, thread_id, channel_id, starter_message_id, now))
            await db.commit()

    async def get_thread_by_id(self, thread_id: int) -> Optional[Dict]:
        async with aiosqlite.connect(self.db_path) as db:
            db.row_factory = aiosqlite.Row
            cursor = await db.execute(
                "SELECT * FROM discord_threads WHERE thread_id = ?", (thread_id,)
            )
            row = await cursor.fetchone()
            return dict(row) if row else None

    async def get_medal(self, thread_id: int, tier: str) -> Optional[Dict]:
        async with aiosqlite.connect(self.db_path) as db:
            db.row_factory = aiosqlite.Row
            cursor = await db.execute(
                "SELECT * FROM medals WHERE thread_id = ? AND tier = ?",
                (thread_id, tier)
            )
            row = await cursor.fetchone()
            return dict(row) if row else None

    async def get_medals(self, thread_id: int) -> Dict[str, int]:
        """Alle vergebenen Medaillen eines Threads: Stufe (T1-T10) -> Discord-User-ID (0 = unbekannt)."""
        async with aiosqlite.connect(self.db_path) as db:
            cursor = await db.execute("SELECT tier, user_id FROM medals WHERE thread_id = ?", (thread_id,))
            medals = {row[0]: row[1] or 0 for row in await cursor.fetchall()}
            cursor = await db.execute(
                "SELECT t1_claimed, t2_claimed, t3_claimed FROM discord_threads WHERE thread_id = ?", (thread_id,))
            row = await cursor.fetchone()
        for tier, claimed in zip(("T1", "T2", "T3"), row or ()):
            if claimed:
                medals.setdefault(tier, 0)
        return medals

    async def save_medal(self, thread_id: int, tier: str, user_id: int) -> None:
        now = datetime.now().isoformat()
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute("""
                INSERT INTO medals (thread_id, tier, user_id, created_at)
                VALUES (?, ?, ?, ?)
            """, (thread_id, tier, user_id, now))

            # Auch die claimed-Spalte in discord_threads setzen
            col_map = {'T1': 't1_claimed', 'T2': 't2_claimed', 'T3': 't3_claimed'}
            if tier in col_map:
                await db.execute(
                    f"UPDATE discord_threads SET {col_map[tier]} = 1 WHERE thread_id = ?",
                    (thread_id,)
                )
            await db.commit()

    async def get_thread_by_banner_id(self, banner_id: int) -> Optional[Dict]:
        async with aiosqlite.connect(self.db_path) as db:
            db.row_factory = aiosqlite.Row
            # Aktive Threads zuerst (is_expired=0), dann neueste - verhindert Rückgabe alter gelöschter Threads
            cursor = await db.execute(
                "SELECT * FROM discord_threads WHERE banner_id = ? ORDER BY is_expired ASC, id DESC LIMIT 1",
                (banner_id,)
            )
            row = await cursor.fetchone()
            return dict(row) if row else None

    async def delete_thread(self, banner_id: int) -> None:
        async with aiosqlite.connect(self.db_path) as db:
            # Erst Medals löschen die zu diesem Thread gehören
            await db.execute("""
                DELETE FROM medals WHERE thread_id IN (
                    SELECT thread_id FROM discord_threads WHERE banner_id = ?
                )
            """, (banner_id,))
            # Dann Thread löschen
            await db.execute(
                "DELETE FROM discord_threads WHERE banner_id = ?", (banner_id,)
            )
            await db.commit()

    async def delete_banner(self, pack_id: int) -> None:
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute("DELETE FROM banners WHERE pack_id = ?", (pack_id,))
            await db.commit()

    async def get_stats(self) -> Dict[str, int]:
        async with aiosqlite.connect(self.db_path) as db:
            stats = {}

            cursor = await db.execute("SELECT COUNT(*) FROM banners WHERE is_active = 1")
            stats['total_banners'] = (await cursor.fetchone())[0]

            cursor = await db.execute("SELECT COUNT(*) FROM discord_threads WHERE is_expired = 0")
            stats['active_threads'] = (await cursor.fetchone())[0]

            cursor = await db.execute("SELECT COUNT(*) FROM medals")
            stats['total_medals'] = (await cursor.fetchone())[0]

            return stats

    async def get_all_active_banners_basic(self) -> List[Dict]:
        """Gibt pack_id, current_packs und total_packs aller aktiven Banner zurück."""
        async with aiosqlite.connect(self.db_path) as db:
            db.row_factory = aiosqlite.Row
            cursor = await db.execute(
                "SELECT pack_id, current_packs, total_packs FROM banners WHERE is_active = 1"
            )
            rows = await cursor.fetchall()
            return [dict(row) for row in rows]

    async def get_banner_states(self) -> Dict[int, bool]:
        """pack_id -> aktiv? für alle Banner, die der Bot kennt."""
        async with aiosqlite.connect(self.db_path) as db:
            cursor = await db.execute("SELECT pack_id, is_active FROM banners")
            return {row[0]: bool(row[1]) for row in await cursor.fetchall()}

    async def get_active_banners(self) -> Dict[int, Dict]:
        """Alle aktiven Banner als pack_id -> Zeile."""
        async with aiosqlite.connect(self.db_path) as db:
            db.row_factory = aiosqlite.Row
            cursor = await db.execute("SELECT * FROM banners WHERE is_active = 1")
            return {row["pack_id"]: dict(row) for row in await cursor.fetchall()}

    async def get_all_active_banner_ids(self) -> List[int]:
        """Gibt alle aktiven Banner-IDs zurück."""
        async with aiosqlite.connect(self.db_path) as db:
            cursor = await db.execute(
                "SELECT pack_id FROM banners WHERE is_active = 1"
            )
            rows = await cursor.fetchall()
            return [row[0] for row in rows]

    async def batch_reset_not_found_count(self, pack_ids: List[int]) -> None:
        """Setzt not_found_count für alle angegebenen Banner auf 0 (Batch-Update)."""
        if not pack_ids:
            return
        async with aiosqlite.connect(self.db_path) as db:
            placeholders = ','.join('?' * len(pack_ids))
            await db.execute(
                f"UPDATE banners SET not_found_count = 0 WHERE pack_id IN ({placeholders})",
                pack_ids
            )
            await db.commit()

    async def batch_increment_not_found_count(self, pack_ids: List[int], threshold: int = 20) -> List[int]:
        """Erhöht not_found_count für alle Banner um 1 und gibt IDs mit count >= threshold zurück."""
        if not pack_ids:
            return []
        async with aiosqlite.connect(self.db_path) as db:
            placeholders = ','.join('?' * len(pack_ids))
            await db.execute(
                f"UPDATE banners SET not_found_count = not_found_count + 1 WHERE pack_id IN ({placeholders})",
                pack_ids
            )
            await db.commit()
            cursor = await db.execute(
                f"SELECT pack_id FROM banners WHERE pack_id IN ({placeholders}) AND not_found_count >= ?",
                [*pack_ids, threshold]
            )
            rows = await cursor.fetchall()
            return [row[0] for row in rows]

    async def get_archived_thread_ids(self, max_age_hours: int = 1) -> List[int]:
        """Gibt Thread-IDs von alten inaktiven Bannern zurück (für Discord-Löschung)."""
        cutoff = (datetime.now() - timedelta(hours=max_age_hours)).isoformat()
        async with aiosqlite.connect(self.db_path) as db:
            cursor = await db.execute("""
                SELECT dt.thread_id FROM discord_threads dt
                JOIN banners b ON dt.banner_id = b.pack_id
                WHERE b.is_active = 0 AND b.updated_at <= ?
                  AND dt.thread_id IS NOT NULL
            """, (cutoff,))
            return [row[0] for row in await cursor.fetchall()]

    async def purge_archived_data(self, max_age_hours: int = 1) -> int:
        """Löscht archivierte Banner/Threads/Medals/History die älter als max_age_hours sind."""
        cutoff = (datetime.now() - timedelta(hours=max_age_hours)).isoformat()
        async with aiosqlite.connect(self.db_path) as db:
            # Finde alte inaktive Banner
            cursor = await db.execute(
                "SELECT pack_id FROM banners WHERE is_active = 0 AND updated_at <= ?",
                (cutoff,)
            )
            old_ids = [row[0] for row in await cursor.fetchall()]

            if not old_ids:
                return 0

            placeholders = ','.join('?' * len(old_ids))

            # Medals löschen
            await db.execute(f"""
                DELETE FROM medals WHERE thread_id IN (
                    SELECT thread_id FROM discord_threads WHERE banner_id IN ({placeholders})
                )
            """, old_ids)

            # Pack-History löschen
            await db.execute(
                f"DELETE FROM pack_history WHERE banner_id IN ({placeholders})",
                old_ids
            )

            # Threads löschen
            await db.execute(
                f"DELETE FROM discord_threads WHERE banner_id IN ({placeholders})",
                old_ids
            )

            # Banner löschen
            await db.execute(
                f"DELETE FROM banners WHERE pack_id IN ({placeholders})",
                old_ids
            )

            await db.commit()
            return len(old_ids)

    async def mark_banner_inactive(self, pack_id: int) -> None:
        """Markiert einen Banner als inaktiv (statt löschen)."""
        now = datetime.now().isoformat()
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute(
                "UPDATE banners SET is_active = 0, updated_at = ? WHERE pack_id = ?",
                (now, pack_id)
            )
            await db.commit()

    async def mark_thread_expired(self, banner_id: int) -> None:
        """Markiert einen Thread als abgelaufen (statt löschen)."""
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute(
                "UPDATE discord_threads SET is_expired = 1 WHERE banner_id = ?",
                (banner_id,)
            )
            await db.commit()

    async def update_probability_message_id(self, thread_id: int, message_id: int) -> None:
        """Speichert die Message-ID der Wahrscheinlichkeits-Nachricht."""
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute(
                "UPDATE discord_threads SET probability_message_id = ? WHERE thread_id = ?",
                (message_id, thread_id)
            )
            await db.commit()

    async def get_probability_message_id(self, thread_id: int) -> Optional[int]:
        """Gibt die Message-ID der Wahrscheinlichkeits-Nachricht zurück."""
        async with aiosqlite.connect(self.db_path) as db:
            cursor = await db.execute(
                "SELECT probability_message_id FROM discord_threads WHERE thread_id = ?",
                (thread_id,)
            )
            row = await cursor.fetchone()
            return row[0] if row and row[0] else None

    async def get_all_active_banners_with_threads(self) -> List[Dict]:
        """Gibt alle aktiven Banner mit Thread-Daten und Medaillen-Anzahl zurück."""
        async with aiosqlite.connect(self.db_path) as db:
            db.row_factory = aiosqlite.Row
            cursor = await db.execute("""
                SELECT b.pack_id, b.category, b.title, b.best_hit, b.price_coins,
                       b.current_packs, b.total_packs, b.entries_per_day, b.sale_end_date,
                       b.image_url, b.detail_page_url, b.is_active, b.not_found_count,
                       b.created_at, b.updated_at,
                       dt.thread_id,
                       (SELECT COUNT(*) FROM medals m WHERE m.thread_id = dt.thread_id) as medal_count
                FROM banners b
                LEFT JOIN discord_threads dt ON b.pack_id = dt.banner_id
                WHERE b.is_active = 1 AND b.current_packs > 0
            """)
            rows = await cursor.fetchall()
            return [dict(row) for row in rows]
