"""
Discord Bot Client - Forum-Channel Version
"""

import asyncio
import json
import re
from dataclasses import dataclass
from datetime import datetime, timedelta
from math import comb
import re as regex_module
from typing import Optional

import aiosqlite
import discord
from discord import app_commands
from discord.ext import commands
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from loguru import logger

import os
import sys
from config import (
    GUILD_ID, SCRAPE_INTERVAL_MINUTES, BASE_URL,
    CHANNEL_IDS, CATEGORIES, SCRAPE_TIMEOUT_SECONDS,
    MENTION_ON_NEW_THREAD, MENTION_ON_PACK_UPDATE,
    HOT_BANNER_CHANNEL_ID, HOT_BANNER_ENABLED,
    DAILY_RESTART_TIME
)
from scraper.gtcha_scraper import GTCHAScraper
from scraper.models import ScrapedBanner
from database.db import Database
from utils.notifications import (
    set_bot_client, notify_scrape_error,
    notify_all_retries_failed, notify_critical_error,
    notify_scrape_success, notify_bot_started, send_notification
)
from utils.rate_limiter import discord_rate_limiter
from utils.memory_monitor import memory_monitor
from utils.cache import banner_cache
from utils.maintenance import backup_database, new_tor_identity, start_watchdog, touch_heartbeat
from utils.banner_info import (
    banner_conditions, category_for, chance_at_least_one, format_conditions, format_shipping,
    is_upcoming, jst_timestamp, shipping_stats, to_int as _int,
)
from utils.card_pool import (
    estimate, fmt_coins, fmt_pct, TIERS, MAX_LISTED, EMBEDS_PER_MESSAGE, decided_value, detect_jump_pulls,
    is_relevant_hit, match_shipped_hits, pool_minimum, relevant_units, shipment_values, tier_keys,
    tracked_units,
)


# Erhöhen, wenn der Startbeitrag neue Felder bekommt: alle Threads werden dann einmal aktualisiert
EMBED_VERSION = 3
# Endspurt-Alarm, sobald höchstens so viel Prozent der Packs übrig sind und noch Hits drin sind
ENDSPURT_PERCENT = float(os.getenv("ENDSPURT_PERCENT") or "10")
# Zeitraum für das Abverkaufs-Tempo
SALES_WINDOW_HOURS = 2
# Zugzahlen für die Hit-Chance in der 🎯-Nachricht
HIT_CHANCE_PULLS = (1, 10, 50)


# Fehlende Kartenpools, die pro Scrape geladen werden (Nachrüsten bestehender Threads)
POOL_FETCH_PER_SCRAPE = 8
# Normale Scrapes laufen nur über pack/list; Tabs werden höchstens so oft komplett durchgeklickt
FULL_SCRAPE_EVERY_MINUTES = 15
# Thread löschen, wenn ein Banner so oft hintereinander fehlt oder ausverkauft ist
NOT_FOUND_DELETE_AFTER = 2
# Pool-Wechsel-Alarm ab so vielen abgefangenen Pack-Anstiegen in einem Scrape (mind. 20 % der Banner)
POOL_SWITCH_MIN_RISES = 5
# Meldung im Admin-Kanal nach so vielen fehlerhaften Scrapes in Folge
SCRAPE_PROBLEM_ALERT_AFTER = 3


MEDAL_EMOJIS = {"T1": "🥇", "T2": "🥈", "T3": "🥉", "T4": "4️⃣", "T5": "5️⃣", "T6": "6️⃣",
                "T7": "7️⃣", "T8": "8️⃣", "T9": "9️⃣", "T10": "🔟"}
EMOJI_TO_MEDAL = {emoji: tier for tier, emoji in MEDAL_EMOJIS.items()}




def format_end_date_countdown(sale_end_date: str) -> str:
    """Konvertiert Enddatum zu Countdown-Format (z.B. 'Endet in 3 Tagen')."""
    if not sale_end_date:
        return None

    try:
        # Versuche Datum aus String zu extrahieren (Format: "2026/01/23 まで販売" oder "2026/01/23")
        date_match = regex_module.search(r'(\d{4})/(\d{2})/(\d{2})', sale_end_date)
        if not date_match:
            return sale_end_date  # Fallback zum Original

        year, month, day = int(date_match.group(1)), int(date_match.group(2)), int(date_match.group(3))
        end_date = datetime(year, month, day, 23, 59, 59)  # Ende des Tages

        now = datetime.now()
        delta = end_date - now
        days = delta.days

        if days < 0:
            return "Abgelaufen"
        elif days == 0:
            return "Endet heute!"
        elif days == 1:
            return "Endet morgen"
        elif days <= 7:
            return f"Endet in {days} Tagen"
        else:
            # Deutsches Datumsformat für längere Zeiträume
            months_de = ["", "Januar", "Februar", "März", "April", "Mai", "Juni",
                        "Juli", "August", "September", "Oktober", "November", "Dezember"]
            return f"{day}. {months_de[month]} {year}"
    except Exception:
        return sale_end_date  # Fallback zum Original


@dataclass
class RecoveredBanner:
    """Minimale Banner-Daten für Wiederherstellung aus Discord."""
    pack_id: int
    category: str
    title: str = None
    best_hit: str = None
    price_coins: int = None
    current_packs: int = None
    total_packs: int = None
    entries_per_day: int = None
    sale_end_date: str = None
    image_url: str = None
    detail_page_url: str = None


class GTCHABot(commands.Bot):
    def __init__(self):
        intents = discord.Intents.default()
        intents.message_content = True
        intents.guilds = True

        super().__init__(
            command_prefix="!",
            intents=intents,
        )

        self.db = Database()
        self.scheduler = AsyncIOScheduler()
        self._scraper: Optional[GTCHAScraper] = None
        self._scrape_lock = asyncio.Lock()  # Verhindert parallele Scrape-Läufe
        self._last_full_scrape: Optional[datetime] = None
        self._scrape_problems = 0
        self._problem_alerted = False
        self._rises_ignored = 0
        self._last_pool_alert: Optional[datetime] = None

    async def setup_hook(self):
        """Setup beim Start."""
        await self.db.init()
        logger.info(f"Datenbank initialisiert: {self.db.db_path}")

        # Slash Commands registrieren
        self.tree.add_command(app_commands.Command(
            name="refresh",
            description="Manuelles Scraping starten",
            callback=self.refresh_command
        ))
        self.tree.add_command(app_commands.Command(
            name="status",
            description="Bot-Status anzeigen",
            callback=self.status_command
        ))
        self.tree.add_command(app_commands.Command(
            name="hotbanner",
            description="Hot-Banner manuell aktualisieren",
            callback=self.hotbanner_command
        ))

        # Scheduler starten (mit Timeout-Wrapper)
        # Läuft alle X Minuten um xx:00:20, xx:05:20, xx:10:20, etc.
        # (20 Sekunden nach der vollen Minute, da neue Banner um :00 und :30 kommen)
        self.scheduler.add_job(
            self._scrape_with_timeout,
            'cron',
            minute=f'*/{SCRAPE_INTERVAL_MINUTES}',  # Intervall aus Config
            second=20,     # 20 Sekunden nach der Minute
            id='scrape_job',
            replace_existing=True,
            coalesce=True,  # Verpasste Jobs zusammenfassen
            max_instances=1,  # Maximal eine Instanz gleichzeitig
            misfire_grace_time=SCRAPE_INTERVAL_MINUTES * 60,  # Grace Time = Intervall
        )
        self.scheduler.add_job(
            self._backup_database, 'cron', hour=3, minute=30,
            id='db_backup_job', replace_existing=True, coalesce=True, max_instances=1,
        )
        self.scheduler.start()
        # Wächter: ohne erfolgreichen Scrape in 20 Min wird der Prozess neu gestartet
        start_watchdog(max_silence_seconds=20 * 60, startup_grace_seconds=20 * 60)
        logger.info(f"Scheduler: Alle {SCRAPE_INTERVAL_MINUTES} Min um xx:xx:20")

        # Hot-Banner Job (alle 30 Min um xx:00:20 und xx:30:20)
        if HOT_BANNER_CHANNEL_ID and HOT_BANNER_ENABLED:
            self.scheduler.add_job(
                self._update_hot_banners,
                'cron',
                minute='0,30',  # Um :00 und :30
                second=20,      # 20 Sekunden nach der Minute
                id='hot_banner_job',
                replace_existing=True,
                coalesce=True,
                max_instances=1,
            )
            logger.info("Hot-Banner Scheduler: Alle 30 Min um xx:00:20 und xx:30:20")

        # Archiv-Bereinigung: Alle 30 Min alte archivierte Daten löschen
        self.scheduler.add_job(
            self._purge_archived_data,
            'interval',
            minutes=30,
            id='purge_archived_job',
            replace_existing=True,
            coalesce=True,
            max_instances=1,
        )
        logger.info("Archiv-Bereinigung Scheduler: Alle 30 Min (löscht Daten älter als 1 Stunde)")

        # Täglicher Auto-Restart (Railway)
        if DAILY_RESTART_TIME:
            try:
                hour, minute = DAILY_RESTART_TIME.split(":")
                self.scheduler.add_job(
                    self._daily_restart,
                    'cron',
                    hour=int(hour),
                    minute=int(minute),
                    id='daily_restart_job',
                    replace_existing=True,
                )
                logger.info(f"Daily Restart Scheduler: Täglich um {DAILY_RESTART_TIME} UTC")
            except ValueError:
                logger.error(f"Ungültiges DAILY_RESTART_TIME Format: '{DAILY_RESTART_TIME}' (erwartet HH:MM)")

        # Commands synchronisieren
        if GUILD_ID:
            guild = discord.Object(id=int(GUILD_ID))
            self.tree.copy_global_to(guild=guild)
            await self.tree.sync(guild=guild)
            logger.info("Slash Commands synchronisiert")

    async def on_ready(self):
        logger.info(f"Bot online: {self.user}")

        # Notification-System initialisieren
        set_bot_client(self)

        # Memory-Monitor starten
        await memory_monitor.start()

        # Startup-Benachrichtigung senden
        await notify_bot_started()

        # Nur die Thread-Wiederherstellung muss vor dem ersten Scrape fertig sein
        # (sonst entstehen doppelte Threads); der Rest läuft parallel zum Scrape.
        await self._recover_threads_from_discord()

        # Erster Scrape sofort - über Scheduler triggern statt direkt aufrufen,
        # das vermeidet Konflikte mit dem regulären Scheduler-Job
        self.scheduler.modify_job('scrape_job', next_run_time=datetime.now())

        self._startup_tasks = asyncio.gather(
            self._sync_medals_from_discord(),
            self._cleanup_duplicate_probability_messages(),
            self._refresh_all_embeds_once(),
            return_exceptions=True,
        )

    async def _recover_threads_from_discord(self):
        """Stellt Thread-Daten aus Discord wieder her (für DB-Verlust nach Neustart)."""
        logger.info("Prüfe Discord-Threads zur Wiederherstellung...")
        recovered_count = 0

        # Alle Forum-Channel-IDs sammeln
        forum_channel_ids = set()
        channel_to_category = {}
        for category, channel_id in CHANNEL_IDS.items():
            if channel_id:
                forum_channel_ids.add(int(channel_id))
                channel_to_category[int(channel_id)] = category

        # Alle aktiven Threads vom Server holen (nicht aus Cache!)
        if GUILD_ID:
            try:
                guild_id = int(GUILD_ID)

                # HTTP API direkt nutzen um aktive Threads zu holen
                data = await self.http.get_active_threads(guild_id)
                threads_data = data.get('threads', [])
                logger.info(f"Gefundene aktive Threads im Guild: {len(threads_data)}")

                for thread_data in threads_data:
                    try:
                        thread_id = int(thread_data['id'])
                        parent_id = int(thread_data.get('parent_id', 0))
                        thread_name = thread_data.get('name', '')

                        # Nur Threads aus unseren Forum-Channels
                        if parent_id not in forum_channel_ids:
                            continue

                        category = channel_to_category.get(parent_id)
                        if not category:
                            continue

                        # Thread-Titel parsen: "ID: 15257 / Kosten: 1111 / Anzahl: 10 / Gesamt: 500"
                        match = re.match(r'ID:\s*(\d+)', thread_name)
                        if not match:
                            logger.debug(f"Thread-Titel passt nicht: {thread_name}")
                            continue

                        pack_id = int(match.group(1))

                        # Prüfen ob schon in DB
                        existing_thread = await self.db.get_thread_by_banner_id(pack_id)
                        if existing_thread:
                            continue  # Thread bereits bekannt

                        # Thread-Objekt holen für Starter-Message
                        thread = self.get_channel(thread_id)
                        if not thread:
                            try:
                                thread = await self.fetch_channel(thread_id)
                            except:
                                thread = None

                        # Starter-Message holen (erste Nachricht im Thread)
                        starter_message_id = None
                        if thread:
                            try:
                                # Forum-Threads haben eine starter_message
                                if hasattr(thread, 'starter_message') and thread.starter_message:
                                    starter_message_id = thread.starter_message.id
                                else:
                                    # Fallback: erste Nachricht holen
                                    async for msg in thread.history(limit=1, oldest_first=True):
                                        starter_message_id = msg.id
                                        break
                            except Exception as e:
                                logger.debug(f"Konnte Starter-Message nicht holen: {e}")

                        # Thread in DB speichern
                        await self.db.save_thread(
                            banner_id=pack_id,
                            thread_id=thread_id,
                            channel_id=parent_id,
                            starter_message_id=starter_message_id or 0
                        )

                        # Prüfen ob Banner schon in DB existiert - wenn ja, NICHT überschreiben!
                        # (sonst wird image_url/detail_page_url mit NULL überschrieben)
                        existing_banner = await self.db.get_banner(pack_id)
                        if not existing_banner:
                            # Banner-Daten aus Thread-Titel extrahieren
                            price_match = re.search(r'Kosten:\s*(\d+)', thread_name)
                            entries_match = re.search(r'Anzahl:\s*(\d+)', thread_name)
                            total_match = re.search(r'Gesamt:\s*(\d+)', thread_name)

                            banner = RecoveredBanner(
                                pack_id=pack_id,
                                category=category,
                                price_coins=int(price_match.group(1)) if price_match else None,
                                entries_per_day=int(entries_match.group(1)) if entries_match else None,
                                total_packs=int(total_match.group(1)) if total_match else None,
                                current_packs=None,  # Unbekannt bei Wiederherstellung - kein falsches Update
                            )

                            await self.db.save_banner(banner)
                        recovered_count += 1
                        logger.info(f"Thread wiederhergestellt: {pack_id} ({thread_name})")

                    except Exception as e:
                        logger.debug(f"Fehler bei Thread {thread_name}: {e}")

            except Exception as e:
                logger.warning(f"Fehler beim Abrufen aktiver Threads: {e}")

        # Archivierte Threads löschen (nicht wiederherstellen!)
        # Archivierte Threads sind abgelaufen und sollten entfernt werden
        for category, channel_id in CHANNEL_IDS.items():
            if not channel_id:
                continue

            try:
                channel = self.get_channel(int(channel_id))
                if not channel:
                    try:
                        channel = await self.fetch_channel(int(channel_id))
                    except Exception:
                        continue

                if not isinstance(channel, discord.ForumChannel):
                    continue

                try:
                    archived_threads = []
                    async for thread in channel.archived_threads(limit=100):
                        archived_threads.append(thread)

                    for thread in archived_threads:
                        try:
                            await discord_rate_limiter.acquire("thread_delete")
                            await thread.delete()
                            logger.info(f"Archivierten Thread gelöscht: {thread.name} ({thread.id})")
                        except discord.NotFound:
                            pass
                        except Exception as e:
                            logger.debug(f"Fehler beim Löschen von archiviertem Thread {thread.id}: {e}")
                except Exception as e:
                    logger.debug(f"Fehler bei archivierten Threads: {e}")

            except Exception as e:
                logger.warning(f"Fehler bei Channel {category}: {e}")

        if recovered_count > 0:
            logger.info(f"Thread-Wiederherstellung abgeschlossen: {recovered_count} Threads wiederhergestellt")
        else:
            logger.info("Keine Threads zur Wiederherstellung gefunden")

    async def _sync_medals_from_discord(self):
        """Synchronisiert Medaillen-Reaktionen von Discord in die Datenbank."""
        logger.info("Synchronisiere Medaillen von Discord-Reaktionen...")
        synced_count = 0

        # Alle aktiven Threads aus der DB holen
        try:
            async with aiosqlite.connect(self.db.db_path) as db:
                db.row_factory = aiosqlite.Row
                cursor = await db.execute(
                    "SELECT thread_id, starter_message_id, t1_claimed, t2_claimed, t3_claimed FROM discord_threads WHERE is_expired = 0"
                )
                threads = await cursor.fetchall()

            for thread_row in threads:
                thread_id = thread_row['thread_id']
                starter_message_id = thread_row['starter_message_id']

                if not starter_message_id:
                    continue

                try:
                    # Thread und Starter-Message holen
                    thread = self.get_channel(thread_id)
                    if not thread:
                        thread = await self.fetch_channel(thread_id)

                    if not thread or not isinstance(thread, discord.Thread):
                        continue

                    # Medaillen von Reaktionen lesen
                    reaction_medals = await self._get_medals_from_reactions(thread, starter_message_id)

                    known = await self.db.get_medals(thread_id)
                    for tier in reaction_medals:
                        if tier not in known:
                            # Medaille ist auf Discord, aber nicht in der DB (Gewinner unbekannt)
                            await self.db.save_medal(thread_id, tier, 0)
                            synced_count += 1
                            logger.debug(f"Medaille {tier} für Thread {thread_id} synchronisiert")

                except discord.NotFound:
                    logger.debug(f"Thread {thread_id} nicht mehr gefunden")
                except Exception as e:
                    logger.debug(f"Fehler bei Medal-Sync für Thread {thread_id}: {e}")

        except Exception as e:
            logger.error(f"Fehler bei Medal-Synchronisation: {e}")

        if synced_count > 0:
            logger.info(f"Medal-Synchronisation abgeschlossen: {synced_count} Medaillen synchronisiert")
        else:
            logger.info("Keine Medaillen zur Synchronisation gefunden")

    async def _cleanup_duplicate_probability_messages(self):
        """Löscht doppelte Wahrscheinlichkeits-Nachrichten in allen Threads (behält nur die neueste)."""
        logger.info("Räume doppelte Wahrscheinlichkeits-Nachrichten auf...")
        total_deleted = 0
        threads_cleaned = 0

        try:
            # Alle aktiven Threads aus der DB holen
            async with aiosqlite.connect(self.db.db_path) as db:
                db.row_factory = aiosqlite.Row
                cursor = await db.execute(
                    "SELECT thread_id FROM discord_threads WHERE is_expired = 0"
                )
                threads = await cursor.fetchall()

            for thread_row in threads:
                thread_id = thread_row['thread_id']

                try:
                    # Thread holen
                    thread = self.get_channel(thread_id)
                    if not thread:
                        thread = await self.fetch_channel(thread_id)

                    if not thread or not isinstance(thread, discord.Thread):
                        continue

                    # Alle Probability-Nachrichten im Thread finden
                    probability_messages = []
                    async for message in thread.history(limit=100):
                        # Nur Bot-Nachrichten prüfen
                        if message.author.id != self.user.id:
                            continue
                        # Prüfen ob es eine Probability-Nachricht ist
                        if message.content and message.content.startswith("🎯 **Hit-Chance:**"):
                            probability_messages.append(message)

                    # Wenn mehr als eine Probability-Nachricht gefunden wurde
                    if len(probability_messages) > 1:
                        # Nach Erstellungsdatum sortieren (neueste zuerst)
                        probability_messages.sort(key=lambda m: m.created_at, reverse=True)

                        # Die neueste behalten, alle anderen löschen
                        newest_message = probability_messages[0]
                        messages_to_delete = probability_messages[1:]

                        for msg in messages_to_delete:
                            try:
                                await discord_rate_limiter.acquire("message_delete")
                                await msg.delete()
                                total_deleted += 1
                                logger.debug(f"Doppelte Probability-Nachricht {msg.id} in Thread {thread_id} gelöscht")
                            except discord.NotFound:
                                pass  # Nachricht bereits gelöscht
                            except Exception as e:
                                logger.debug(f"Fehler beim Löschen der Nachricht {msg.id}: {e}")

                        # Message-ID der neuesten in DB speichern
                        await self.db.update_probability_message_id(thread_id, newest_message.id)
                        threads_cleaned += 1
                        logger.info(f"Thread {thread_id}: {len(messages_to_delete)} doppelte Nachricht(en) gelöscht")

                    elif len(probability_messages) == 1:
                        # Nur eine Nachricht - ID in DB speichern falls nicht vorhanden
                        existing_id = await self.db.get_probability_message_id(thread_id)
                        if not existing_id:
                            await self.db.update_probability_message_id(thread_id, probability_messages[0].id)
                            logger.debug(f"Thread {thread_id}: Probability-Message-ID in DB gespeichert")

                except discord.NotFound:
                    logger.debug(f"Thread {thread_id} nicht mehr gefunden")
                except Exception as e:
                    logger.debug(f"Fehler bei Cleanup für Thread {thread_id}: {e}")

        except Exception as e:
            logger.error(f"Fehler bei Probability-Message-Cleanup: {e}")

        if total_deleted > 0:
            logger.info(f"Cleanup abgeschlossen: {total_deleted} doppelte Nachricht(en) in {threads_cleaned} Thread(s) gelöscht")
        else:
            logger.info("Cleanup abgeschlossen: Keine doppelten Probability-Nachrichten gefunden")

    async def scrape_and_post(self):
        """Hauptfunktion: Scrapen und neue Banner posten."""
        logger.info("Scrape startet...")
        start_time = datetime.now()

        self._rises_ignored = 0
        if await new_tor_identity():
            logger.debug("[TOR] Neue Route für diesen Scrape")
        try:
            async with GTCHAScraper(BASE_URL) as scraper:
                self._scraper = scraper
                try:
                    api_items = await scraper.fetch_pack_list()
                except Exception as e:
                    logger.warning(f"[API] pack/list fehlgeschlagen: {e}")
                    api_items = {}

                created_from_api = await self._create_banners_from_api(api_items) if api_items else []
                if api_items:
                    await self._announce_started_banners(api_items)
                full_reason = await self._full_scrape_reason(api_items)
                if full_reason:
                    logger.info(f"Voller Scrape mit Tabs: {full_reason}")
                    banners = await scraper.scrape_all_banners()
                    if not scraper._api_pack_data and api_items:
                        scraper._api_pack_data = dict(api_items)
                    if banners and api_items:
                        self._last_full_scrape = datetime.now()
                        # Pack-Zahlen immer aus pack/list (eine Quelle, kein Balken-Rückfall)
                        for b in banners:
                            if b.pack_id in api_items:
                                b.current_packs = _int(api_items[b.pack_id].get('pack_count'))
                else:
                    banners = await self._banners_from_api(api_items)
                    logger.info(f"Schneller Scrape über die API: {len(banners)} bekannte Banner")

                if not banners:
                    logger.warning("Keine Banner gefunden!")
                    await self._report_scrape_problem("Keine Banner gefunden (API und Tabs leer)")
                    return
                if api_items:
                    await self._report_scrape_ok()
                else:
                    await self._report_scrape_problem("pack/list über Tor lieferte keine Daten (Tab-Scrape lief)")

                # Verarbeite Banner
                new_count = 0
                skipped_empty = 0
                deleted_count = 0
                skipped_inactive = 0

                # Semaphore für parallele Updates (max 5 gleichzeitig)
                update_semaphore = asyncio.Semaphore(5)

                # Sammle Updates für parallele Verarbeitung
                update_tasks = []
                new_banner_ids = list(created_from_api)

                for banner in banners:
                    try:
                        # Pruefe ob Banner neu ist
                        existing = await self.db.get_banner(banner.pack_id)

                        # Inaktive Banner die wieder auf der Website erscheinen reaktivieren
                        # (kann passieren wenn bot falscherweise 0-Pack via Proxy-Fehler gelöscht hat)
                        if existing and existing.get('is_active') == 0 and (banner.current_packs or 0) > 0:
                            logger.info(f"Banner {banner.pack_id} wieder auf Website - reaktiviere und erstelle Thread neu")
                            existing = None  # Als neuen Banner behandeln (save_banner setzt is_active=1)

                        # Banner mit 0 Packs: nur überspringen, NICHT löschen
                        # DE-Proxy kann 0 zurückgeben für JP-Only-Pool-Banner die noch aktiv sind.
                        # Echte Löschung erfolgt wenn Banner vom Website verschwindet (not_found >= 20).
                        if banner.current_packs is not None and banner.current_packs == 0:
                            skipped_empty += 1
                            continue

                        if not existing:
                            # Neuer Banner - sequentiell verarbeiten (Thread erstellen)
                            await self.db.save_banner(banner)
                            await self._post_banner_to_discord(banner)
                            new_banner_ids.append(banner.pack_id)
                            new_count += 1
                            logger.info(f"Neu: {banner.pack_id} ({banner.category})")

                            # Cache aktualisieren
                            await banner_cache.set(banner.pack_id, {
                                'current_packs': banner.current_packs,
                                'price_coins': banner.price_coins,
                                'entries_per_day': banner.entries_per_day,
                                'total_packs': banner.total_packs
                            })
                        else:
                            # Existierender Banner - für parallele Verarbeitung sammeln
                            update_tasks.append(
                                self._process_banner_update(banner, existing, update_semaphore)
                            )

                    except Exception as e:
                        logger.error(f"Fehler bei Banner {banner.pack_id}: {e}")

                # Parallele Verarbeitung der Updates
                if update_tasks:
                    logger.info(f"Verarbeite {len(update_tasks)} Banner-Updates parallel...")
                    results = await asyncio.gather(*update_tasks, return_exceptions=True)
                    updated_count = sum(1 for r in results if isinstance(r, dict) and r.get('updated'))
                    error_count = sum(1 for r in results if isinstance(r, Exception) or (isinstance(r, dict) and r.get('error')))
                    if updated_count > 0:
                        logger.info(f"   {updated_count} Banner erfolgreich aktualisiert")
                    if error_count > 0:
                        logger.warning(f"   {error_count} Banner mit Fehlern")
                    await self._check_pool_switch(len(update_tasks))

                # === NICHT-GEFUNDEN-TRACKING ===
                # "Gefunden" = laut Seite noch Packs übrig. Ausverkaufte (0 Packs) und verschwundene
                # Banner zählen hoch und werden nach NOT_FOUND_DELETE_AFTER Scrapes gelöscht.
                api_items = getattr(scraper, '_api_pack_data', {}) or api_items
                if api_items:
                    found_banner_ids = {pid for pid, it in api_items.items() if _int(it.get('pack_count')) > 0}
                else:
                    found_banner_ids = {b.pack_id for b in banners if (b.current_packs or 0) > 0}
                scraped_ids = {b.pack_id for b in banners}

                # === API-ONLY PACK-UPDATES ===
                # Für DB-Banner die nicht im DOM-Scrape auftauchten (z.B. Banner die auf keinem
                # sichtbaren Tab landen), Pack-Zahlen direkt aus der Proxy-API aktualisieren.
                api_pack_data = getattr(scraper, '_api_pack_data', {})
                if api_pack_data:
                    db_banners_all = await self.db.get_all_active_banners_basic()
                    pack_fields = ['pack_count', 'pack_remaining', 'remaining_count', 'remaining',
                                   'stock', 'packs', 'pack_num', 'pack_stock', 'count']
                    api_only_count = 0
                    for db_b in db_banners_all:
                        pid = db_b['pack_id']
                        if pid in scraped_ids:
                            continue  # Schon normal verarbeitet
                        api_item = api_pack_data.get(pid)
                        if not api_item:
                            continue  # Keine API-Daten für diesen Banner
                        new_packs = None
                        for field in pack_fields:
                            val = api_item.get(field)
                            if val is not None:
                                new_packs = int(val)
                                break
                        if new_packs is None or new_packs == 0:
                            continue
                        old_packs = db_b.get('current_packs')
                        total_packs = db_b.get('total_packs')
                        if old_packs is None:
                            await self.db.update_banner_packs(pid, new_packs)
                            api_only_count += 1
                        elif new_packs > old_packs:
                            logger.warning(f"[PACK-ANSTIEG IGNORIERT/API] {pid}: {old_packs} -> {new_packs}")
                        elif new_packs < old_packs:
                            posted = await self._post_pack_update_to_thread(pid, old_packs, new_packs, total_packs)
                            if posted:
                                await self.db.update_banner_packs(pid, new_packs)
                                api_only_count += 1
                                logger.info(f"API-Only Pack-Update: {pid} ({old_packs} → {new_packs})")
                    if api_only_count > 0:
                        logger.info(f"API-Only Updates: {api_only_count} Banner außerhalb der gescrapten Kategorien aktualisiert")

                # === KARTENPOOL (Top 5, Ø Rückgabe) ===
                # Die Kartenliste ändert sich nicht; pro Scrape nur wenige fehlende Pools nachladen
                # (neue Banner zuerst), damit der Scrape kurz bleibt.
                missing_pools = await self.db.get_banners_without_pool(
                    limit=POOL_FETCH_PER_SCRAPE, prefer_ids=new_banner_ids)
                if missing_pools:
                    try:
                        pools = await scraper.fetch_card_pools(missing_pools)
                    except Exception as e:
                        logger.warning(f"[POOL] Kartenpools nicht geladen: {e}")
                        pools = {}
                    for pid, pool in pools.items():
                        await self.db.save_card_pool(pid, pool)
                        await self._refresh_pool_views(pid, initial_pool=True)

                # === HIT-ERKENNUNG über die Rückgabe-Zähler aus pack/list ===
                await self._detect_pulled_hits(getattr(scraper, '_api_pack_data', {}) or {})

                # === HIT-LISTEN abgleichen (korrigiert alles, was vom Soll abweicht) ===
                for pid, row in (await self.db.get_active_banners()).items():
                    if row.get('card_pool'):
                        await self._refresh_pool_views(pid, embed=False)

                # === KAUFBEDINGUNGEN und VERSAND-ZAHLEN aus pack/list ===
                for pid, item in (getattr(scraper, '_api_pack_data', {}) or {}).items():
                    changed = await self.db.update_conditions(pid, banner_conditions(item))
                    if _int(item.get('point')) > 0:
                        changed = await self.db.update_price(pid, _int(item.get('point'))) or changed
                    changed = await self.db.update_site_stats(pid, shipping_stats(item)) or changed
                    if changed:
                        row = await self.db.get_banner(pid)
                        if row and row.get('is_active'):
                            await self._update_thread_embed(row)

                # Hole alle bekannten Banner aus der DB
                db_banner_ids = set(await self.db.get_all_active_banner_ids())

                # SCHUTZ: Nur tracken, wenn die Daten vollständig wirken (API-Liste mit genug Bannern
                # bzw. ein voller Tab-Scrape). Verhindert Massen-Löschung bei fehlgeschlagenem Scrape.
                MIN_BANNERS_FOR_TRACKING = 10
                expired_count = 0
                tracking_base = len(api_items)

                if not api_items or tracking_base < MIN_BANNERS_FOR_TRACKING:
                    # Ohne vollständige API-Liste wird nichts gelöscht (ein fehlender Tab reicht sonst)
                    logger.warning(f"⚠️ Keine verlässliche Banner-Liste ({tracking_base} Banner) - "
                                   f"Not-Found-Tracking übersprungen")
                else:
                    # Für gefundene Banner: Zähler zurücksetzen (Batch-Update statt N Einzelqueries)
                    found_in_db = list(found_banner_ids & db_banner_ids)
                    if found_in_db:
                        await self.db.batch_reset_not_found_count(found_in_db)

                    # Für NICHT gefundene Banner: Zähler erhöhen (Batch-Update)
                    not_found_ids = list(db_banner_ids - found_banner_ids)
                    if not_found_ids:
                        logger.debug(f"{len(not_found_ids)} Banner nicht gefunden - erhöhe Zähler")
                        expired_ids = await self.db.batch_increment_not_found_count(
                            not_found_ids, threshold=NOT_FOUND_DELETE_AFTER)

                        for pack_id in expired_ids:
                            logger.info(f"Banner {pack_id} {NOT_FOUND_DELETE_AFTER}x nicht gefunden oder "
                                        f"ausverkauft - lösche Thread")
                            deleted = await self._delete_banner_thread(pack_id)
                            if deleted:
                                expired_count += 1
                                logger.info(f"   Banner {pack_id} (abgelaufen) Thread gelöscht!")

                elapsed = (datetime.now() - start_time).total_seconds()
                if skipped_inactive > 0:
                    logger.debug(f"Übersprungen: {skipped_inactive} inaktive Banner")
                logger.info(f"Scrape done: {elapsed:.1f}s, {new_count} neu, {deleted_count} archiviert, {expired_count} abgelaufen")
                touch_heartbeat()

                # Erfolgs-Benachrichtigung immer senden
                await notify_scrape_success(
                    new_banners=new_count,
                    deleted_banners=deleted_count,
                    expired_banners=expired_count,
                    duration_seconds=elapsed,
                    total_banners=len(banners)
                )

        except Exception as e:
            logger.error(f"Scrape-Fehler: {e}")
            await self._report_scrape_problem(f"Scrape-Fehler: {e}")
        finally:
            self._scraper = None

    async def _check_pool_switch(self, banner_count: int):
        """Viele abgefangene Pack-Anstiege auf einmal = die Seite liefert wohl wieder den falschen Pool."""
        rises = self._rises_ignored
        if rises < max(POOL_SWITCH_MIN_RISES, banner_count * 0.2):
            return
        logger.warning(f"[ÜBERWACHUNG] {rises} von {banner_count} Bannern mit gestiegenen Packs - Pool-Wechsel?")
        now = datetime.now()
        if self._last_pool_alert and now - self._last_pool_alert < timedelta(hours=3):
            return
        self._last_pool_alert = now
        await notify_critical_error(
            f"Bei {rises} von {banner_count} Bannern meldet die Seite gerade **mehr** Packs als zuvor.\n"
            f"Das spricht dafür, dass der Bot wieder einen falschen Pack-Pool sieht. Die Anstiege werden "
            f"ignoriert, aber neue Käufe könnten fehlen.\n"
            f"Prüfen: `docker restart gtcha-tor` und danach die Pack-Zahlen mit der Seite vergleichen."
        )

    async def _backup_database(self):
        try:
            target = await asyncio.to_thread(backup_database, self.db.db_path)
            logger.info(f"Datenbank-Backup erstellt: {target}")
        except Exception as e:
            logger.error(f"Datenbank-Backup fehlgeschlagen: {e}")
            await notify_critical_error(f"Datenbank-Backup fehlgeschlagen: {e}")

    async def _report_scrape_problem(self, reason: str):
        """Zählt Probleme in Folge; ab SCRAPE_PROBLEM_ALERT_AFTER einmalig Meldung im Admin-Kanal."""
        self._scrape_problems += 1
        logger.warning(f"[ÜBERWACHUNG] Problem {self._scrape_problems}x in Folge: {reason}")
        if self._scrape_problems == SCRAPE_PROBLEM_ALERT_AFTER:
            self._problem_alerted = True
            await notify_critical_error(
                f"Der Bot hat seit {self._scrape_problems} Scrapes in Folge Probleme.\n"
                f"Letzter Grund: {reason}\n\n"
                f"Prüfen: `docker logs --tail 50 gtcha-tor` und `docker logs --tail 50 gtcha-discord-bot`"
            )

    async def _report_scrape_ok(self):
        if self._problem_alerted:
            await send_notification(
                title="Scrape läuft wieder",
                description=f"Nach {self._scrape_problems} fehlerhaften Scrapes kommen wieder Daten.",
                color=0x2ECC71,
            )
        self._scrape_problems = 0
        self._problem_alerted = False

    @staticmethod
    def _new_banner_candidates(api_items: dict, states: dict) -> set:
        """Laufende oder angekündigte Banner mit Packs, die dem Bot unbekannt oder inaktiv sind."""
        return {pid for pid, it in api_items.items()
                if _int(it.get('pack_count')) > 0 and not states.get(pid) and category_for(it)}

    async def _create_banners_from_api(self, api_items: dict) -> list:
        """Legt neue Banner direkt aus pack/list an (auch vor Verkaufsstart); gibt die IDs zurück."""
        created = []
        states = await self.db.get_banner_states()
        for pid in sorted(self._new_banner_candidates(api_items, states)):
            item = api_items[pid]
            image = (item.get('image') or [None])[0]
            limit = _int(item.get('max_buy_count'))
            banner = ScrapedBanner(
                pack_id=pid,
                category=category_for(item),
                price_coins=_int(item.get('point')) or None,
                current_packs=_int(item.get('pack_count')),
                total_packs=_int(item.get('total_pack_count')) or None,
                entries_per_day=limit or None,
                sale_end_date=item.get('end_date'),
                image_url=f"{BASE_URL}{image.split('?')[0]}" if image else None,
                detail_page_url=f"{BASE_URL}/pack-detail?packId={pid}",
            )
            starts_at = jst_timestamp(item.get('start_date'))
            upcoming = is_upcoming(item)
            await self.db.save_banner(banner)
            await self.db.set_start(pid, starts_at, announced=not upcoming)
            await self.db.update_conditions(pid, banner_conditions(item))
            await self._post_banner_to_discord(banner, starts_at=starts_at if upcoming else None)
            created.append(pid)
            logger.info(f"Neu aus API: {pid} ({banner.category}){' - angekündigt' if upcoming else ''}")
        return created

    async def _announce_started_banners(self, api_items: dict):
        """Postet bei angekündigten Bannern, sobald der Verkauf läuft."""
        now = datetime.now().timestamp()
        for pid, row in (await self.db.get_active_banners()).items():
            if row.get('start_announced') or not row.get('starts_at'):
                continue
            item = api_items.get(pid)
            if (item and is_upcoming(item)) or (not item and now < row['starts_at']):
                continue
            await self.db.set_start(pid, row['starts_at'], announced=True)
            thread_data = await self.db.get_thread_by_banner_id(pid)
            if not thread_data or thread_data.get('is_expired'):
                continue
            try:
                thread = self.get_channel(int(thread_data['thread_id'])) or await self.fetch_channel(
                    int(thread_data['thread_id']))
                if thread.archived:
                    await discord_rate_limiter.acquire("thread_edit")
                    await thread.edit(archived=False)
                mention = "@everyone " if MENTION_ON_NEW_THREAD else ""
                await discord_rate_limiter.acquire("message_send")
                await thread.send(f"{mention}🟢 **Verkauf gestartet!** Ab jetzt kann gezogen werden.")
                await self._update_thread_embed(await self.db.get_banner(pid))
                logger.info(f"Start gemeldet: Banner {pid}")
            except Exception as e:
                logger.warning(f"Start-Meldung für {pid} fehlgeschlagen: {e}")

    async def _full_scrape_reason(self, api_items: dict) -> Optional[str]:
        """Grund für einen vollen Tab-Scrape, sonst None (dann reicht die API)."""
        if not api_items:
            return "API-Liste leer"
        if self._last_full_scrape is None:
            return "erster Scrape seit Start"
        if datetime.now() - self._last_full_scrape >= timedelta(minutes=FULL_SCRAPE_EVERY_MINUTES):
            return f"regelmäßig alle {FULL_SCRAPE_EVERY_MINUTES} Min"
        return None

    async def _banners_from_api(self, api_items: dict) -> list:
        """Bekannte aktive Banner mit aktuellen Pack-Zahlen aus pack/list (Rest aus der DB)."""
        rows = await self.db.get_active_banners()
        banners = []
        for pid, item in api_items.items():
            row = rows.get(pid)
            if not row:
                continue
            banners.append(ScrapedBanner(
                pack_id=pid,
                category=row['category'],
                title=row.get('title'),
                best_hit=row.get('best_hit'),
                price_coins=_int(item.get('point')) or row.get('price_coins'),
                current_packs=_int(item.get('pack_count')),
                total_packs=_int(item.get('total_pack_count')) or row.get('total_packs'),
                entries_per_day=row.get('entries_per_day'),
                sale_end_date=row.get('sale_end_date'),
                image_url=row.get('image_url'),
                detail_page_url=row.get('detail_page_url'),
            ))
        return banners

    async def _scrape_with_timeout(self):
        """Wrapper für scrape_and_post mit konfigurierbarem Timeout und Retry-Logik."""
        # Verhindert parallele Scrape-Läufe (z.B. Scheduler + /refresh gleichzeitig)
        if self._scrape_lock.locked():
            logger.warning("Scrape läuft bereits - überspringe diesen Aufruf")
            return

        async with self._scrape_lock:
            timeout_seconds = SCRAPE_TIMEOUT_SECONDS
            max_retries = 2
            retry_delay = 30  # Sekunden zwischen Retries

            for attempt in range(max_retries + 1):
                try:
                    if attempt > 0:
                        logger.info(f"Retry {attempt}/{max_retries} - warte {retry_delay}s...")
                        await asyncio.sleep(retry_delay)

                    await asyncio.wait_for(self.scrape_and_post(), timeout=timeout_seconds)
                    return  # Erfolg - beenden

                except asyncio.TimeoutError:
                    logger.error(f"TIMEOUT: Scrape-Job nach {timeout_seconds}s abgebrochen! (Versuch {attempt + 1}/{max_retries + 1})")
                    await self._report_scrape_problem(f"Zeitüberschreitung nach {timeout_seconds}s")
                    # Webhook-Benachrichtigung
                    await notify_scrape_error(
                        "Timeout",
                        f"Scrape-Job nach {timeout_seconds}s abgebrochen",
                        attempt, max_retries
                    )
                    # Scraper aufräumen falls noch aktiv
                    if self._scraper:
                        try:
                            await self._scraper.close()
                        except Exception:
                            pass
                        self._scraper = None

                    if attempt < max_retries:
                        continue  # Retry
                    else:
                        logger.error("Alle Retries fehlgeschlagen!")
                        await notify_all_retries_failed()

                except Exception as e:
                    logger.error(f"Fehler im Scrape-Job: {e} (Versuch {attempt + 1}/{max_retries + 1})")
                    # Webhook-Benachrichtigung
                    await notify_scrape_error(
                        "Exception",
                        str(e),
                        attempt, max_retries
                    )
                    if self._scraper:
                        try:
                            await self._scraper.close()
                        except Exception:
                            pass
                        self._scraper = None

                    if attempt < max_retries:
                        continue  # Retry
                    else:
                        logger.error("Alle Retries fehlgeschlagen!")
                        await notify_all_retries_failed()

    def _get_banner_value(self, banner, key, default=None):
        """Holt einen Wert aus Banner-Objekt oder Dict."""
        if isinstance(banner, dict):
            return banner.get(key, default)
        return getattr(banner, key, default)

    def _build_banner_embed(self, banner, title_prefix: str = None, stats: Optional[dict] = None,
                            tempo: Optional[str] = None, conditions: Optional[str] = None,
                            shipped: Optional[str] = None, minimum: Optional[str] = None,
                            starts_at: Optional[int] = None) -> discord.Embed:
        """Erstellt ein Embed für einen Banner (funktioniert mit Objekt oder Dict)."""
        # Helper für Zugriff
        get = lambda key, default=None: self._get_banner_value(banner, key, default)

        # Kategorie-Farben
        category_colors = {
            "Bonus": 0xFFD700,      # Gold
            "MIX": 0x9B59B6,        # Lila
            "Pokémon": 0xFFCC00,    # Pokémon-Gelb
            "One piece": 0xE74C3C,  # Rot
            "Dragon Ball": 0xF57C00,  # Orange
        }
        embed_color = category_colors.get(get('category'), 0xFFD700)

        # Titel (mit optionalem Prefix für Hot-Banner)
        banner_title = get('title') or f"Pack {get('pack_id')}"
        if title_prefix:
            banner_title = f"{title_prefix} | {banner_title}"

        embed = discord.Embed(
            title=banner_title,
            url=get('detail_page_url'),
            color=embed_color,
            timestamp=datetime.now()
        )

        # Felder hinzufügen
        starts_at = starts_at or (get('starts_at') if not get('start_announced', 1) else None)
        if starts_at and starts_at > datetime.now().timestamp():
            embed.add_field(name="🕒 Verkaufsstart", value=f"<t:{starts_at}:F> (<t:{starts_at}:R>)", inline=False)

        if get('price_coins'):
            embed.add_field(name="Preis", value=f"{fmt_coins(get('price_coins'))} Coins", inline=True)

        if get('current_packs') is not None and get('total_packs'):
            embed.add_field(
                name="Packs",
                value=f"{fmt_coins(get('current_packs'))} / {fmt_coins(get('total_packs'))}",
                inline=True
            )

        if get('entries_per_day'):
            embed.add_field(name="Pro Tag", value=f"{get('entries_per_day')}x", inline=True)

        if get('best_hit'):
            embed.add_field(name="Best Hit", value=get('best_hit'), inline=False)

        if get('sale_end_date'):
            countdown = format_end_date_countdown(get('sale_end_date'))
            embed.add_field(name="Ende", value=countdown, inline=True)

        if stats:
            ev_text = f"{fmt_coins(stats['ev'])} Coins"
            if stats['ev_pct'] is not None:
                ev_text += f" ({fmt_pct(stats['ev_pct'])} % vom Preis)"
            if stats['estimated']:
                ev_text += "\n*geschätzt aus Kartenpool und Medaillen*"
            embed.add_field(name="Ø Rückgabe pro Zug", value=ev_text, inline=False)

            open_tiers = " ".join(MEDAL_EMOJIS[t] for t in stats['open_tiers']) or "keine"
            if stats['tracked_hits']:
                hits_text = f"{stats['hits_open']} von {stats['hits_total']} noch drin · T1–T3: {open_tiers}"
            else:
                hits_text = f"Top 3 noch drin: {open_tiers}"
            if stats.get('cost_to_hit'):
                hits_text += f"\nØ Kosten bis zum nächsten Hit: ca. {fmt_coins(stats['cost_to_hit'])} Coins"
            embed.add_field(name="Hits", value=hits_text, inline=False)

        if minimum:
            embed.add_field(name="Mindestens zurück pro Zug", value=minimum, inline=False)
        if tempo:
            embed.add_field(name="Abverkauf", value=tempo, inline=False)
        if shipped:
            embed.add_field(name="Verschickt", value=shipped, inline=False)
        if conditions:
            embed.add_field(name="Kaufbedingungen", value=conditions, inline=False)

        embed.set_footer(text=f"Pack ID: {get('pack_id')}")

        # Bild hinzufügen falls vorhanden
        if get('image_url'):
            embed.set_image(url=get('image_url'))

        return embed

    async def _post_banner_to_discord(self, banner, starts_at: Optional[int] = None):
        """Postet einen Banner als Thread in Discord (starts_at = angekündigt, Verkauf startet später)."""

        # Channel fuer Kategorie finden
        channel_id = CHANNEL_IDS.get(banner.category)
        if not channel_id:
            logger.warning(f"Kein Channel fuer Kategorie: {banner.category}")
            return

        channel = self.get_channel(int(channel_id))
        if not channel:
            logger.warning(f"Channel nicht gefunden: {channel_id}")
            return

        # Pruefe ob es ein Forum-Channel ist
        if not isinstance(channel, discord.ForumChannel):
            logger.warning(f"Channel {channel.name} ist kein Forum!")
            return

        # Thread-Titel Format
        price = banner.price_coins or 0
        entries = banner.entries_per_day if banner.entries_per_day else "unbegrenzt"
        total = banner.total_packs or 0
        title = f"ID: {banner.pack_id} / Kosten: {price} Coins / Anzahl Pulls: {entries} / Pulls Gesamt: {total}"
        if len(title) > 100:
            title = title[:97] + "..."

        # Embed erstellen mit Helper-Funktion
        embed = self._build_banner_embed(banner, starts_at=starts_at)

        try:
            # Rate-Limiting für Discord API
            await discord_rate_limiter.acquire("thread_create")

            # Thread erstellen
            thread, message = await channel.create_thread(
                name=title,
                embed=embed,
                reason=f"Neuer Banner: {banner.pack_id}"
            )

            # Thread-ID in DB speichern
            await self.db.save_thread(
                banner_id=banner.pack_id,
                thread_id=thread.id,
                channel_id=channel.id,
                starter_message_id=message.id
            )

            # @everyone Mention bei neuem Thread
            if MENTION_ON_NEW_THREAD:
                await discord_rate_limiter.acquire("message_send")
                if starts_at:
                    await thread.send(f"@everyone 🕒 Neuer Banner angekündigt! Verkaufsstart <t:{starts_at}:R>")
                else:
                    await thread.send("@everyone Neuer Banner verfügbar!")

            # Wahrscheinlichkeit initial posten
            await self._update_probability_message(thread.id, banner.pack_id)

            logger.info(f"Thread erstellt: {title} in #{channel.name}")

        except discord.HTTPException as e:
            logger.error(f"Discord-Fehler beim Thread erstellen: {e}")
        except Exception as e:
            logger.error(f"Fehler beim Thread erstellen: {e}")

    async def _post_pack_update_to_thread(self, pack_id: int, old_packs: int, new_packs: int, total_packs: int) -> bool:
        """Postet einen Kommentar im Thread wenn sich die Pack-Anzahl ändert. Gibt True bei Erfolg zurück."""
        try:
            thread_data = await self.db.get_thread_by_banner_id(pack_id)
            if not thread_data:
                logger.debug(f"Kein Thread für Pack-Update {pack_id}")
                return False

            thread_id = thread_data.get('thread_id')
            if not thread_id:
                return False

            # Thread holen
            thread = self.get_channel(int(thread_id))
            if not thread:
                try:
                    thread = await self.fetch_channel(int(thread_id))
                except discord.NotFound:
                    logger.debug(f"Thread {thread_id} nicht gefunden")
                    return False
                except Exception:
                    return False

            if not isinstance(thread, discord.Thread):
                return False

            # Archivierte Threads entsperren (Discord archiviert inaktive Threads automatisch → keine Posts möglich)
            if thread.archived:
                try:
                    await discord_rate_limiter.acquire("thread_edit")
                    await thread.edit(archived=False)
                    logger.info(f"Thread {thread_id} entsperrt (war archiviert)")
                except Exception as e:
                    logger.warning(f"Konnte Thread {thread_id} nicht entsperren: {e}")
                    return False

            # Kommentar erstellen
            old_packs = old_packs or 0
            new_packs = new_packs or 0
            total = total_packs or 0

            # Emoji basierend auf Veränderung
            if new_packs < old_packs:
                emoji = "📉"
                change = f"-{old_packs - new_packs}"
            else:
                emoji = "📈"
                change = f"+{new_packs - old_packs}"

            message = f"{emoji} **Pack-Update:** {old_packs} → {new_packs} / {total} ({change})"
            if total > 0:
                percent = (new_packs / total) * 100
                filled = int(percent / 10)
                bar = "█" * filled + "░" * (10 - filled)
                message += f"\n`{bar}` {percent:.0f}%"

            # @everyone Mention bei Pack-Update
            if MENTION_ON_PACK_UPDATE:
                message = f"@everyone\n{message}"

            await discord_rate_limiter.acquire("message_send")
            await thread.send(message)
            logger.info(f"Pack-Update gepostet: {pack_id} ({old_packs} → {new_packs})")
            return True

        except discord.HTTPException as e:
            logger.warning(f"Discord-Fehler bei Pack-Update {pack_id}: {e}")
            return False
        except Exception as e:
            logger.warning(f"Fehler bei Pack-Update {pack_id}: {e}")
            return False

    async def _process_banner_update(self, banner, existing: dict, semaphore: asyncio.Semaphore) -> dict:
        """
        Verarbeitet ein Banner-Update parallel.
        Gibt ein dict mit Statistiken zurück.
        """
        async with semaphore:
            result = {'updated': False, 'error': None}
            try:
                old_packs = existing.get('current_packs')
                old_entries = existing.get('entries_per_day')
                title_updated = False

                # URLs aktualisieren falls fehlend
                if banner.image_url or banner.detail_page_url:
                    old_image = existing.get('image_url')
                    old_detail = existing.get('detail_page_url')
                    if (not old_image and banner.image_url) or (not old_detail and banner.detail_page_url):
                        await self.db.update_banner_urls(
                            banner.pack_id,
                            banner.image_url,
                            banner.detail_page_url
                        )
                        logger.debug(f"URLs repariert für Banner {banner.pack_id}")

                # Prüfe ob entries_per_day sich geändert hat
                # Nur updaten wenn neuer Wert nicht None ist (leeres buy_limit ignorieren)
                if banner.entries_per_day is not None and banner.entries_per_day != old_entries:
                    await self.db.update_banner_entries(
                        banner.pack_id,
                        banner.entries_per_day
                    )
                    await self._update_thread_title(banner)
                    title_updated = True
                    old_entries_str = old_entries if old_entries else "unbegrenzt"
                    logger.info(f"Update: {banner.pack_id} Entries: {old_entries_str} -> {banner.entries_per_day}")

                # Packs können auf der Website nur sinken - ein höherer Wert stammt immer
                # aus einer falschen Quelle (anderer Regional-Pool / Cache) und wird verworfen.
                if (old_packs is not None and banner.current_packs is not None
                        and banner.current_packs > old_packs):
                    logger.warning(
                        f"[PACK-ANSTIEG IGNORIERT] {banner.pack_id}: {old_packs} -> {banner.current_packs} "
                        f"(Packs können nicht steigen - bleibe bei {old_packs})"
                    )
                    banner.current_packs = old_packs
                    self._rises_ignored += 1

                packs_changed = banner.current_packs != old_packs

                if packs_changed:
                    logger.info(f"Pack-Änderung erkannt: {banner.pack_id} {old_packs} -> {banner.current_packs}")
                    if old_packs is not None:
                        # Post FIRST - nur bei Erfolg DB updaten
                        # (Fehler: DB updated, Discord-Post schlägt fehl → nächster Scrape erkennt keine Änderung mehr)
                        posted = await self._post_pack_update_to_thread(
                            banner.pack_id,
                            old_packs,
                            banner.current_packs,
                            banner.total_packs
                        )
                        if posted:
                            await self.db.update_banner_packs(banner.pack_id, banner.current_packs)
                        else:
                            logger.warning(f"Pack-Update-Post für {banner.pack_id} fehlgeschlagen - DB bleibt bei {old_packs}, nächster Scrape versucht es erneut")
                            packs_changed = False  # Kein Embed/Probability-Update wenn Post fehlschlug
                    else:
                        # Initiales Pack-Update (kein Discord-Post nötig)
                        await self.db.update_banner_packs(banner.pack_id, banner.current_packs)
                        logger.debug(f"Initiales Pack-Update für {banner.pack_id}: {banner.current_packs}")

                # Embed NUR aktualisieren wenn sich etwas geändert hat
                if packs_changed or title_updated:
                    await self._update_thread_embed(banner)
                    result['updated'] = True

                    if packs_changed:
                        thread_data = await self.db.get_thread_by_banner_id(banner.pack_id)
                        if thread_data and thread_data.get('thread_id'):
                            await self._update_probability_message(
                                thread_data['thread_id'],
                                banner.pack_id
                            )

                # Banner im Cache aktualisieren
                await banner_cache.set(banner.pack_id, {
                    'current_packs': banner.current_packs,
                    'price_coins': banner.price_coins,
                    'entries_per_day': banner.entries_per_day,
                    'total_packs': banner.total_packs
                })

            except Exception as e:
                result['error'] = str(e)
                logger.error(f"Fehler bei Banner {banner.pack_id}: {e}")

            return result

    async def _update_thread_title(self, banner):
        """Aktualisiert den Thread-Titel wenn sich Banner-Daten geändert haben."""
        try:
            thread_data = await self.db.get_thread_by_banner_id(banner.pack_id)
            if not thread_data:
                logger.debug(f"Kein Thread für Titel-Update {banner.pack_id}")
                return

            thread_id = thread_data.get('thread_id')
            if not thread_id:
                return

            # Thread holen
            thread = self.get_channel(int(thread_id))
            if not thread:
                try:
                    thread = await self.fetch_channel(int(thread_id))
                except discord.NotFound:
                    logger.debug(f"Thread {thread_id} nicht gefunden")
                    return
                except Exception:
                    return

            if not isinstance(thread, discord.Thread):
                return

            # Neuen Titel generieren
            price = banner.price_coins or 0
            entries = banner.entries_per_day if banner.entries_per_day else "unbegrenzt"
            total = banner.total_packs or 0
            new_title = f"ID: {banner.pack_id} / Kosten: {price} Coins / Anzahl Pulls: {entries} / Pulls Gesamt: {total}"
            if len(new_title) > 100:
                new_title = new_title[:97] + "..."

            # Nur updaten wenn sich Titel geändert hat
            if thread.name != new_title:
                await discord_rate_limiter.acquire("thread_edit")
                await thread.edit(name=new_title)
                logger.info(f"Thread-Titel aktualisiert: {new_title}")

        except discord.HTTPException as e:
            logger.debug(f"Discord-Fehler bei Titel-Update: {e}")
        except Exception as e:
            logger.debug(f"Fehler bei Titel-Update für {banner.pack_id}: {e}")

    async def _update_thread_embed(self, banner, initial_pool: bool = False):
        """Aktualisiert das Embed im Thread mit aktuellen Daten (z.B. Countdown, Ø Rückgabe)."""
        pack_id = self._get_banner_value(banner, 'pack_id')
        try:
            thread_data = await self.db.get_thread_by_banner_id(pack_id)
            if not thread_data:
                return

            thread_id = thread_data.get('thread_id')
            starter_message_id = thread_data.get('starter_message_id')

            if not thread_id or not starter_message_id:
                return

            # Thread holen
            thread = self.get_channel(int(thread_id))
            if not thread:
                try:
                    thread = await self.fetch_channel(int(thread_id))
                except (discord.NotFound, Exception):
                    return

            if not isinstance(thread, discord.Thread):
                return

            if thread.archived:
                try:
                    await discord_rate_limiter.acquire("thread_edit")
                    await thread.edit(archived=False)
                except Exception:
                    return

            # Starter-Message holen
            try:
                message = await thread.fetch_message(int(starter_message_id))
            except (discord.NotFound, Exception):
                logger.debug(f"Starter-Message für {pack_id} nicht gefunden")
                return

            stats = await self._pool_stats(banner, thread_data)
            tempo = await self._sales_tempo(pack_id, _int(self._get_banner_value(banner, 'current_packs')))
            row = await self.db.get_banner(pack_id) or {}
            conditions = format_conditions(row.get('conditions'))
            shipped = format_shipping(row.get('site_stats'))
            minimum = self._minimum_text(await self.db.get_card_pool(pack_id), row.get('price_coins'))
            new_embed = self._build_banner_embed(banner, stats=stats, tempo=tempo, conditions=conditions,
                                                 shipped=shipped, minimum=minimum)

            # Message updaten
            await discord_rate_limiter.acquire("message_edit")
            await message.edit(embed=new_embed)
            logger.debug(f"Embed aktualisiert für Banner {pack_id}")

            if stats:
                # Beim Nachrüsten alter Threads ist der erste Wert eine Schätzung ohne Verlauf
                # (Medaillen oft nie gesetzt) - dann nur scharf schalten, nicht posten.
                silent = initial_pool and stats['estimated']
                await self._check_value_alert(thread, thread_data, stats, banner, silent=silent)
                await self._check_endspurt(thread, thread_data, stats, banner, silent=initial_pool)

        except discord.HTTPException as e:
            logger.debug(f"Discord-Fehler bei Embed-Update: {e}")
        except Exception as e:
            logger.debug(f"Fehler bei Embed-Update für {pack_id}: {e}")

    async def _hit_chance_text(self, banner: dict, thread_data: Optional[dict], thread_id: int) -> str:
        """Text der 🎯-Nachricht: Chance auf mindestens einen Hit bei 1 / 10 / 50 Zügen."""
        remaining = _int(banner.get('current_packs'))
        pool = await self.db.get_card_pool(banner['pack_id'])
        if pool and pool.get('version') == 2 and pool.get('hits') and thread_data:
            stats = await self._pool_stats(banner, thread_data)
            hits_open, label = stats['hits_open'], f"{stats['hits_open']} von {stats['hits_total']} Hits noch drin"
        else:
            claimed = await self._claimed_tiers(thread_id, banner['pack_id'])
            hits_open = sum(1 for t in TIERS if not claimed.get(t))
            label = f"{hits_open} von 3 Top-Karten noch drin"
        if hits_open <= 0:
            return "🎯 **Hit-Chance:** Alle Hits wurden gezogen!"
        parts = []
        for pulls in HIT_CHANCE_PULLS:
            if pulls > remaining:
                break
            pct = chance_at_least_one(remaining, hits_open, pulls)
            parts.append(f"{pulls} {'Zug' if pulls == 1 else 'Züge'}: {fmt_pct(pct, 2 if pct < 10 else 1)} %")
        text = "🎯 **Hit-Chance:** " + (" · ".join(parts) if parts else "100 %")
        text += f"\n{label} · {fmt_coins(remaining)} Packs übrig"
        if banner.get('entries_per_day'):
            text += f"\n*Max. {banner['entries_per_day']} Züge pro Tag*"
        return text

    async def _pool_stats(self, banner, thread_data: dict) -> Optional[dict]:
        """Ø Rückgabe und Hit-Chance aus Kartenpool, Rest-Packs, Medaillen und erkannten Hits."""
        get = lambda key: self._get_banner_value(banner, key)
        pool = await self.db.get_card_pool(get('pack_id'))
        if not pool:
            return None
        pulled, _, _ = await self._pulled_cards(int(thread_data['thread_id']), get('pack_id'), pool)
        return estimate(pool, get('current_packs'), get('total_packs'), pulled, get('price_coins'))

    async def _pulled_cards(self, thread_id: int, pack_id: int, pool: dict) -> tuple:
        """(alle als gezogen bekannten Karten-Schlüssel, davon nur automatisch erkannte, Gewinner).

        Medaille Tn zählt für Platz n der Hit-Liste; Gewinner = Schlüssel -> Discord-User-ID.
        """
        medals = await self.db.get_medals(int(thread_id))
        detected = set((await self.db.get_pull_tracking(pack_id))["pulled"])
        keys = tier_keys(pool)
        winners = {keys[t]: user for t, user in medals.items() if t in keys}
        return detected | set(winners), detected - set(winners), winners

    async def _invalid_medal_reason(self, pack_id: Optional[int], tier: str) -> Optional[str]:
        """Fehlertext, wenn es die Medaille bei diesem Banner nicht gibt, sonst None."""
        pool = await self.db.get_card_pool(pack_id) if pack_id else None
        if pool and pool.get('version') == 2:
            price = _int((await self.db.get_banner(pack_id) or {}).get('price_coins')) or None
            listed = relevant_units(pool, price) if pool.get('hits') else tracked_units(pool)
            available = max(1, min(len(tier_keys(pool)), len(listed)))
        else:
            available = len(TIERS)
        if int(tier[1:]) > available:
            return (f"❌ Diesen Banner gibt es nur mit T1–T{available}. "
                    f"Die Nummer entspricht dem Platz in der Hit-Liste.")
        return None

    async def _claimed_tiers(self, thread_id: int, pack_id: int) -> dict:
        """T1-T3 als gezogen (Medaille oder automatisch erkannt) für die 🎯-Nachricht."""
        medals = await self.db.get_medals(int(thread_id))
        pool = await self.db.get_card_pool(pack_id)
        if not pool:
            return {t: t in medals for t in TIERS}
        detected = set((await self.db.get_pull_tracking(pack_id))["pulled"])
        keys = tier_keys(pool)
        return {t: t in medals or keys.get(t) in detected for t in TIERS}

    @staticmethod
    def _minimum_text(pool: Optional[dict], price) -> Optional[str]:
        """'300 Coins (22,5 % vom Preis) · 500× Karte' aus dem niedrigsten Kartenwert des Pools."""
        low = pool_minimum(pool) if pool and pool.get('total_count') else None
        if not low:
            return None
        text = f"{fmt_coins(low['value'])} Coins"
        if price:
            text += f" ({fmt_pct(low['value'] / _int(price) * 100)} % vom Preis)"
        if low.get('name'):
            text += f" · {fmt_coins(low['copies'])}× „{low['name']}“"
        elif low.get('copies'):
            text += f" · {fmt_coins(low['copies'])} Karten mit diesem Wert"
        return text

    async def _refresh_all_embeds_once(self):
        """Aktualisiert alle Startbeiträge einmal, wenn neue Felder dazugekommen sind."""
        try:
            await self._refresh_all_embeds()
        except Exception as e:
            logger.error(f"Einmalige Aktualisierung der Startbeiträge fehlgeschlagen: {e}")

    async def _refresh_all_embeds(self):
        if await self.db.get_meta('embed_version') == str(EMBED_VERSION):
            return
        rows = await self.db.get_active_banners()
        logger.info(f"Neue Felder im Startbeitrag: aktualisiere {len(rows)} Threads einmalig...")
        for pid, row in rows.items():
            if row.get('card_pool'):
                await self._refresh_pool_views(pid)
            else:
                await self._update_thread_embed(row)
        await self.db.set_meta('embed_version', str(EMBED_VERSION))
        logger.info("Startbeiträge aktualisiert")

    async def _check_endspurt(self, thread: discord.Thread, thread_data: dict, stats: dict, banner, silent: bool):
        """Einmaliger Alarm, wenn nur noch wenige Packs übrig und noch Hits drin sind."""
        if thread_data.get('endspurt_sent'):
            return
        get = lambda key: self._get_banner_value(banner, key)
        remaining, total = _int(get('current_packs')), _int(get('total_packs'))
        open_hits = [u for u in stats['open_units'] if u.get('hit', u['shipping_only'])] or (
            [] if stats['tracked_hits'] else stats['open_units'])
        if not total or remaining <= 0 or remaining > total * ENDSPURT_PERCENT / 100 or not open_hits:
            return
        await self.db.set_endspurt_sent(thread.id)
        if silent:
            return
        units = tracked_units(await self.db.get_card_pool(get('pack_id')))
        rank = {u['key']: i for i, u in enumerate(units, 1)}
        top = ", ".join(f"{self._rank_icon(rank[u['key']])} {u['name']} ({fmt_coins(u['value'])})"
                        for u in open_hits[:3])
        more = f" und {len(open_hits) - 3} weitere" if len(open_hits) > 3 else ""
        chance = chance_at_least_one(remaining, len(open_hits), min(10, remaining))
        mention = "@everyone " if MENTION_ON_PACK_UPDATE else ""
        await discord_rate_limiter.acquire("message_send")
        await thread.send(
            f"{mention}⚡ **Endspurt:** nur noch {fmt_coins(remaining)} von {fmt_coins(total)} Packs!\n"
            f"Noch drin: {top}{more}\n"
            f"Chance auf mindestens einen Hit bei {min(10, remaining)} Zügen: {fmt_pct(chance)} %"
        )
        logger.info(f"Endspurt-Alarm gepostet: Banner {get('pack_id')} ({remaining}/{total})")

    async def _sales_tempo(self, pack_id: int, remaining: int) -> Optional[str]:
        """'~150 Packs/Std. · ausverkauft in ca. 3 Std.' aus dem Pack-Verlauf der letzten Stunden."""
        now = datetime.now()
        sold, first = await self.db.get_sales_since(pack_id, now - timedelta(hours=SALES_WINDOW_HOURS))
        if not sold or not first or remaining <= 0:
            return None
        hours = max((now - first).total_seconds() / 3600, 0.25)
        rate = sold / hours
        eta = remaining / rate
        if eta < 1:
            eta_text = "in unter 1 Std."
        elif eta < 48:
            eta_text = f"in ca. {round(eta)} Std."
        else:
            eta_text = f"in ca. {round(eta / 24)} Tagen"
        return f"~{fmt_coins(rate)} Packs/Std. · ausverkauft {eta_text}"

    async def _check_value_alert(self, thread: discord.Thread, thread_data: dict, stats: dict, banner,
                                 silent: bool = False):
        """Einmaliger Hinweis, wenn die Ø Rückgabe über 100 % des Preises steigt."""
        pct = stats.get('ev_pct')
        if pct is None:
            return
        alert_sent = bool(thread_data.get('value_alert_sent'))
        if pct > 100 and not alert_sent and silent:
            await self.db.set_value_alert_sent(thread.id, True)
        elif pct > 100 and not alert_sent:
            price = self._get_banner_value(banner, 'price_coins')
            mention = "@everyone " if MENTION_ON_PACK_UPDATE else ""
            await discord_rate_limiter.acquire("message_send")
            await thread.send(
                f"{mention}💰 **Lohnt sich gerade:** Ø Rückgabe pro Zug ca. {fmt_coins(stats['ev'])} Coins "
                f"= {fmt_pct(pct)} % des Preises ({fmt_coins(price)} Coins)"
            )
            await self.db.set_value_alert_sent(thread.id, True)
            logger.info(f"Lohnt-sich-Hinweis gepostet: Thread {thread.id} ({pct:.1f} %)")
        elif pct < 97 and alert_sent:
            # Hysterese: erst deutlich unter 100 % zurücksetzen, damit es nicht hin und her springt
            await self.db.set_value_alert_sent(thread.id, False)

    async def _detect_pulled_hits(self, api_items: dict):
        """Erkennt gezogene Hits an den Zählern aus pack/list.

        Versand-Hits ("Versand nur") können nur verschickt werden: ein Anstieg von total_sendcount
        und total_sendprice wird exakt mit den Kartenwerten abgeglichen. Banner ohne Versand-Hits
        nutzen als Rückfall den Sprung von total_kangen + total_sendprice für T1-T3.
        """
        for pid, item in api_items.items():
            try:
                pool = await self.db.get_card_pool(pid)
                if not pool or pool.get('version') != 2 or not pool.get('total_count'):
                    continue
                thread_data = await self.db.get_thread_by_banner_id(pid)
                if not thread_data or thread_data.get('is_expired'):
                    continue

                state = await self.db.get_pull_tracking(pid)
                pulled, unsure = list(state["pulled"]), list(state["unsure"])
                ships = shipment_values(item) or (None, None)
                value = decided_value(item)
                match = {"certain": [], "groups": [], "maybe": []}
                first_look, reason = False, ""

                if pool.get('hits'):
                    count, ship_value = ships
                    prev_count, prev_value = state["ship_count"], state["ship_value"]
                    if count is None:
                        pass
                    elif prev_count is None:
                        # Erste Messung: bisherige Sendungen nur auswerten, soweit es eindeutig ist
                        match = match_shipped_hits(pool, count, ship_value, set(pulled))
                        first_look, reason = True, f"bisher {count} Karten / {ship_value:,} Coins verschickt"
                    elif count > prev_count and ship_value > prev_value:
                        match = match_shipped_hits(pool, count - prev_count, ship_value - prev_value, set(pulled))
                        reason = f"{count - prev_count} Karte(n) / {ship_value - prev_value:,} Coins verschickt"
                elif value is not None and state["decided_value"] is not None and value > state["decided_value"]:
                    match["certain"] = detect_jump_pulls(pool, value - state["decided_value"], set(pulled))
                    reason = f"Anstieg {value - state['decided_value']:,} Coins"

                # Bei wertgleichen Hits zählt für die Rechnung ein Stellvertreter als gezogen
                stand_ins = [g["keys"][i] for g in match["groups"] for i in range(g["pulled"])]
                await self.db.set_pull_tracking(pid, value, ships[0], ships[1],
                                                pulled + match["certain"] + stand_ins,
                                                unsure + match["groups"] + match["maybe"])
                if not (match["certain"] or match["groups"] or match["maybe"]):
                    continue

                logger.info(f"[HIT] {pid}: {reason} -> sicher {match['certain']}, "
                            f"wertgleich {match['groups']}, möglich {match['maybe']}")
                thread_id = int(thread_data['thread_id'])
                if not first_look:
                    medals = await self.db.get_medals(thread_id)
                    medal_keys = {k for t, k in tier_keys(pool).items() if t in medals}
                    price = _int((await self.db.get_banner(pid) or {}).get('price_coins')) or None
                    worth = {u["key"] for u in tracked_units(pool) if is_relevant_hit(u, price)}
                    certain = [k for k in match["certain"] if k not in medal_keys and k in worth]
                    groups = [g for g in match["groups"] if set(g["keys"]) & worth]
                    maybe = [g for g in match["maybe"] if set(g["keys"]) & worth]
                    if certain or groups or maybe:
                        await self._announce_detected_hits(thread_id, pool, certain, groups, maybe)
                await self._refresh_pool_views(pid)
                await self._update_probability_message(thread_id, pid)
            except Exception as e:
                logger.warning(f"[HIT] Fehler bei Banner {pid}: {e}")

    async def _announce_detected_hits(self, thread_id: int, pool: dict, certain: list,
                                      groups: list = (), maybe: list = ()):
        thread = self.get_channel(thread_id) or await self.fetch_channel(thread_id)
        if not isinstance(thread, discord.Thread):
            return
        if thread.archived:
            await discord_rate_limiter.acquire("thread_edit")
            await thread.edit(archived=False)
        units = tracked_units(pool)
        rank = {u["key"]: i for i, u in enumerate(units, 1)}
        label = {u["key"]: f"{self._rank_icon(rank[u['key']])} {u['name']}" for u in units}
        lines = []
        for key in certain:
            unit = units[rank[key] - 1]
            card_text = f"{label[key]} ({fmt_coins(unit['value'])} Coins)"
            if unit["shipping_only"]:
                lines.append(f"🔥 **Hit gezogen:** {card_text}")
            elif rank[key] == 1:
                lines.append(f"🔥 **T1 gezogen:** {card_text}")
            else:
                lines.append(f"🔥 **Großer Hit gezogen**, vermutlich T{rank[key]}: {card_text}")
        for group in groups:
            names = " oder ".join(label[k] for k in group["keys"])
            amount = "eine der Karten" if group["pulled"] == 1 else f"{group['pulled']} der Karten"
            lines.append(f"🔥 **Hit gezogen:** {amount} mit {fmt_coins(group['value'])} Coins ❓ ({names})")
        for group in maybe:
            names = " oder ".join(label[k] for k in group["keys"])
            lines.append(f"❓ **Möglicher Hit:** Eine Karte mit {fmt_coins(group['value'])} Coins wurde verschickt. "
                         f"Das kann {names} sein, aber auch eine normale Karte mit gleichem Wert.")
        key_tier = {k: t for t, k in tier_keys(pool).items()}
        asks = [f"**{key_tier[k]}**" for k in certain if k in key_tier]
        for group in list(groups) + list(maybe):
            tiers = [f"**{key_tier[k]}**" for k in group["keys"] if k in key_tier]
            if tiers:
                asks.append(" oder ".join(tiers))
        if asks:
            lines.append(f"Warst du's? Schreib {' bzw. '.join(asks)} hier rein und hol dir deine Medaille 🏅")
        if all(u["shipping_only"] for u in units):
            lines.append("*Automatisch erkannt: die Karte wurde gerade zum Versand angefordert.*")
        else:
            lines.append("*Automatisch erkannt: die Karte wurde gerade in Coins umgewandelt oder verschickt.*")
        mention = "@everyone " if MENTION_ON_PACK_UPDATE and (certain or groups) else ""
        await discord_rate_limiter.acquire("message_send")
        await thread.send(mention + "\n".join(lines))

    @staticmethod
    def _rank_icon(rank: int) -> str:
        return {1: "🥇", 2: "🥈", 3: "🥉"}.get(rank, f"{rank}.")

    @staticmethod
    def _card_status(key: str, pulled: set, detected: set, unsure: list, winners: dict) -> tuple:
        """(Text, Farbe) für eine Karte in der Hit-Liste; None-Text = noch drin."""
        if winners.get(key):
            return f"✅ gezogen von <@{winners[key]}>", 0x95A5A6
        for group in unsure:
            if key in group["keys"] and group["pulled"] > 0 and not set(group["keys"]) <= pulled:
                amount = "eine" if group["pulled"] == 1 else str(group["pulled"])
                return f"❓ {amount} von {len(group['keys'])} Karten mit diesem Wert gezogen", 0xE67E22
        if key in pulled:
            return "✅ gezogen" + (" (erkannt)" if key in detected else ""), 0x95A5A6
        for group in unsure:
            if key in group["keys"] and group["pulled"] == 0:
                return "❓ möglicherweise gezogen (gleicher Wert wie eine normale Karte)", 0xE67E22
        return None, 0xFFD700

    def _build_hit_messages(self, pool: dict, pulled: set, detected: set, unsure: list,
                            winners: Optional[dict] = None, price: Optional[int] = None) -> list:
        """[(Überschrift, Embeds), ...]: alle Versand-Hits ab Packpreis in Nachrichten zu je 10, sonst Top 5."""
        units = relevant_units(pool, price) if pool.get('hits') else tracked_units(pool)
        if pool.get('hits') and not units:
            units = tracked_units(pool)[:5]
        if pool.get('hits'):
            entries = units[:MAX_LISTED]
            open_count = sum(1 for u in units if u["key"] not in pulled)
            header = f"🏆 **Hits im Pool** (nur Versand) · noch drin: {open_count} von {len(units)}"
        else:
            tracked = {u["name"]: u["key"] for u in units}
            entries = [{**c, "key": tracked.get(c["name"])} for c in pool.get('top', [])]
            header = "🏆 **Top 5 Karten** (Coin-Wert)"
        embeds = []
        for rank, card in enumerate(entries, 1):
            status, color = self._card_status(card.get("key"), pulled, detected, unsure, winners or {})
            description = f"**{fmt_coins(card['value'])} Coins**" + (f" · {status}" if status else "")
            embed = discord.Embed(title=f"{self._rank_icon(rank)} {card['name']}"[:256],
                                  description=description, color=color)
            if card.get('image'):
                embed.set_thumbnail(url=card['image'])
            embeds.append(embed)
        chunks = [embeds[i:i + EMBEDS_PER_MESSAGE] for i in range(0, len(embeds), EMBEDS_PER_MESSAGE)]
        if len(chunks) <= 1:
            return [(header, chunks[0])] if chunks else []
        return [(f"{header} · Teil 1/{len(chunks)}" if i == 0 else f"🏆 **Hits im Pool** · Teil {i + 1}/{len(chunks)}",
                 chunk) for i, chunk in enumerate(chunks)]

    async def _refresh_pool_views(self, pack_id: int, initial_pool: bool = False, embed: bool = True):
        """Aktualisiert Startbeitrag (Ø Rückgabe, Hits) und die Hit-Nachricht(en) eines Banners.

        Die Hit-Nachrichten werden nur bearbeitet, wenn sich ihr Inhalt gegenüber dem zuletzt
        geposteten Stand geändert hat (Signatur in der DB).
        """
        try:
            banner = await self.db.get_banner(pack_id)
            thread_data = await self.db.get_thread_by_banner_id(pack_id)
            pool = await self.db.get_card_pool(pack_id)
            if not banner or not thread_data or not pool or thread_data.get('is_expired'):
                return

            if embed:
                await self._update_thread_embed(banner, initial_pool=initial_pool)

            thread_id = int(thread_data['thread_id'])
            thread = self.get_channel(thread_id)
            if not thread:
                thread = await self.fetch_channel(thread_id)
            if not isinstance(thread, discord.Thread):
                return

            pulled, detected, winners = await self._pulled_cards(thread_id, pack_id, pool)
            unsure = (await self.db.get_pull_tracking(pack_id))["unsure"]
            messages = self._build_hit_messages(pool, pulled, detected, unsure, winners,
                                                price=_int(banner.get('price_coins')) or None)
            if not messages:
                return

            old_ids = json.loads(thread_data.get('hit_message_ids') or 'null') or (
                [thread_data['top5_message_id']] if thread_data.get('top5_message_id') else [])
            sig = json.dumps([[c, [[e.title, e.description, e.color.value if e.color else None,
                                    e.thumbnail.url if e.thumbnail else None] for e in em]]
                              for c, em in messages], ensure_ascii=False)
            if old_ids and thread_data.get('hit_list_sig') == sig:
                return
            new_ids = []
            for i, (content, embeds) in enumerate(messages):
                msg = None
                if i < len(old_ids):
                    try:
                        msg = await thread.fetch_message(int(old_ids[i]))
                        await discord_rate_limiter.acquire("message_edit")
                        await msg.edit(content=content, embeds=embeds)
                    except discord.NotFound:
                        msg = None
                if msg is None:
                    await discord_rate_limiter.acquire("message_send")
                    msg = await thread.send(content=content, embeds=embeds)
                    logger.info(f"Hit-Nachricht {i + 1}/{len(messages)} gepostet: Banner {pack_id}")
                new_ids.append(msg.id)
            for extra_id in old_ids[len(messages):]:
                try:
                    old = await thread.fetch_message(int(extra_id))
                    await discord_rate_limiter.acquire("message_delete")
                    await old.delete()
                except discord.NotFound:
                    pass
            if new_ids != old_ids:
                await self.db.set_hit_message_ids(thread_id, new_ids)
            await self.db.set_hit_list_sig(thread_id, sig)
            if old_ids:
                logger.info(f"Hit-Liste aktualisiert: Banner {pack_id} ({len(messages)} Nachricht(en))")
        except Exception as e:
            logger.warning(f"Fehler bei Hit-Nachricht/Ø-Update für {pack_id}: {e}")

    async def _get_medals_from_reactions(self, thread, starter_message_id: int) -> list:
        """Liest Medaillen von Discord-Reaktionen auf der Starter-Message."""
        medals = []
        try:
            if not starter_message_id:
                return medals

            starter_msg = await thread.fetch_message(int(starter_message_id))
            for reaction in starter_msg.reactions:
                tier = EMOJI_TO_MEDAL.get(str(reaction.emoji))
                if tier:
                    medals.append(tier)
        except Exception as e:
            logger.debug(f"Fehler beim Lesen der Reaktionen: {e}")
        return medals

    async def _find_existing_probability_message(self, thread: discord.Thread) -> Optional[discord.Message]:
        """
        Sucht im Thread nach einer existierenden Wahrscheinlichkeits-Nachricht.
        Gibt die Nachricht zurück, falls gefunden, sonst None.
        """
        try:
            # Die letzten 50 Nachrichten durchsuchen (sollte ausreichen)
            async for message in thread.history(limit=50):
                # Nur Bot-Nachrichten prüfen
                if message.author.id != self.user.id:
                    continue
                # Prüfen ob es eine Probability-Nachricht ist (beginnt mit dem Hit-Chance Emoji)
                if message.content and message.content.startswith("🎯 **Hit-Chance:**"):
                    logger.debug(f"Existierende Probability-Nachricht gefunden in Thread {thread.id}: {message.id}")
                    return message
        except Exception as e:
            logger.debug(f"Fehler beim Suchen der Probability-Nachricht: {e}")
        return None

    async def _update_probability_message(self, thread_id: int, banner_id: int):
        """Erstellt oder aktualisiert die Wahrscheinlichkeits-Nachricht im Thread."""
        try:
            # Banner-Daten holen
            banner = await self.db.get_banner(banner_id)
            if not banner:
                return

            current_packs = banner.get('current_packs', 0)
            if not current_packs or current_packs <= 0:
                return

            # Pulls pro Tag (entries_per_day), None = unbegrenzt
            pulls_per_day = banner.get('entries_per_day')

            # Thread-Daten für starter_message_id holen
            thread_data = await self.db.get_thread_by_banner_id(banner_id)
            starter_message_id = thread_data.get('starter_message_id') if thread_data else None

            thread_id_int = int(thread_id)
            full_message = await self._hit_chance_text(banner, thread_data, thread_id_int)

            # Thread holen (falls nicht schon im Fallback geholt)
            thread = self.get_channel(thread_id_int)
            if not thread:
                try:
                    thread = await self.fetch_channel(thread_id_int)
                except (discord.NotFound, Exception):
                    return

            if not isinstance(thread, discord.Thread):
                return

            # Prüfe ob bereits eine Probability-Message existiert (in der Datenbank)
            existing_msg_id = await self.db.get_probability_message_id(thread_id)

            if existing_msg_id:
                # Versuche bestehende Nachricht zu editieren
                try:
                    existing_msg = await thread.fetch_message(int(existing_msg_id))
                    await discord_rate_limiter.acquire("message_edit")
                    await existing_msg.edit(content=full_message)
                    logger.debug(f"Probability-Message aktualisiert in Thread {thread_id}")
                    return
                except discord.NotFound:
                    # Message wurde gelöscht, im Thread suchen
                    logger.debug(f"Probability-Message {existing_msg_id} nicht mehr vorhanden, suche im Thread...")
                except Exception as e:
                    logger.debug(f"Fehler beim Editieren der Probability-Message: {e}")

            # Fallback: Im Thread nach existierender Probability-Nachricht suchen
            # (z.B. nach Bot-Neustart wenn Message-ID nicht in DB war)
            existing_msg = await self._find_existing_probability_message(thread)
            if existing_msg:
                try:
                    await discord_rate_limiter.acquire("message_edit")
                    await existing_msg.edit(content=full_message)
                    # Message-ID in DB speichern für zukünftige Updates
                    await self.db.update_probability_message_id(thread_id, existing_msg.id)
                    logger.info(f"Existierende Probability-Message gefunden und aktualisiert in Thread {thread_id}")
                    return
                except Exception as e:
                    logger.debug(f"Fehler beim Aktualisieren der gefundenen Probability-Message: {e}")

            # Keine existierende Nachricht gefunden - neue erstellen
            await discord_rate_limiter.acquire("message_send")
            new_msg = await thread.send(full_message)
            await self.db.update_probability_message_id(thread_id, new_msg.id)
            logger.debug(f"Neue Probability-Message erstellt in Thread {thread_id}")

        except Exception as e:
            logger.debug(f"Fehler bei Probability-Update: {e}")

    async def _delete_banner_thread(self, pack_id: int) -> bool:
        """Löscht den Discord-Thread für einen abgelaufenen Banner."""
        try:
            logger.info(f"   Archiviere Thread für Banner {pack_id}...")

            thread_data = await self.db.get_thread_by_banner_id(pack_id)
            if not thread_data:
                logger.warning(f"   Kein Thread in DB für Banner {pack_id}")
                # Banner als inaktiv markieren
                await self.db.mark_banner_inactive(pack_id)
                return False

            thread_id = thread_data.get('thread_id')
            logger.info(f"   Thread-ID für {pack_id}: {thread_id}")

            if not thread_id:
                logger.warning(f"   Keine thread_id in Daten für {pack_id}")
                return False

            # Thread aus Discord holen
            thread = self.get_channel(int(thread_id))
            logger.debug(f"   Thread aus Cache: {thread}")

            # Falls nicht im Cache, von API holen
            if not thread:
                try:
                    logger.debug(f"   Hole Thread {thread_id} von API...")
                    thread = await self.fetch_channel(int(thread_id))
                except discord.NotFound:
                    logger.info(f"   Thread {thread_id} existiert nicht mehr in Discord")
                    thread = None
                except Exception as e:
                    logger.warning(f"   Fehler beim Fetchen von Thread {thread_id}: {e}")
                    thread = None

            if thread and isinstance(thread, discord.Thread):
                # Thread komplett aus Discord löschen
                logger.info(f"   Lösche Discord-Thread {thread_id}...")
                try:
                    await discord_rate_limiter.acquire("thread_delete")
                    await thread.delete()
                    logger.info(f"   Discord-Thread {thread_id} gelöscht!")
                except discord.NotFound:
                    logger.info(f"   Thread {thread_id} existiert bereits nicht mehr")
                except Exception as e:
                    logger.warning(f"   Fehler beim Löschen von Thread {thread_id}: {e}")
            else:
                logger.info(f"   Kein gültiger Thread zum Archivieren gefunden")

            # In DB als inaktiv/expired markieren (nicht löschen!)
            logger.debug(f"   Markiere als inaktiv in DB...")
            await self.db.mark_banner_inactive(pack_id)
            await self.db.mark_thread_expired(pack_id)
            logger.info(f"   Banner {pack_id} als inaktiv markiert")

            return True

        except discord.NotFound:
            # Thread existiert nicht mehr
            logger.debug(f"Thread für {pack_id} nicht gefunden - markiere als inaktiv")
            await self.db.mark_banner_inactive(pack_id)
            await self.db.mark_thread_expired(pack_id)
            return True
        except discord.HTTPException as e:
            logger.error(f"Discord-Fehler beim Thread löschen: {e}")
            return False
        except Exception as e:
            logger.error(f"Fehler beim Thread löschen für {pack_id}: {e}")
            return False

    async def _purge_archived_data(self):
        """Löscht archivierte Banner-Daten und deren Discord-Threads."""
        try:
            # Zuerst archivierte Discord-Threads löschen
            thread_ids = await self.db.get_archived_thread_ids(max_age_hours=1)
            deleted_threads = 0
            for tid in thread_ids:
                try:
                    thread = self.get_channel(int(tid))
                    if not thread:
                        try:
                            thread = await self.fetch_channel(int(tid))
                        except discord.NotFound:
                            thread = None
                        except Exception:
                            thread = None
                    if thread and isinstance(thread, discord.Thread):
                        await discord_rate_limiter.acquire("thread_delete")
                        await thread.delete()
                        deleted_threads += 1
                except Exception as e:
                    logger.debug(f"Konnte archivierten Thread {tid} nicht löschen: {e}")

            if deleted_threads > 0:
                logger.info(f"Archiv-Bereinigung: {deleted_threads} Discord-Threads gelöscht")

            # Dann DB-Einträge löschen
            purged = await self.db.purge_archived_data(max_age_hours=1)
            if purged > 0:
                logger.info(f"Archiv-Bereinigung: {purged} alte Banner aus DB gelöscht")
        except Exception as e:
            logger.error(f"Fehler bei Archiv-Bereinigung: {e}")

    async def on_message(self, message: discord.Message):
        """Listener fuer T1/T2/T3 Reaktionen."""
        # Erst Commands verarbeiten
        await self.process_commands(message)

        if message.author.bot:
            return

        # Pruefe ob in einem unserer Threads
        if not isinstance(message.channel, discord.Thread):
            return

        # Suche nach T1, T2 oder T3 im Text (case insensitive)
        # Matcht: "T1", "t1 + 4b", "t1+4b", "T2 test", etc.
        content = message.content.strip().upper()
        tier_match = re.search(r'\b(T(?:10|[1-9]))\b', content)
        if not tier_match:
            return

        tier = tier_match.group(1)  # "T1" bis "T10"
        logger.debug(f"T-Nachricht erkannt: {tier} von {message.author.name} in Thread {message.channel.id}")

        try:
            user_id = message.author.id
            thread_id = message.channel.id
            emoji = MEDAL_EMOJIS[tier]

            # Prüfe ob Thread im Hot-Banner Channel ist
            is_hot_banner = (message.channel.parent_id == HOT_BANNER_CHANNEL_ID)

            if is_hot_banner:
                # Hot-Banner Thread: Extrahiere Pack-ID aus Thread-Titel
                # Format: "#1 | 25.3% | ID: 15393 | 5 Pulls"
                id_match = re.search(r'ID:\s*(\d+)', message.channel.name)
                if not id_match:
                    await message.reply("❌ Konnte Pack-ID nicht aus Thread-Titel extrahieren!")
                    return

                pack_id = int(id_match.group(1))

                # Original-Thread finden
                original_thread_data = await self.db.get_thread_by_banner_id(pack_id)
                if not original_thread_data:
                    await message.reply("❌ Original-Thread nicht gefunden!")
                    return

                original_thread_id = original_thread_data.get('thread_id')
                problem = await self._invalid_medal_reason(pack_id, tier)
                if problem:
                    await message.reply(problem)
                    return

                # Prüfe ob Medaille schon vergeben (im Original-Thread)
                existing = await self.db.get_medal(original_thread_id, tier)
                if existing:
                    await message.reply(f"❌ {tier} wurde bereits von <@{existing['user_id']}> beansprucht!")
                    return

                # Medaille im Original-Thread speichern
                await self.db.save_medal(original_thread_id, tier, user_id)

                # Reaktion auf Hot-Banner Thread
                await message.add_reaction(emoji)

                # Auch auf Original-Thread Reaktion setzen
                try:
                    original_thread = self.get_channel(int(original_thread_id))
                    if not original_thread:
                        original_thread = await self.fetch_channel(int(original_thread_id))

                    starter_msg_id = original_thread_data.get('starter_message_id')
                    if starter_msg_id and original_thread:
                        starter_msg = await original_thread.fetch_message(int(starter_msg_id))
                        await starter_msg.add_reaction(emoji)
                except Exception as e:
                    logger.debug(f"Konnte Original-Thread nicht updaten: {e}")

                await message.reply(f"{emoji} {tier} geht an {message.author.mention}!\n*(Auch im Original-Thread gesetzt)*")

                logger.info(f"Medaille (Hot-Banner): {tier} an {message.author.name} für Pack {pack_id}")

                # Wahrscheinlichkeit im Original-Thread aktualisieren
                await self._update_probability_message(original_thread_id, pack_id)
                await self._refresh_pool_views(pack_id)

            else:
                # Normaler Thread
                thread_data = await self.db.get_thread_by_id(thread_id)
                if not thread_data:
                    logger.debug(f"Thread {thread_id} nicht in DB gefunden")
                    return

                problem = await self._invalid_medal_reason(thread_data.get('banner_id'), tier)
                if problem:
                    await message.reply(problem)
                    return

                # Pruefe ob Medaille schon vergeben
                existing = await self.db.get_medal(thread_id, tier)
                if existing:
                    await message.reply(f"❌ {tier} wurde bereits von <@{existing['user_id']}> beansprucht!")
                    return

                # Medaille vergeben
                await self.db.save_medal(thread_id, tier, user_id)

                # Hole die Starter-Message (erste Nachricht im Thread)
                starter_message_id = thread_data.get('starter_message_id')
                if starter_message_id:
                    try:
                        starter_message = await message.channel.fetch_message(int(starter_message_id))
                        await starter_message.add_reaction(emoji)
                    except Exception as e:
                        logger.debug(f"Konnte Starter-Message nicht finden: {e}")
                        await message.add_reaction(emoji)
                else:
                    await message.add_reaction(emoji)

                await message.reply(f"{emoji} {tier} geht an {message.author.mention}!")

                logger.info(f"Medaille: {tier} an {message.author.name} in {message.channel.name}")

                # Wahrscheinlichkeit aktualisieren
                banner_id = thread_data.get('banner_id')
                if banner_id:
                    await self._update_probability_message(thread_id, banner_id)
                    await self._refresh_pool_views(banner_id)

        except Exception as e:
            logger.error(f"Fehler bei Medaillen-Vergabe: {e}")
            await message.reply(f"❌ Fehler: {e}")

    def _calculate_banner_probability(self, banner: dict) -> float:
        """Berechnet die Hit-Wahrscheinlichkeit für ein Banner für das Ranking."""
        current_packs = banner.get('current_packs', 0)
        if not current_packs or current_packs <= 0:
            return 0.0

        pulls_per_day = banner.get('entries_per_day')
        medal_count = banner.get('medal_count', 0) or 0
        hits_remaining = 3 - medal_count

        if hits_remaining <= 0:
            return 0.0

        if pulls_per_day is None or pulls_per_day <= 0:
            # Unbegrenzte Pulls - einfache Wahrscheinlichkeit pro Pull
            return (hits_remaining / current_packs) * 100
        else:
            # Hypergeometrische Verteilung
            N = current_packs
            n = hits_remaining
            k = min(pulls_per_day, N)

            if k > N - n:
                return 100.0
            else:
                p_zero = comb(N - n, k) / comb(N, k)
                return (1 - p_zero) * 100

    async def _cleanup_hot_banner_threads(self, channel: discord.ForumChannel):
        """Löscht alle Threads im Hot-Banner Channel."""
        try:
            deleted_count = 0
            # Alle Threads im Channel holen (archived und active)
            threads_to_delete = []

            # Aktive Threads
            for thread in channel.threads:
                threads_to_delete.append(thread)

            # Archivierte Threads
            async for thread in channel.archived_threads(limit=100):
                threads_to_delete.append(thread)

            # Threads löschen
            for thread in threads_to_delete:
                try:
                    await discord_rate_limiter.acquire("thread_delete")
                    await thread.delete()
                    deleted_count += 1
                except Exception as e:
                    logger.debug(f"Konnte Hot-Banner Thread nicht löschen: {e}")

            if deleted_count > 0:
                logger.info(f"Hot-Banner Cleanup: {deleted_count} alte Threads gelöscht")

        except Exception as e:
            logger.error(f"Fehler bei Hot-Banner Cleanup: {e}")

    async def _update_hot_banners(self):
        """Postet die Top 10 Banner mit höchster Hit-Chance in den Hot-Banner Channel."""
        try:
            if not HOT_BANNER_CHANNEL_ID or not HOT_BANNER_ENABLED:
                return

            logger.info("Hot-Banner Update gestartet...")

            # Channel holen
            channel = self.get_channel(HOT_BANNER_CHANNEL_ID)
            if not channel:
                try:
                    channel = await self.fetch_channel(HOT_BANNER_CHANNEL_ID)
                except Exception as e:
                    logger.error(f"Hot-Banner Channel nicht gefunden: {e}")
                    return

            if not isinstance(channel, discord.ForumChannel):
                logger.error(f"Hot-Banner Channel ist kein Forum-Channel!")
                return

            # Alte Hot-Banner Threads löschen
            await self._cleanup_hot_banner_threads(channel)

            # Alle aktiven Banner mit Medaillen-Count holen
            banners = await self.db.get_all_active_banners_with_threads()

            # Filter: Nur Nicht-Bonus und nicht alle Hits gezogen
            filtered_banners = []
            for b in banners:
                # Bonus exkludieren
                if b.get('category') == 'Bonus':
                    continue
                # Banners ohne Packs exkludieren
                if not b.get('current_packs') or b.get('current_packs') <= 0:
                    continue
                # Banners mit allen Hits gezogen exkludieren
                medal_count = b.get('medal_count', 0) or 0
                if medal_count >= 3:
                    continue
                filtered_banners.append(b)

            # Wahrscheinlichkeit berechnen und sortieren
            for b in filtered_banners:
                b['probability'] = self._calculate_banner_probability(b)

            # Nach Wahrscheinlichkeit sortieren (höchste zuerst)
            sorted_banners = sorted(filtered_banners, key=lambda x: x['probability'], reverse=True)[:10]

            if not sorted_banners:
                logger.info("Keine Banner für Hot-Banner gefunden")
                return

            # Für jeden Hot-Banner einen Thread erstellen/aktualisieren
            # Rate-Limiting wird bereits durch Discord.py bzw. rate_limiter gehandhabt
            for rank, banner in enumerate(sorted_banners, 1):
                await self._post_hot_banner(channel, banner, rank)

            logger.info(f"Hot-Banner Update abgeschlossen: {len(sorted_banners)} Banner")

        except Exception as e:
            logger.error(f"Fehler bei Hot-Banner Update: {e}")

    async def _post_hot_banner(self, channel: discord.ForumChannel, banner: dict, rank: int):
        """Postet einen einzelnen Hot-Banner als Thread (identisches Format wie normale Banner + Hit-Chance)."""
        try:
            pack_id = banner.get('pack_id')
            probability = banner.get('probability', 0)
            medal_count = banner.get('medal_count', 0) or 0
            hits_remaining = 3 - medal_count

            # DEBUG: Prüfen ob image_url und detail_page_url vorhanden sind
            logger.debug(f"Hot-Banner {pack_id} - image_url: {banner.get('image_url')}")
            logger.debug(f"Hot-Banner {pack_id} - detail_page_url: {banner.get('detail_page_url')}")

            # Thread-Titel: IDENTISCH wie normale Banner
            price = banner.get('price_coins') or 0
            entries = banner.get('entries_per_day') if banner.get('entries_per_day') else "unbegrenzt"
            total = banner.get('total_packs') or 0
            title = f"ID: {pack_id} / Kosten: {price} Coins / Anzahl Pulls: {entries} / Pulls Gesamt: {total}"
            if len(title) > 100:
                title = title[:97] + "..."

            # Embed erstellen: IDENTISCH wie normale Banner
            embed = self._build_banner_embed(banner)

            # DEBUG: Prüfen ob Embed korrekt erstellt wurde
            logger.debug(f"Hot-Banner {pack_id} - Embed URL: {embed.url}")
            logger.debug(f"Hot-Banner {pack_id} - Embed Image: {embed.image.url if embed.image else 'NONE'}")

            # NUR die Hit-Chance als zusätzliches Feld am Anfang einfügen
            original_fields = embed.fields.copy()
            embed.clear_fields()

            # Rang und Hit-Chance als erstes Feld
            embed.add_field(
                name=f"🔥 #{rank} | 🎯 Hit-Chance",
                value=f"**{probability:.2f}%** ({hits_remaining}/3 Hits)",
                inline=False
            )

            # Dann alle Original-Felder
            for field in original_fields:
                embed.add_field(name=field.name, value=field.value, inline=field.inline)

            # Thread erstellen
            await discord_rate_limiter.acquire("thread_create")
            thread, message = await channel.create_thread(
                name=title,
                embed=embed,
                reason=f"Hot Banner #{rank}: {pack_id}"
            )

            logger.debug(f"Hot-Banner Thread erstellt: #{rank} - {pack_id}")

        except Exception as e:
            logger.error(f"Fehler beim Posten von Hot-Banner {banner.get('pack_id')}: {e}")

    # Slash Commands als Methoden
    async def refresh_command(self, interaction: discord.Interaction):
        """Manuelles Scraping starten."""
        await interaction.response.defer()
        await self._scrape_with_timeout()
        await interaction.followup.send("Scrape abgeschlossen!")

    async def status_command(self, interaction: discord.Interaction):
        """Bot-Status anzeigen."""
        stats = await self.db.get_stats()

        embed = discord.Embed(
            title="GTCHA Bot Status",
            color=discord.Color.green()
        )

        embed.add_field(name="Banner gesamt", value=str(stats.get('total_banners', 0)), inline=True)
        embed.add_field(name="Aktive Threads", value=str(stats.get('active_threads', 0)), inline=True)
        embed.add_field(name="Medaillen", value=str(stats.get('total_medals', 0)), inline=True)

        await interaction.response.send_message(embed=embed)

    async def hotbanner_command(self, interaction: discord.Interaction):
        """Hot-Banner manuell aktualisieren."""
        if not HOT_BANNER_CHANNEL_ID or not HOT_BANNER_ENABLED:
            await interaction.response.send_message("❌ Hot-Banner nicht aktiviert oder kein Channel konfiguriert!")
            return

        await interaction.response.defer()
        await self._update_hot_banners()
        await interaction.followup.send("🔥 Hot-Banner aktualisiert!")

    async def _daily_restart(self):
        """Beendet den Bot-Prozess für einen automatischen Neustart (Railway)."""
        logger.warning("Täglicher Auto-Restart wird ausgeführt...")
        try:
            from utils.notifications import notify_critical_error
            await notify_critical_error("Geplanter täglicher Neustart wird durchgeführt.")
        except Exception:
            pass
        await asyncio.sleep(2)
        logger.warning("Prozess wird mit Exit-Code 1 beendet - Railway startet automatisch neu.")
        os._exit(1)
