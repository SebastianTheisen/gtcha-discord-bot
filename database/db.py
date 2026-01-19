"""
Datenbank-Operationen
"""

import aiosqlite
from pathlib import Path
from typing import Optional, Dict, Any, List
from datetime import datetime

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

    async def update_banner_entries(self, pack_id: int, entries_per_day: int) -> None:
        """Aktualisiert entries_per_day für einen Banner."""
        now = datetime.now().isoformat()
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute(
                "UPDATE banners SET entries_per_day = ?, updated_at = ? WHERE pack_id = ?",
                (entries_per_day, now, pack_id)
            )
            await db.commit()

    async def save_thread(self, banner_id: int, thread_id: int, channel_id: int, starter_message_id: int) -> None:
        now = datetime.now().isoformat()
        async with aiosqlite.connect(self.db_path) as db:
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

    async def save_medal(self, thread_id: int, tier: str, user_id: int) -> None:
        now = datetime.now().isoformat()
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute("""
                INSERT INTO medals (thread_id, tier, user_id, created_at)
                VALUES (?, ?, ?, ?)
            """, (thread_id, tier, user_id, now))
            await db.commit()

    async def get_thread_by_banner_id(self, banner_id: int) -> Optional[Dict]:
        async with aiosqlite.connect(self.db_path) as db:
            db.row_factory = aiosqlite.Row
            cursor = await db.execute(
                "SELECT * FROM discord_threads WHERE banner_id = ?", (banner_id,)
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

    async def get_all_active_banner_ids(self) -> List[int]:
        """Gibt alle aktiven Banner-IDs zurück."""
        async with aiosqlite.connect(self.db_path) as db:
            cursor = await db.execute(
                "SELECT pack_id FROM banners WHERE is_active = 1"
            )
            rows = await cursor.fetchall()
            return [row[0] for row in rows]

    async def increment_not_found_count(self, pack_id: int) -> int:
        """Erhöht not_found_count um 1 und gibt den neuen Wert zurück."""
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute(
                "UPDATE banners SET not_found_count = not_found_count + 1 WHERE pack_id = ?",
                (pack_id,)
            )
            await db.commit()

            cursor = await db.execute(
                "SELECT not_found_count FROM banners WHERE pack_id = ?", (pack_id,)
            )
            row = await cursor.fetchone()
            return row[0] if row else 0

    async def reset_not_found_count(self, pack_id: int) -> None:
        """Setzt not_found_count auf 0 zurück."""
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute(
                "UPDATE banners SET not_found_count = 0 WHERE pack_id = ?",
                (pack_id,)
            )
            await db.commit()

    async def get_expired_banners(self, threshold: int = 2) -> List[Dict]:
        """Gibt Banner zurück die >= threshold mal nicht gefunden wurden."""
        async with aiosqlite.connect(self.db_path) as db:
            db.row_factory = aiosqlite.Row
            cursor = await db.execute(
                "SELECT * FROM banners WHERE not_found_count >= ? AND is_active = 1",
                (threshold,)
            )
            rows = await cursor.fetchall()
            return [dict(row) for row in rows]

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
