"""
Datenbank-Operationen
"""

import json

import aiosqlite
from pathlib import Path
from typing import Optional, Dict, Any, List
from datetime import datetime, timedelta, timezone

from loguru import logger
from config import DATABASE_PATH
from utils.card_pool import card_value, fmt_coins



PACK_HISTORY_DAYS = 90
# Beendete Banner bleiben so lange komplett erhalten (App: Kategorie Archiv), danach nur noch banner_archive
ARCHIVE_DAYS = 30
# "0 Packs" bzw. Verschwinden gilt als leer gezogen, wenn vorher höchstens so viele Packs übrig waren
SOLD_OUT_MAX_LEFT = 20
API_LOG_DAYS = 30
# Store-Packs (gtchaxonline.com/store) liegen in derselben Tabelle, aber mit is_active = 2: alle Funktionen des Bots
# für Discord (Threads, Top 10, "nicht gefunden") arbeiten nur mit is_active = 1 und sehen sie nie; Funktionen
# nach Banner-ID (Kartenpool, Pack-Verlauf, Preis ...) und die App funktionieren für beide.
STORE = 2


def store_thread_id(pack_id: int) -> int:
    """Interne "Thread-Nummer" eines Store-Packs (ohne Discord): Medaillen hängen daran wie an einem Thread.
    Negativ, damit sie nie mit einer echten Discord-ID zusammenfällt."""
    return -int(pack_id)


def archive_pool(pool_json) -> tuple:
    """(Kartenwerte + Ø je Pack, Karten-ID -> Wert) eines Kartenpools als JSON für banner_archive."""
    try:
        pool = json.loads(pool_json) if pool_json else {}
    except ValueError:
        pool = {}
    cards = (pool.get("cards") or []) + (pool.get("hits") or [])
    values = sorted({int(c["value"]) for c in pool.get("cards") or [] if c.get("value")})
    total, count = pool.get("total_value"), pool.get("total_count")
    avg = round(int(total) / int(count)) if total and count else None
    ids = {str(c.get("id")): int(c.get("value") or 0) for c in cards if c.get("id") is not None}
    return json.dumps({"values": values, "avg": avg}), json.dumps(ids)

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

                CREATE TABLE IF NOT EXISTS shipment_history (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    banner_id INTEGER,
                    old_cards INTEGER, new_cards INTEGER,
                    old_coins INTEGER, new_coins INTEGER,
                    old_players INTEGER, new_players INTEGER,
                    changed_at TEXT
                );

                CREATE TABLE IF NOT EXISTS card_value_history (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    banner_id INTEGER,
                    card_id TEXT, name TEXT,
                    old_value INTEGER, new_value INTEGER,
                    changed_at TEXT
                );

                CREATE TABLE IF NOT EXISTS convert_history (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    banner_id INTEGER,
                    old_coins INTEGER, new_coins INTEGER,
                    changed_at TEXT
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
                CREATE INDEX IF NOT EXISTS pack_history_changed ON pack_history (changed_at);

                -- Discord sieht automatisch erkannte Hits zeitversetzt: öffentlicher Stand je Banner
                -- (fehlt die Zeile, gilt der echte Stand) und Warteschlange für spätere Posts
                -- Rohdaten der Seite je Banner, bei jeder Änderung (zum Prüfen der Versand-/Umwandlungs-Rechnung)
                CREATE TABLE IF NOT EXISTS api_log (
                    id INTEGER PRIMARY KEY AUTOINCREMENT, banner_id INTEGER, changed_at TEXT,
                    pack_count INTEGER, sendcount INTEGER, sendprice INTEGER, kangen INTEGER, sendpeople INTEGER,
                    price INTEGER);
                CREATE INDEX IF NOT EXISTS api_log_banner ON api_log (banner_id, id);
                -- Automatisch (aus Versand/Umwandlung) abgehakte Hits - zur Kontrolle für den Admin
                CREATE TABLE IF NOT EXISTS auto_ticks (
                    id INTEGER PRIMARY KEY AUTOINCREMENT, pack_id INTEGER, card_key TEXT, tier TEXT, name TEXT,
                    value INTEGER, rebuild INTEGER, created_at TEXT);
                -- Vom Admin als falsch markierte Erkennungen: dieser Hit gilt nicht als automatisch erkannt
                CREATE TABLE IF NOT EXISTS pull_rejects (pack_id INTEGER, card_key TEXT, created_at TEXT,
                    PRIMARY KEY (pack_id, card_key));
                -- Abgeschlossene Banner als Lernfall (Versand-Zeiten, Medaillen, Beobachtungen), siehe utils/ship_odds.py
                CREATE TABLE IF NOT EXISTS banner_cases (pack_id INTEGER PRIMARY KEY, ended_at TEXT, data TEXT);
                -- Übersetzungen japanischer Namensteile (DeepL), siehe utils/translate.py
                CREATE TABLE IF NOT EXISTS translations (source TEXT PRIMARY KEY, german TEXT, created_at TEXT);
                CREATE TABLE IF NOT EXISTS discord_public (
                    pack_id INTEGER PRIMARY KEY, pulled_cards TEXT, unsure_cards TEXT);
                CREATE TABLE IF NOT EXISTS discord_outbox (
                    id INTEGER PRIMARY KEY AUTOINCREMENT, kind TEXT, pack_id INTEGER, thread_id INTEGER,
                    payload TEXT, send_at REAL);

                -- Schlankes Archiv beendeter Banner (für den eigenen Verlauf in der App:
                -- Preis, Kartenwerte und Karten-IDs, damit ältere Züge noch zugeordnet werden können)
                CREATE TABLE IF NOT EXISTS banner_archive (
                    pack_id INTEGER PRIMARY KEY,
                    title TEXT, category TEXT, best_hit TEXT, price_coins INTEGER, image_url TEXT,
                    created_at TEXT, ended_at TEXT, card_values TEXT, card_ids TEXT
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
                               ('banners', 'ship_batches TEXT'),
                               ('banners', 'pool_updated_at TEXT'),
                               ('banners', 'starts_at INTEGER'),
                               ('banners', 'start_announced INTEGER DEFAULT 0'),
                               ('discord_threads', 'top5_message_id INTEGER'),
                               ('discord_threads', 'value_alert_sent INTEGER DEFAULT 0'),
                               ('banners', 'converted INTEGER'),
                               ('medals', "source TEXT DEFAULT 'discord'")]:
                try:
                    await db.execute(f"ALTER TABLE {table} ADD COLUMN {col}")
                    await db.commit()
                    logger.info(f"Migration: {table}.{col.split()[0]} hinzugefügt")
                except Exception:
                    pass  # Spalte existiert bereits

            # Versand-Abgleich mit Toleranz: bisherige Versand-Summen einmal neu auswerten lassen
            cursor = await db.execute("SELECT value FROM bot_meta WHERE key = 'ship_match_version'")
            row = await cursor.fetchone()
            if not row or row[0] != '15':
                # Banner mit Versand-Hits komplett neu auswerten (auch die erkannten Karten), damit
                # Fehlzuordnungen der alten Logik verschwinden; Medaillen bleiben unberührt
                await db.execute("""UPDATE banners SET pulled_cards = NULL, unsure_cards = NULL
                                    WHERE card_pool LIKE '%"hits": [{%'""")
                await db.execute("UPDATE banners SET ship_count = NULL, ship_value = NULL, ship_batches = NULL")
                await db.execute("INSERT OR REPLACE INTO bot_meta (key, value) VALUES ('ship_match_version', '15')")
                await db.commit()
                logger.info("Migration: Versand-Summen werden neu ausgewertet (15: Einzelkarte über allen normalen Karten = Hit mit anderem Wert)")

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
                                       max_age_hours: int = 6) -> List[int]:
        """Aktive Banner mit Thread, deren Kartenpool fehlt oder älter als max_age_hours ist.

        Reihenfolge: prefer_ids (z.B. neue Banner), dann fehlende Pools, dann die ältesten.
        """
        cutoff = (datetime.now() - timedelta(hours=max_age_hours)).isoformat()
        async with aiosqlite.connect(self.db_path) as db:
            cursor = await db.execute("""
                SELECT b.pack_id, b.card_pool IS NULL OR b.card_pool NOT LIKE '%"version": 2%'
                                  OR b.card_pool NOT LIKE '%"cards": [%' AS missing,
                       COALESCE(b.pool_updated_at, '') AS updated
                FROM banners b
                JOIN discord_threads t ON t.banner_id = b.pack_id AND t.is_expired = 0
                WHERE b.is_active = 1 AND (b.card_pool IS NULL OR b.card_pool NOT LIKE '%"version": 2%'
                                           OR b.card_pool NOT LIKE '%"cards": [%'
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

    async def replace_pool_names(self, pack_id: int, old_json: str, pool: Dict) -> bool:
        """Pool mit übersetzten Namen speichern, ohne den Zeitpunkt des Pool-Abrufs zu ändern - nur wenn
        der Pool seit dem Lesen unverändert ist (sonst nicht einen frisch geladenen Pool überschreiben)."""
        async with aiosqlite.connect(self.db_path) as db:
            cursor = await db.execute("UPDATE banners SET card_pool = ? WHERE pack_id = ? AND card_pool = ?",
                                      (json.dumps(pool, ensure_ascii=False), pack_id, old_json))
            await db.commit()
            return cursor.rowcount > 0

    async def get_translations(self) -> Dict[str, str]:
        async with aiosqlite.connect(self.db_path) as db:
            try:
                cursor = await db.execute("SELECT source, german FROM translations")
            except aiosqlite.OperationalError:
                return {}   # ältere Datenbank ohne Tabelle (die App liest nur)
            return {src: de for src, de in await cursor.fetchall()}

    async def save_translations(self, translations: Dict[str, str]) -> None:
        if not translations:
            return
        now = datetime.now().isoformat()
        async with aiosqlite.connect(self.db_path) as db:
            await db.executemany("INSERT OR REPLACE INTO translations (source, german, created_at) VALUES (?, ?, ?)",
                                 [(src, de, now) for src, de in translations.items()])
            await db.commit()

    async def get_card_pool(self, pack_id: int) -> Optional[Dict]:
        async with aiosqlite.connect(self.db_path) as db:
            cursor = await db.execute("SELECT card_pool FROM banners WHERE pack_id = ?", (pack_id,))
            row = await cursor.fetchone()
        return json.loads(row[0]) if row and row[0] else None

    # --- Öffentlicher Stand für Discord (zeitversetzt) ---
    async def get_public_pulls(self, pack_id: int) -> Optional[Dict]:
        async with aiosqlite.connect(self.db_path) as db:
            cur = await db.execute("SELECT pulled_cards, unsure_cards FROM discord_public WHERE pack_id = ?", (pack_id,))
            row = await cur.fetchone()
        if not row:
            return None
        return {"pulled": json.loads(row[0]) if row[0] else [], "unsure": json.loads(row[1]) if row[1] else []}

    async def set_public_pulls(self, pack_id: int, pulled: Optional[list], unsure: Optional[list] = None,
                               only_if_missing: bool = False) -> None:
        """pulled=None: Zeile löschen (Discord sieht wieder den echten Stand)."""
        async with aiosqlite.connect(self.db_path) as db:
            if pulled is None:
                await db.execute("DELETE FROM discord_public WHERE pack_id = ?", (pack_id,))
            else:
                verb = "INSERT OR IGNORE" if only_if_missing else "INSERT OR REPLACE"
                await db.execute(f"{verb} INTO discord_public (pack_id, pulled_cards, unsure_cards) VALUES (?, ?, ?)",
                                 (pack_id, json.dumps(pulled), json.dumps(unsure or [])))
            await db.commit()

    async def queue_discord(self, kind: str, pack_id: int, thread_id: int, payload: Dict, send_at: float) -> None:
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute("INSERT INTO discord_outbox (kind, pack_id, thread_id, payload, send_at) VALUES (?, ?, ?, ?, ?)",
                             (kind, pack_id, thread_id, json.dumps(payload), send_at))
            await db.commit()

    async def due_discord(self, now: float) -> List[Dict]:
        async with aiosqlite.connect(self.db_path) as db:
            cur = await db.execute("SELECT id, kind, pack_id, thread_id, payload FROM discord_outbox "
                                   "WHERE send_at <= ? ORDER BY id", (now,))
            return [{"id": r[0], "kind": r[1], "pack_id": r[2], "thread_id": r[3], "payload": json.loads(r[4] or "{}")}
                    for r in await cur.fetchall()]

    async def done_discord(self, item_id: int) -> None:
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute("DELETE FROM discord_outbox WHERE id = ?", (item_id,))
            await db.commit()

    async def pending_discord(self, pack_id: int) -> int:
        async with aiosqlite.connect(self.db_path) as db:
            cur = await db.execute("SELECT count(*) FROM discord_outbox WHERE pack_id = ?", (pack_id,))
            return (await cur.fetchone())[0]

    async def get_pull_tracking(self, pack_id: int) -> Dict:
        """Zuletzt gesehene Zähler (None = noch nie gesehen) und als gezogen erkannte Karten."""
        async with aiosqlite.connect(self.db_path) as db:
            cursor = await db.execute(
                "SELECT decided_value, ship_count, ship_value, pulled_cards, unsure_cards, ship_batches "
                "FROM banners WHERE pack_id = ?", (pack_id,))
            row = await cursor.fetchone()
        if not row:
            return {"decided_value": None, "ship_count": None, "ship_value": None, "pulled": [], "unsure": [],
                    "batches": None}
        return {"decided_value": row[0], "ship_count": row[1], "ship_value": row[2],
                "pulled": json.loads(row[3]) if row[3] else [],
                "unsure": json.loads(row[4]) if row[4] else [],
                "batches": json.loads(row[5]) if row[5] else None}

    async def set_pull_tracking(self, pack_id: int, decided_value: Optional[int], ship_count: Optional[int],
                                ship_value: Optional[int], pulled: List[str], unsure: List[Dict],
                                batches: Optional[List[List[int]]] = None) -> None:
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute(
                "UPDATE banners SET decided_value = ?, ship_count = ?, ship_value = ?, pulled_cards = ?, "
                "unsure_cards = ?, ship_batches = COALESCE(?, ship_batches) WHERE pack_id = ?",
                (decided_value, ship_count, ship_value, json.dumps(pulled), json.dumps(unsure),
                 json.dumps(batches) if batches is not None else None, pack_id))
            await db.commit()

    async def rebuild_ship_batches(self, pack_id: int, count: int, value: int) -> List[List]:
        """Schübe aus dem Versand-Verlauf: Stand vor der ersten Aufzeichnung als ein Schub, dann je Änderung.
        Je Schub [Karten, Betrag, Zeit (Unix-Sekunden, None = vor der Aufzeichnung)]."""
        async with aiosqlite.connect(self.db_path) as db:
            cursor = await db.execute(
                "SELECT old_cards, new_cards, old_coins, new_coins, changed_at FROM shipment_history "
                "WHERE banner_id = ? ORDER BY id", (pack_id,))
            rows = await cursor.fetchall()
        batches: List[List] = []
        total_c = total_v = 0
        for old_c, new_c, old_v, new_v, changed in rows:
            if old_c is None or new_c is None or new_c <= old_c:
                continue
            if old_c > total_c:  # Stand vor diesem Eintrag, der nicht aufgezeichnet ist
                batches.append([old_c - total_c, (old_v or 0) - total_v, None])
            try:
                t = int(datetime.fromisoformat(changed).replace(tzinfo=timezone.utc).timestamp())
            except (TypeError, ValueError):
                t = None
            batches.append([new_c - old_c, (new_v or 0) - (old_v or 0), t])
            total_c, total_v = new_c, new_v or 0
        if count > total_c:
            batches.append([count - total_c, value - total_v, int(datetime.now(timezone.utc).timestamp())])
        return batches

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
            old = json.loads(row[0]) if row[0] else None
            if old and (old.get("cards"), old.get("coins")) != (stats.get("cards"), stats.get("coins")):
                await db.execute("""
                    INSERT INTO shipment_history (banner_id, old_cards, new_cards, old_coins, new_coins,
                                                  old_players, new_players, changed_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """, (pack_id, old.get("cards"), stats.get("cards"), old.get("coins"), stats.get("coins"),
                      old.get("players"), stats.get("players"), datetime.now().isoformat()))
                counted = (stats.get('coins') or 0) - (old.get('coins') or 0)
                logger.info(f"[VERSAND] {pack_id}: {old.get('cards')} -> {stats.get('cards')} Karten "
                            f"(+{fmt_coins(card_value(counted))} Coins Kartenwert, gezählt {fmt_coins(counted)}, "
                            f"{old.get('players')} -> {stats.get('players')} Spieler)")
            await db.commit()
        return True

    async def save_value_changes(self, pack_id: int, changes: List[Dict]) -> None:
        """Merkt geänderte Kartenwerte (aus dem Vergleich zweier geladener Pools)."""
        if not changes:
            return
        now = datetime.now().isoformat()
        async with aiosqlite.connect(self.db_path) as db:
            await db.executemany(
                "INSERT INTO card_value_history (banner_id, card_id, name, old_value, new_value, changed_at) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                [(pack_id, c["id"], c["name"], c["old"], c["new"], now) for c in changes])
            await db.commit()

    async def update_converted(self, pack_id: int, coins: int) -> Optional[int]:
        """Speichert den Coin-Wert aller umgewandelten Karten (total_kangen) und merkt jede Änderung.

        Gibt den Anstieg zurück (None = unverändert oder Banner unbekannt).
        """
        async with aiosqlite.connect(self.db_path) as db:
            cursor = await db.execute("SELECT converted FROM banners WHERE pack_id = ?", (pack_id,))
            row = await cursor.fetchone()
            if not row or row[0] == coins:
                return None
            await db.execute("UPDATE banners SET converted = ? WHERE pack_id = ?", (coins, pack_id))
            if row[0] is not None:
                await db.execute(
                    "INSERT INTO convert_history (banner_id, old_coins, new_coins, changed_at) VALUES (?, ?, ?, ?)",
                    (pack_id, row[0], coins, datetime.now().isoformat()))
            await db.commit()
        return coins - (row[0] or 0)

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

    async def get_medals(self, thread_id: int, hide_app_since: Optional[str] = None) -> Dict[str, int]:
        """Alle vergebenen Medaillen eines Threads: Stufe (T1-T10) -> Discord-User-ID (0 = unbekannt).

        hide_app_since (ISO-Zeit): in der App gemeldete Medaillen, die jünger sind, weglassen
        (Discord sieht sie zeitversetzt); im Thread geschriebene zählen immer sofort.
        """
        async with aiosqlite.connect(self.db_path) as db:
            cursor = await db.execute("SELECT tier, user_id, source, created_at FROM medals WHERE thread_id = ?",
                                      (thread_id,))
            rows = await cursor.fetchall()
            cursor = await db.execute(
                "SELECT t1_claimed, t2_claimed, t3_claimed FROM discord_threads WHERE thread_id = ?", (thread_id,))
            row = await cursor.fetchone()
        hidden = {t for t, _, src, created in rows
                  if hide_app_since and src == "app" and (created or "") > hide_app_since}
        medals = {t: u or 0 for t, u, _, _ in rows if t not in hidden}
        for tier, claimed in zip(("T1", "T2", "T3"), row or ()):
            if claimed and tier not in hidden:
                medals.setdefault(tier, 0)
        return medals

    async def save_medal(self, thread_id: int, tier: str, user_id: int, source: str = "discord") -> None:
        """source: "discord" (im Thread geschrieben) oder "app" (in der App gemeldet - in Discord zeitversetzt)."""
        now = datetime.now().isoformat()
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute("""
                INSERT INTO medals (thread_id, tier, user_id, created_at, source)
                VALUES (?, ?, ?, ?, ?)
            """, (thread_id, tier, user_id, now, source))

            # Auch die claimed-Spalte in discord_threads setzen
            col_map = {'T1': 't1_claimed', 'T2': 't2_claimed', 'T3': 't3_claimed'}
            if tier in col_map:
                await db.execute(
                    f"UPDATE discord_threads SET {col_map[tier]} = 1 WHERE thread_id = ?",
                    (thread_id,)
                )
            await db.commit()

    async def delete_medal(self, thread_id: int, tier: str) -> None:
        """Nimmt eine Medaille zurück (auch die T1-T3-Markierung am Thread)."""
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute("DELETE FROM medals WHERE thread_id = ? AND tier = ?", (thread_id, tier))
            col_map = {'T1': 't1_claimed', 'T2': 't2_claimed', 'T3': 't3_claimed'}
            if tier in col_map:
                await db.execute(f"UPDATE discord_threads SET {col_map[tier]} = 0 WHERE thread_id = ?", (thread_id,))
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

    async def get_store_banners(self) -> Dict[int, Dict]:
        """Aktive Store-Packs (nur für die App) als pack_id -> Zeile."""
        async with aiosqlite.connect(self.db_path) as db:
            db.row_factory = aiosqlite.Row
            cursor = await db.execute("SELECT * FROM banners WHERE is_active = ?", (STORE,))
            return {row["pack_id"]: dict(row) for row in await cursor.fetchall()}

    async def upsert_store_pack(self, pack_id: int, title: Optional[str], price: Optional[int], packs: int,
                                total: Optional[int], per_day: Optional[int], sale_end: Optional[str],
                                image_url: Optional[str], detail_url: str) -> bool:
        """Store-Pack anlegen oder aktualisieren (Pack-Zahl über update_banner_packs, damit der Verlauf stimmt).
        True = neu. Ein normaler Banner mit derselben ID wird nie angefasst."""
        existing = await self.get_banner(pack_id)
        if existing and existing.get("is_active") == 1:
            return False
        now = datetime.now().isoformat()
        async with aiosqlite.connect(self.db_path) as db:
            if not existing:
                await db.execute("""
                    INSERT INTO banners (pack_id, category, title, price_coins, current_packs, total_packs, entries_per_day,
                                         sale_end_date, image_url, detail_page_url, is_active, created_at, updated_at)
                    VALUES (?, 'Store', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """, (pack_id, title, price, packs, total, per_day, sale_end, image_url, detail_url, STORE, now, now))
            else:   # auch ein früher beendeter Store-Pack (is_active 0) wird wieder aktiv
                await db.execute("""
                    UPDATE banners SET category = 'Store', title = ?, price_coins = ?, total_packs = ?, entries_per_day = ?,
                                       sale_end_date = ?, image_url = ?, detail_page_url = ?, is_active = ?, not_found_count = 0
                    WHERE pack_id = ?
                """, (title, price, total, per_day, sale_end, image_url, detail_url, STORE, pack_id))
            await db.commit()
        if existing and existing.get("current_packs") != packs:
            await self.update_banner_packs(pack_id, packs)
        return not existing

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
                  AND dt.thread_id IS NOT NULL AND dt.is_expired = 0
            """, (cutoff,))
            return [row[0] for row in await cursor.fetchall()]

    async def mark_threads_expired(self, thread_ids: List[int]) -> None:
        """Discord-Threads als gelöscht markieren (die Daten des Banners bleiben fürs Archiv)."""
        if not thread_ids:
            return
        async with aiosqlite.connect(self.db_path) as db:
            await db.executemany("UPDATE discord_threads SET is_expired = 1 WHERE thread_id = ?",
                                 [(int(t),) for t in thread_ids])
            await db.commit()

    async def fix_sold_out_counts(self) -> List[int]:
        """Beendete Banner, die leer gezogen wurden, auf 0 Packs setzen. Leer gezogen heißt: die Seite hat zuletzt
        0 gemeldet, oder der Banner verschwand mit höchstens SOLD_OUT_MAX_LEFT Packs vor seinem Verkaufsende
        (ausverkaufte Banner nimmt die Seite einfach aus der Liste). Gibt die korrigierten IDs zurück."""
        from utils.banner_info import sale_end_timestamp
        async with aiosqlite.connect(self.db_path) as db:
            cursor = await db.execute("""
                SELECT b.pack_id, b.current_packs, b.updated_at, b.sale_end_date,
                       (SELECT a.pack_count FROM api_log a WHERE a.banner_id = b.pack_id ORDER BY a.id DESC LIMIT 1)
                FROM banners b WHERE b.is_active = 0 AND b.current_packs > 0
            """)
            fixed = []
            for pid, old, ended, sale_end, last_api in await cursor.fetchall():
                end_ts = sale_end_timestamp(sale_end)
                try:
                    ended_ts = datetime.fromisoformat(ended).replace(tzinfo=timezone.utc).timestamp()
                except (TypeError, ValueError):
                    ended_ts = None
                early = old <= SOLD_OUT_MAX_LEFT and end_ts and ended_ts and ended_ts < end_ts - 3600
                if last_api == 0 or early:
                    # updated_at bleibt (= Ende des Banners, zählt für die 30 Tage im Archiv)
                    await db.execute("UPDATE banners SET current_packs = 0 WHERE pack_id = ?", (pid,))
                    await db.execute("INSERT INTO pack_history (banner_id, old_count, new_count, changed_at) "
                                     "VALUES (?, ?, 0, ?)", (pid, old, ended))
                    fixed.append(pid)
            await db.commit()
        return fixed

    async def odds_inputs(self, pack_id: int) -> Dict:
        """Zeitlicher Ablauf eines Banners für utils/ship_odds: Versandschübe, Medaillen (Stufe -> Meldezeit),
        Pack-Verkäufe. Zeiten als Unix-Sekunden (die DB speichert naive UTC-Zeit)."""
        def ts(iso):
            try:
                return datetime.fromisoformat(iso).replace(tzinfo=timezone.utc).timestamp()
            except (TypeError, ValueError):
                return None
        async with aiosqlite.connect(self.db_path) as db:
            cur = await db.execute("SELECT changed_at, old_cards, new_cards, old_coins, new_coins FROM shipment_history "
                                   "WHERE banner_id = ? ORDER BY id", (pack_id,))
            shipments = [{"t": ts(t), "cards": (nc or 0) - (oc or 0), "coins": (nv or 0) - (ov or 0)}
                         for t, oc, nc, ov, nv in await cur.fetchall() if (nc or 0) > (oc or 0)]
            cur = await db.execute("SELECT is_active, category FROM banners WHERE pack_id = ?", (pack_id,))
            row = await cur.fetchone()
            store = bool(row) and (row[0] == STORE or (row[0] == 0 and row[1] == 'Store'))
            if store:
                threads = [store_thread_id(pack_id)]
            else:
                cur = await db.execute("SELECT thread_id FROM discord_threads WHERE banner_id = ?", (pack_id,))
                threads = [r[0] for r in await cur.fetchall()]
            medals = {}
            if threads:
                # vom Admin abgehakt: kein Zugzeitpunkt, keine Versand-Anforderung
                cur = await db.execute(f"SELECT tier, created_at FROM medals WHERE COALESCE(source, '') != 'admin' "
                                       f"AND thread_id IN ({','.join('?' * len(threads))})", threads)
                medals = {tier: ts(t) for tier, t in await cur.fetchall() if ts(t)}
            cur = await db.execute("SELECT changed_at, old_count - new_count FROM pack_history "
                                   "WHERE banner_id = ? AND new_count < old_count ORDER BY id", (pack_id,))
            moves = [(ts(t), n) for t, n in await cur.fetchall() if ts(t)]
        return {"shipments": shipments, "medals": medals, "moves": moves}

    async def medal_rows(self, thread_id: int) -> Dict[str, Dict]:
        """Medaillen eines Threads mit Herkunft: Stufe -> {user_id, source ("discord"/"app"), at (Unix-Zeit)}."""
        if not thread_id:
            return {}
        async with aiosqlite.connect(self.db_path) as db:
            cur = await db.execute("SELECT tier, user_id, source, created_at FROM medals WHERE thread_id = ?",
                                   (thread_id,))
            rows = await cur.fetchall()
        out = {}
        for tier, user, source, created in rows:
            try:
                at = int(datetime.fromisoformat(created).replace(tzinfo=timezone.utc).timestamp())
            except (TypeError, ValueError):
                at = None
            out[tier] = {"user_id": user, "source": source or "discord", "at": at}
        return out

    async def log_auto_ticks(self, pack_id: int, ticks: List[Dict], rebuild: bool) -> None:
        if not ticks:
            return
        now = datetime.now().isoformat()
        async with aiosqlite.connect(self.db_path) as db:
            await db.executemany(
                "INSERT INTO auto_ticks (pack_id, card_key, tier, name, value, rebuild, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                [(pack_id, t["key"], t.get("tier"), t.get("name"), t.get("value"), int(rebuild), now) for t in ticks])
            await db.execute("DELETE FROM auto_ticks WHERE created_at < ?",
                             ((datetime.now() - timedelta(days=PACK_HISTORY_DAYS)).isoformat(),))
            await db.commit()

    async def get_rejects(self, pack_id: int) -> set:
        async with aiosqlite.connect(self.db_path) as db:
            cur = await db.execute("SELECT card_key FROM pull_rejects WHERE pack_id = ?", (pack_id,))
            return {r[0] for r in await cur.fetchall()}

    async def set_reject(self, pack_id: int, key: str, rejected: bool) -> None:
        """Admin: automatische Erkennung eines Hits als falsch markieren (oder wieder zulassen). Beim Markieren
        verschwindet der Hit sofort aus den erkannten; beim Zulassen zählt er ab der nächsten Auswertung wieder."""
        async with aiosqlite.connect(self.db_path) as db:
            if rejected:
                await db.execute("INSERT OR IGNORE INTO pull_rejects (pack_id, card_key, created_at) VALUES (?, ?, ?)",
                                 (pack_id, key, datetime.now().isoformat()))
                cur = await db.execute("SELECT pulled_cards FROM banners WHERE pack_id = ?", (pack_id,))
                row = await cur.fetchone()
                if row and row[0]:
                    pulled = [k for k in json.loads(row[0]) if k != key]
                    await db.execute("UPDATE banners SET pulled_cards = ? WHERE pack_id = ?",
                                     (json.dumps(pulled), pack_id))
            else:
                await db.execute("DELETE FROM pull_rejects WHERE pack_id = ? AND card_key = ?", (pack_id, key))
                # beim nächsten Versand neu auswerten (alle Schübe)
                await db.execute("UPDATE banners SET ship_batches = NULL, ship_count = NULL WHERE pack_id = ?",
                                 (pack_id,))
            await db.commit()

    async def save_case(self, pack_id: int, ended_at: Optional[str], data: Dict) -> None:
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute("INSERT OR REPLACE INTO banner_cases (pack_id, ended_at, data) VALUES (?, ?, ?)",
                             (pack_id, ended_at, json.dumps(data, ensure_ascii=False)))
            await db.commit()

    async def get_cases(self) -> Dict[int, Dict]:
        async with aiosqlite.connect(self.db_path) as db:
            try:
                cur = await db.execute("SELECT pack_id, data FROM banner_cases")
            except aiosqlite.OperationalError:
                return {}
            return {pid: json.loads(d) for pid, d in await cur.fetchall()}

    async def get_ended_banners(self, days: int = ARCHIVE_DAYS) -> Dict[int, Dict]:
        """Beendete Banner (auch Store-Packs) der letzten `days` Tage, neueste zuerst."""
        cutoff = (datetime.now() - timedelta(days=days)).isoformat()
        async with aiosqlite.connect(self.db_path) as db:
            db.row_factory = aiosqlite.Row
            cursor = await db.execute("SELECT * FROM banners WHERE is_active = 0 AND updated_at >= ? "
                                      "ORDER BY updated_at DESC", (cutoff,))
            return {row["pack_id"]: dict(row) for row in await cursor.fetchall()}

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

            # Vor dem Löschen ins Archiv (nur was der Verlauf braucht)
            cursor = await db.execute(
                f"SELECT pack_id, title, category, best_hit, price_coins, image_url, created_at, updated_at, card_pool "
                f"FROM banners WHERE pack_id IN ({placeholders})", old_ids)
            for row in await cursor.fetchall():
                await db.execute(
                    "INSERT OR REPLACE INTO banner_archive (pack_id, title, category, best_hit, price_coins, image_url, "
                    "created_at, ended_at, card_values, card_ids) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (*row[:7], datetime.now().isoformat(), *archive_pool(row[8])))

            # Medals löschen (Store-Packs: interne Nummer -pack_id)
            await db.execute(f"""
                DELETE FROM medals WHERE thread_id IN (
                    SELECT thread_id FROM discord_threads WHERE banner_id IN ({placeholders})
                )
            """, old_ids)
            await db.execute(f"DELETE FROM medals WHERE thread_id IN ({placeholders})", [-pid for pid in old_ids])

            # Pack-Bewegungen bleiben PACK_HISTORY_DAYS Tage (Zuordnung im eigenen Verlauf), siehe unten
            await db.execute(f"DELETE FROM convert_history WHERE banner_id IN ({placeholders})", old_ids)
            await db.execute(f"DELETE FROM card_value_history WHERE banner_id IN ({placeholders})", old_ids)

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

    async def log_api_values(self, pack_id: int, item: Dict) -> bool:
        """Rohwerte aus pack/list protokollieren, wenn sich etwas geändert hat. True = neuer Eintrag."""
        def num(key):
            try:
                return int(float(item.get(key))) if item.get(key) is not None else None
            except (TypeError, ValueError):
                return None
        values = (num("pack_count"), num("total_sendcount"), num("total_sendprice"), num("total_kangen"),
                  num("total_sendpeople"), num("point"))
        last = self.__dict__.setdefault("_api_last", {})
        if last.get(pack_id) == values:
            return False
        async with aiosqlite.connect(self.db_path) as db:
            if pack_id not in last:   # nach Neustart: letzten gespeicherten Stand als Vergleich nehmen
                cur = await db.execute("SELECT pack_count, sendcount, sendprice, kangen, sendpeople, price FROM api_log "
                                       "WHERE banner_id = ? ORDER BY id DESC LIMIT 1", (pack_id,))
                row = await cur.fetchone()
                if row and tuple(row) == values:
                    last[pack_id] = values
                    return False
            await db.execute("INSERT INTO api_log (banner_id, changed_at, pack_count, sendcount, sendprice, kangen, "
                             "sendpeople, price) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                             (pack_id, datetime.now().isoformat(), *values))
            await db.commit()
        last[pack_id] = values
        return True

    async def purge_old_history(self) -> int:
        """Pack-Bewegungen und Archiv älter als PACK_HISTORY_DAYS Tage löschen."""
        cutoff = (datetime.now() - timedelta(days=PACK_HISTORY_DAYS)).isoformat()
        async with aiosqlite.connect(self.db_path) as db:
            cur = await db.execute("DELETE FROM pack_history WHERE changed_at < ?", (cutoff,))
            removed = cur.rowcount
            await db.execute("DELETE FROM banner_archive WHERE ended_at < ?", (cutoff,))
            await db.execute("DELETE FROM api_log WHERE changed_at < ?",
                             ((datetime.now() - timedelta(days=API_LOG_DAYS)).isoformat(),))
            await db.commit()
            return removed

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
