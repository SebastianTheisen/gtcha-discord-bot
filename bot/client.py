"""
Discord Bot Client - Forum-Channel Version
"""

import asyncio
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

from config import (
    GUILD_ID, SCRAPE_INTERVAL_MINUTES, BASE_URL,
    CHANNEL_IDS, CATEGORIES, SCRAPE_TIMEOUT_SECONDS,
    MENTION_ON_NEW_THREAD, MENTION_ON_PACK_UPDATE,
    HOT_BANNER_CHANNEL_ID
)
from scraper.gtcha_scraper import GTCHAScraper
from database.db import Database
from utils.notifications import (
    set_bot_client, notify_scrape_error, notify_low_banner_count,
    notify_all_retries_failed, notify_critical_error,
    notify_scrape_success, notify_bot_started
)
from utils.rate_limiter import discord_rate_limiter
from utils.memory_monitor import memory_monitor
from utils.cache import banner_cache


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
        # Läuft alle 5 Minuten um xx:00:20, xx:05:20, xx:10:20, etc.
        # (20 Sekunden nach der vollen Minute, da neue Banner um :00 und :30 kommen)
        self.scheduler.add_job(
            self._scrape_with_timeout,
            'cron',
            minute='*/5',  # Alle 5 Minuten
            second=20,     # 20 Sekunden nach der Minute
            id='scrape_job',
            replace_existing=True,
            coalesce=True,  # Verpasste Jobs zusammenfassen
            max_instances=1,  # Maximal eine Instanz gleichzeitig
            misfire_grace_time=300,  # Job kann bis zu 5 Min verspätet starten
        )
        self.scheduler.start()
        logger.info("Scheduler: Alle 5 Min um xx:xx:20")

        # Hot-Banner Job (alle 30 Min um xx:00:20 und xx:30:20)
        if HOT_BANNER_CHANNEL_ID:
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

        # Threads aus Discord wiederherstellen (falls DB leer nach Neustart)
        await self._recover_threads_from_discord()

        # Medaillen von Discord-Reaktionen synchronisieren
        await self._sync_medals_from_discord()

        # Erster Scrape nach 10 Sekunden - über Scheduler triggern statt direkt aufrufen
        # Das vermeidet Konflikte mit dem regulären Scheduler-Job
        await asyncio.sleep(10)
        self.scheduler.modify_job('scrape_job', next_run_time=datetime.now())

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

        # Auch archivierte Threads prüfen
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
                    async for thread in channel.archived_threads(limit=100):
                        try:
                            match = re.match(r'ID:\s*(\d+)', thread.name)
                            if not match:
                                continue

                            pack_id = int(match.group(1))

                            existing_thread = await self.db.get_thread_by_banner_id(pack_id)
                            if existing_thread:
                                continue

                            starter_message_id = None
                            try:
                                if thread.starter_message:
                                    starter_message_id = thread.starter_message.id
                                else:
                                    async for msg in thread.history(limit=1, oldest_first=True):
                                        starter_message_id = msg.id
                                        break
                            except Exception:
                                pass

                            await self.db.save_thread(
                                banner_id=pack_id,
                                thread_id=thread.id,
                                channel_id=channel.id,
                                starter_message_id=starter_message_id or 0
                            )

                            price_match = re.search(r'Kosten:\s*(\d+)', thread.name)
                            entries_match = re.search(r'Anzahl:\s*(\d+)', thread.name)
                            total_match = re.search(r'Gesamt:\s*(\d+)', thread.name)

                            banner = RecoveredBanner(
                                pack_id=pack_id,
                                category=category,
                                price_coins=int(price_match.group(1)) if price_match else None,
                                entries_per_day=int(entries_match.group(1)) if entries_match else None,
                                total_packs=int(total_match.group(1)) if total_match else None,
                                current_packs=None,  # Unbekannt bei Wiederherstellung
                            )

                            await self.db.save_banner(banner)
                            recovered_count += 1
                            logger.info(f"Archivierter Thread wiederhergestellt: {pack_id}")

                        except Exception as e:
                            logger.debug(f"Fehler bei archiviertem Thread: {e}")
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

                    if reaction_medals:
                        # Prüfen welche Medaillen noch nicht in DB gesetzt sind
                        for tier in reaction_medals:
                            col_map = {'T1': 't1_claimed', 'T2': 't2_claimed', 'T3': 't3_claimed'}
                            col = col_map.get(tier)
                            if col and not thread_row[col]:
                                # Medaille ist auf Discord aber nicht in DB - synchronisieren
                                async with aiosqlite.connect(self.db.db_path) as db:
                                    await db.execute(
                                        f"UPDATE discord_threads SET {col} = 1 WHERE thread_id = ?",
                                        (thread_id,)
                                    )
                                    await db.commit()
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

    async def scrape_and_post(self):
        """Hauptfunktion: Scrapen und neue Banner posten."""
        logger.info("Scrape startet...")
        start_time = datetime.now()

        try:
            async with GTCHAScraper(BASE_URL) as scraper:
                self._scraper = scraper
                banners = await scraper.scrape_all_banners()

                if not banners:
                    logger.warning("Keine Banner gefunden!")
                    return

                # Verarbeite Banner
                new_count = 0
                skipped_empty = 0
                deleted_count = 0
                skipped_inactive = 0
                for banner in banners:
                    try:
                        # Pruefe ob Banner neu ist
                        existing = await self.db.get_banner(banner.pack_id)

                        # Bereits inaktive Banner komplett überspringen
                        if existing and existing.get('is_active') == 0:
                            skipped_inactive += 1
                            continue

                        # Banner mit 0 Packs: Thread archivieren falls vorhanden
                        if banner.current_packs is not None and banner.current_packs == 0:
                            if existing and existing.get('is_active') == 1:
                                logger.info(f"Banner {banner.pack_id} hat 0 Packs - archiviere Thread")
                                deleted = await self._delete_banner_thread(banner.pack_id)
                                if deleted:
                                    deleted_count += 1
                                    logger.info(f"   Banner {banner.pack_id} Thread archiviert!")
                            skipped_empty += 1
                            continue

                        if not existing:
                            # Neuer Banner - Best Hit erstmal NICHT laden (zu langsam)
                            # TODO: Best Hit optional oder async laden
                            # if not banner.best_hit:
                            #     try:
                            #         best_hit, _ = await scraper.scrape_banner_details(banner.pack_id)
                            #         if best_hit:
                            #             banner.best_hit = best_hit
                            #             logger.debug(f"Best Hit fuer {banner.pack_id}: {best_hit}")
                            #     except Exception as e:
                            #         logger.debug(f"Best Hit Fehler: {e}")

                            # In DB speichern
                            await self.db.save_banner(banner)

                            # In Discord posten
                            await self._post_banner_to_discord(banner)
                            new_count += 1

                            logger.info(f"Neu: {banner.pack_id} ({banner.category})")
                        else:
                            # Existierender Banner - auf Updates pruefen
                            old_packs = existing.get('current_packs')
                            old_entries = existing.get('entries_per_day')
                            title_updated = False

                            # Prüfe ob entries_per_day sich geändert hat (Titel-Update nötig)
                            # Auch updaten wenn neuer Wert None (unbegrenzt) ist!
                            if banner.entries_per_day != old_entries:
                                await self.db.update_banner_entries(
                                    banner.pack_id,
                                    banner.entries_per_day
                                )
                                # Thread-Titel aktualisieren
                                await self._update_thread_title(banner)
                                title_updated = True
                                new_entries_str = banner.entries_per_day if banner.entries_per_day else "unbegrenzt"
                                old_entries_str = old_entries if old_entries else "unbegrenzt"
                                logger.info(f"Update: {banner.pack_id} Entries: {old_entries_str} -> {new_entries_str}")

                            if banner.current_packs != old_packs:
                                await self.db.update_banner_packs(
                                    banner.pack_id,
                                    banner.current_packs
                                )
                                # Kommentar im Thread posten - NUR wenn alter Wert bekannt war
                                # (Bei Wiederherstellung ist old_packs=None, dann kein Update posten)
                                if old_packs is not None:
                                    await self._post_pack_update_to_thread(
                                        banner.pack_id,
                                        old_packs,
                                        banner.current_packs,
                                        banner.total_packs
                                    )
                                    logger.info(f"Update: {banner.pack_id} Packs: {old_packs} -> {banner.current_packs}")
                                else:
                                    logger.debug(f"Initiales Pack-Update für {banner.pack_id}: {banner.current_packs} (kein Post)")

                            # Embed IMMER aktualisieren (für Countdown-Refresh und Pack-Anzeige)
                            await self._update_thread_embed(banner)

                            # Wahrscheinlichkeit aktualisieren
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
                        logger.error(f"Fehler bei Banner {banner.pack_id}: {e}")

                # === NICHT-GEFUNDEN-TRACKING ===
                # Sammle alle gefundenen Banner-IDs (inkl. der mit 0 Packs)
                found_banner_ids = {b.pack_id for b in banners}

                # Hole alle bekannten Banner aus der DB
                db_banner_ids = set(await self.db.get_all_active_banner_ids())

                # SCHUTZ: Nur tracken wenn mindestens 60 Banner gefunden wurden
                # Verhindert Massen-Löschung bei fehlgeschlagenem Scrape
                # (Website hat normalerweise 50-100 Banner)
                MIN_BANNERS_FOR_TRACKING = 60
                expired_count = 0

                if len(found_banner_ids) < MIN_BANNERS_FOR_TRACKING:
                    logger.warning(f"⚠️ Nur {len(found_banner_ids)} Banner gefunden - Not-Found-Tracking übersprungen!")
                    logger.warning("   Mögliche Ursache: Website-Problem oder Scrape-Fehler")
                    # Webhook-Benachrichtigung
                    await notify_low_banner_count(len(found_banner_ids), MIN_BANNERS_FOR_TRACKING)
                else:
                    # Für gefundene Banner: Zähler zurücksetzen
                    for pack_id in found_banner_ids:
                        if pack_id in db_banner_ids:
                            await self.db.reset_not_found_count(pack_id)

                    # Für NICHT gefundene Banner: Zähler erhöhen
                    not_found_ids = db_banner_ids - found_banner_ids
                    for pack_id in not_found_ids:
                        count = await self.db.increment_not_found_count(pack_id)
                        logger.debug(f"Banner {pack_id} nicht gefunden (Zähler: {count})")

                        # Bei 20x nicht gefunden: Banner löschen
                        if count >= 20:
                            logger.info(f"Banner {pack_id} 20x nicht gefunden - lösche Thread")
                            deleted = await self._delete_banner_thread(pack_id)
                            if deleted:
                                expired_count += 1
                                logger.info(f"   Banner {pack_id} (abgelaufen) Thread gelöscht!")

                elapsed = (datetime.now() - start_time).total_seconds()
                if skipped_inactive > 0:
                    logger.debug(f"Übersprungen: {skipped_inactive} inaktive Banner")
                logger.info(f"Scrape done: {elapsed:.1f}s, {new_count} neu, {deleted_count} archiviert, {expired_count} abgelaufen")

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
        finally:
            self._scraper = None

    async def _scrape_with_timeout(self):
        """Wrapper für scrape_and_post mit konfigurierbarem Timeout und Retry-Logik."""
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

    def _build_banner_embed(self, banner) -> discord.Embed:
        """Erstellt ein Embed für einen Banner (wird für neue und Updates verwendet)."""
        # Kategorie-Farben
        category_colors = {
            "Bonus": 0xFFD700,      # Gold
            "MIX": 0x9B59B6,        # Lila
            "Yu-Gi-Oh!": 0x8B4513,  # Braun
            "Pokémon": 0xFFCC00,    # Pokémon-Gelb
            "Weiss Schwarz": 0x2C3E50,  # Dunkelblau
            "One piece": 0xE74C3C,  # Rot
            "Hobby": 0x27AE60,      # Grün
        }
        embed_color = category_colors.get(banner.category, 0xFFD700)

        embed = discord.Embed(
            title=banner.title or f"Pack {banner.pack_id}",
            url=banner.detail_page_url,
            color=embed_color,
            timestamp=datetime.now()
        )

        # Felder hinzufügen
        if banner.price_coins:
            embed.add_field(name="Preis", value=f"{banner.price_coins:,} Coins", inline=True)

        if banner.current_packs is not None and banner.total_packs:
            embed.add_field(
                name="Packs",
                value=f"{banner.current_packs} / {banner.total_packs}",
                inline=True
            )

        if banner.entries_per_day:
            embed.add_field(name="Pro Tag", value=f"{banner.entries_per_day}x", inline=True)

        if banner.best_hit:
            embed.add_field(name="Best Hit", value=banner.best_hit, inline=False)

        if banner.sale_end_date:
            countdown = format_end_date_countdown(banner.sale_end_date)
            embed.add_field(name="Ende", value=countdown, inline=True)

        embed.set_footer(text=f"Pack ID: {banner.pack_id}")

        # Bild hinzufügen falls vorhanden
        if banner.image_url:
            embed.set_image(url=banner.image_url)

        return embed

    async def _post_banner_to_discord(self, banner):
        """Postet einen Banner als Thread in Discord."""

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
        embed = self._build_banner_embed(banner)

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
                await thread.send("@everyone Neuer Banner verfügbar!")

            # Wahrscheinlichkeit initial posten
            await self._update_probability_message(thread.id, banner.pack_id)

            logger.info(f"Thread erstellt: {title} in #{channel.name}")

        except discord.HTTPException as e:
            logger.error(f"Discord-Fehler beim Thread erstellen: {e}")
        except Exception as e:
            logger.error(f"Fehler beim Thread erstellen: {e}")

    async def _post_pack_update_to_thread(self, pack_id: int, old_packs: int, new_packs: int, total_packs: int):
        """Postet einen Kommentar im Thread wenn sich die Pack-Anzahl ändert."""
        try:
            thread_data = await self.db.get_thread_by_banner_id(pack_id)
            if not thread_data:
                logger.debug(f"Kein Thread für Pack-Update {pack_id}")
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

            # Fortschrittsbalken erstellen
            if total > 0:
                percent = (new_packs / total) * 100
                filled = int(percent / 10)
                bar = "█" * filled + "░" * (10 - filled)
                progress = f"`{bar}` {percent:.0f}%"
            else:
                progress = ""

            message = f"{emoji} **Pack-Update:** {old_packs} → {new_packs} ({change})"
            if progress:
                message += f"\n{progress}"

            # @everyone Mention bei Pack-Update
            if MENTION_ON_PACK_UPDATE:
                message = f"@everyone\n{message}"

            await discord_rate_limiter.acquire("message_send")
            await thread.send(message)
            logger.debug(f"Pack-Update gepostet in Thread {thread_id}")

        except discord.HTTPException as e:
            logger.debug(f"Discord-Fehler bei Pack-Update: {e}")
        except Exception as e:
            logger.debug(f"Fehler bei Pack-Update für {pack_id}: {e}")

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

    async def _update_thread_embed(self, banner):
        """Aktualisiert das Embed im Thread mit aktuellen Daten (z.B. Countdown)."""
        try:
            thread_data = await self.db.get_thread_by_banner_id(banner.pack_id)
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

            # Starter-Message holen
            try:
                message = await thread.fetch_message(int(starter_message_id))
            except (discord.NotFound, Exception):
                logger.debug(f"Starter-Message für {banner.pack_id} nicht gefunden")
                return

            # Neues Embed erstellen
            new_embed = self._build_banner_embed(banner)

            # Message updaten
            await discord_rate_limiter.acquire("message_edit")
            await message.edit(embed=new_embed)
            logger.debug(f"Embed aktualisiert für Banner {banner.pack_id}")

        except discord.HTTPException as e:
            logger.debug(f"Discord-Fehler bei Embed-Update: {e}")
        except Exception as e:
            logger.debug(f"Fehler bei Embed-Update für {banner.pack_id}: {e}")

    async def _get_medals_from_reactions(self, thread, starter_message_id: int) -> list:
        """Liest Medaillen von Discord-Reaktionen auf der Starter-Message."""
        medals = []
        try:
            if not starter_message_id:
                return medals

            starter_msg = await thread.fetch_message(int(starter_message_id))
            for reaction in starter_msg.reactions:
                if str(reaction.emoji) == '🥇':
                    medals.append('T1')
                elif str(reaction.emoji) == '🥈':
                    medals.append('T2')
                elif str(reaction.emoji) == '🥉':
                    medals.append('T3')
        except Exception as e:
            logger.debug(f"Fehler beim Lesen der Reaktionen: {e}")
        return medals

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

            # Medaillen-Status holen (aus den neuen Spalten in discord_threads)
            thread_id_int = int(thread_id)
            medal_status = await self.db.get_medal_status(thread_id_int)
            logger.debug(f"Probability Update - Thread: {thread_id_int}, Banner: {banner_id}, Medal Status: {medal_status}")

            # Fallback: Wenn alle Medaillen als nicht-vergeben markiert sind, prüfe Discord-Reaktionen
            if not any(medal_status.values()) and starter_message_id:
                # Thread holen für Reaktions-Check
                thread = self.get_channel(thread_id_int)
                if not thread:
                    try:
                        thread = await self.fetch_channel(thread_id_int)
                    except (discord.NotFound, Exception):
                        thread = None

                if thread and isinstance(thread, discord.Thread):
                    reaction_medals = await self._get_medals_from_reactions(thread, starter_message_id)
                    if reaction_medals:
                        logger.info(f"Medaillen aus Reaktionen gelesen für Thread {thread_id_int}: {reaction_medals}")
                        # Sync: Medaillen in DB speichern (setzt auch die claimed-Spalten)
                        for tier in reaction_medals:
                            existing = await self.db.get_medal(thread_id_int, tier)
                            if not existing:
                                await self.db.save_medal(thread_id_int, tier, 0)
                                logger.debug(f"Medaille {tier} für Thread {thread_id_int} in DB nachgetragen")
                        # Status neu laden
                        medal_status = await self.db.get_medal_status(thread_id_int)

            # Anzahl vergebener Medaillen zählen
            claimed_count = sum(1 for claimed in medal_status.values() if claimed)
            hits_remaining = 3 - claimed_count
            logger.debug(f"Finale Medal Status für Thread {thread_id_int}: {medal_status}, Hits remaining: {hits_remaining}")

            if hits_remaining <= 0:
                # Alle Hits gezogen - keine Wahrscheinlichkeit mehr
                probability_text = "🎯 **Hit-Chance:** Alle Hits wurden gezogen!"
            elif pulls_per_day is None or pulls_per_day <= 0:
                # Unbegrenzte Pulls - zeige einfache Wahrscheinlichkeit pro Pull
                probability = (hits_remaining / current_packs) * 100
                probability_text = f"🎯 **Hit-Chance:** {probability:.2f}% pro Pull ({hits_remaining} Hits / {current_packs} Packs)"
            else:
                # Hypergeometrische Verteilung: P(X ≥ 1) = 1 - P(X = 0)
                # P(X = 0) = C(N-n, k) / C(N, k)
                # N = current_packs, n = hits_remaining, k = pulls_per_day
                N = current_packs
                n = hits_remaining
                k = min(pulls_per_day, N)  # k kann nicht größer als N sein

                # Wenn k > N-n, dann ist mindestens 1 Hit garantiert
                if k > N - n:
                    probability = 100.0
                else:
                    # P(X = 0) = C(N-n, k) / C(N, k)
                    p_zero = comb(N - n, k) / comb(N, k)
                    probability = (1 - p_zero) * 100

                probability_text = f"🎯 **Hit-Chance:** {probability:.2f}% bei {k} Pulls ({hits_remaining} Hits / {current_packs} Packs)\n*(gilt bei max. Anzahl der möglichen Züge pro Tag)*"

            # Medal-Status anzeigen (nur verfügbare Medaillen zeigen)
            available_medals = []
            if not medal_status['T1']:
                available_medals.append("🥇")
            if not medal_status['T2']:
                available_medals.append("🥈")
            if not medal_status['T3']:
                available_medals.append("🥉")

            if available_medals:
                medal_line = f"Verbleibend: {' '.join(available_medals)}"
                full_message = f"{probability_text}\n{medal_line}"
            else:
                # Alle Medaillen vergeben
                full_message = probability_text

            # Thread holen (falls nicht schon im Fallback geholt)
            thread = self.get_channel(thread_id_int)
            if not thread:
                try:
                    thread = await self.fetch_channel(thread_id_int)
                except (discord.NotFound, Exception):
                    return

            if not isinstance(thread, discord.Thread):
                return

            # Prüfe ob bereits eine Probability-Message existiert
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
                    # Message wurde gelöscht, neue erstellen
                    pass
                except Exception as e:
                    logger.debug(f"Fehler beim Editieren der Probability-Message: {e}")

            # Neue Nachricht erstellen
            await discord_rate_limiter.acquire("message_send")
            new_msg = await thread.send(full_message)
            await self.db.update_probability_message_id(thread_id, new_msg.id)
            logger.debug(f"Neue Probability-Message erstellt in Thread {thread_id}")

        except Exception as e:
            logger.debug(f"Fehler bei Probability-Update: {e}")

    async def _delete_banner_thread(self, pack_id: int) -> bool:
        """Archiviert den Discord-Thread für einen abgelaufenen Banner (statt löschen)."""
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
                # Thread archivieren und sperren (statt löschen)
                logger.info(f"   Archiviere Discord-Thread {thread_id}...")
                try:
                    # Abschluss-Nachricht posten
                    await discord_rate_limiter.acquire("message_send")
                    await thread.send("🔒 **Banner abgelaufen** - Dieser Thread wurde archiviert.")
                except:
                    pass

                # Thread archivieren und sperren
                await discord_rate_limiter.acquire("thread_edit")
                await thread.edit(
                    archived=True,
                    locked=True,
                    reason=f"Banner {pack_id} abgelaufen/ausverkauft"
                )
                logger.info(f"   Discord-Thread {thread_id} archiviert!")
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
        tier_match = re.search(r'\b(T[123])\b', content)
        if not tier_match:
            return

        tier = tier_match.group(1)  # "T1", "T2" oder "T3"
        logger.debug(f"T-Nachricht erkannt: {tier} von {message.author.name} in Thread {message.channel.id}")

        try:
            user_id = message.author.id
            thread_id = message.channel.id
            emoji = {'T1': '🥇', 'T2': '🥈', 'T3': '🥉'}[tier]

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

            else:
                # Normaler Thread
                thread_data = await self.db.get_thread_by_id(thread_id)
                if not thread_data:
                    logger.debug(f"Thread {thread_id} nicht in DB gefunden")
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
            if not HOT_BANNER_CHANNEL_ID:
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
            for rank, banner in enumerate(sorted_banners, 1):
                await self._post_hot_banner(channel, banner, rank)
                await asyncio.sleep(1)  # Rate-Limiting

            logger.info(f"Hot-Banner Update abgeschlossen: {len(sorted_banners)} Banner")

        except Exception as e:
            logger.error(f"Fehler bei Hot-Banner Update: {e}")

    async def _post_hot_banner(self, channel: discord.ForumChannel, banner: dict, rank: int):
        """Postet einen einzelnen Hot-Banner als Thread (gleiches Format wie normale Banner)."""
        try:
            pack_id = banner.get('pack_id')
            probability = banner.get('probability', 0)
            pulls = banner.get('entries_per_day')
            medal_count = banner.get('medal_count', 0) or 0
            hits_remaining = 3 - medal_count

            # Thread-Titel (wie normal aber mit Rang und Wahrscheinlichkeit)
            pulls_text = f"{pulls}" if pulls else "unbegrenzt"
            title = f"#{rank} | {probability:.1f}% | ID: {pack_id} | {pulls_text} Pulls"
            if len(title) > 100:
                title = title[:97] + "..."

            # Kategorie-Farben (gleich wie normale Banner)
            category_colors = {
                "Bonus": 0xFFD700,
                "MIX": 0x9B59B6,
                "Yu-Gi-Oh!": 0x8B4513,
                "Pokémon": 0xFFCC00,
                "Weiss Schwarz": 0x2C3E50,
                "One piece": 0xE74C3C,
                "Hobby": 0x27AE60,
            }
            embed_color = category_colors.get(banner.get('category'), 0xFFD700)

            # Embed erstellen (gleiches Format wie normale Banner, mit Hot-Banner Extras)
            banner_title = banner.get('title') or f"Pack {pack_id}"
            embed = discord.Embed(
                title=f"🔥 #{rank} | {banner_title}",
                url=banner.get('detail_page_url'),
                color=embed_color,
                timestamp=datetime.now()
            )

            # Bild setzen (wie normale Banner)
            image_url = banner.get('image_url')
            if image_url:
                embed.set_image(url=image_url)
                logger.debug(f"Hot-Banner {pack_id}: Bild gesetzt - {image_url[:50]}...")
            else:
                logger.warning(f"Hot-Banner {pack_id}: Kein Bild-URL vorhanden!")

            # Hit-Chance als erstes Feld
            embed.add_field(
                name="🎯 Hit-Chance",
                value=f"**{probability:.2f}%** ({hits_remaining}/3 Hits)",
                inline=False
            )

            # Preis
            if banner.get('price_coins'):
                embed.add_field(name="Preis", value=f"{banner.get('price_coins'):,} Coins", inline=True)

            # Packs
            if banner.get('current_packs') is not None and banner.get('total_packs'):
                embed.add_field(
                    name="Packs",
                    value=f"{banner.get('current_packs')} / {banner.get('total_packs')}",
                    inline=True
                )

            # Pro Tag
            if pulls:
                embed.add_field(name="Pro Tag", value=f"{pulls}x", inline=True)
            else:
                embed.add_field(name="Pro Tag", value="unbegrenzt", inline=True)

            # Best Hit
            if banner.get('best_hit'):
                embed.add_field(name="Best Hit", value=banner.get('best_hit'), inline=False)

            # Ende (Countdown)
            if banner.get('sale_end_date'):
                countdown = format_end_date_countdown(banner.get('sale_end_date'))
                embed.add_field(name="Ende", value=countdown, inline=True)

            # Kategorie
            embed.add_field(name="Kategorie", value=banner.get('category', 'Unbekannt'), inline=True)

            embed.set_footer(text=f"Pack ID: {pack_id}")

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
        if not HOT_BANNER_CHANNEL_ID:
            await interaction.response.send_message("❌ HOT_BANNER_CHANNEL_ID nicht konfiguriert!")
            return

        await interaction.response.defer()
        await self._update_hot_banners()
        await interaction.followup.send("🔥 Hot-Banner aktualisiert!")
