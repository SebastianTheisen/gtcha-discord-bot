"""
Discord Bot Client - Forum-Channel Version
"""

import asyncio
import re
from dataclasses import dataclass
from datetime import datetime
from typing import Optional

import discord
from discord import app_commands
from discord.ext import commands
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from loguru import logger

from config import (
    GUILD_ID, SCRAPE_INTERVAL_MINUTES, BASE_URL,
    CHANNEL_IDS, CATEGORIES
)
from scraper.gtcha_scraper import GTCHAScraper
from database.db import Database


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

        # Scheduler starten (mit Timeout-Wrapper)
        self.scheduler.add_job(
            self._scrape_with_timeout,
            'interval',
            minutes=SCRAPE_INTERVAL_MINUTES,
            id='scrape_job',
            replace_existing=True,
            coalesce=True,  # Verpasste Jobs zusammenfassen
            max_instances=1,  # Maximal eine Instanz gleichzeitig
            misfire_grace_time=300,  # Job kann bis zu 5 Min verspätet starten
        )
        self.scheduler.start()
        logger.info(f"Scheduler: Alle {SCRAPE_INTERVAL_MINUTES} Min")

        # Commands synchronisieren
        if GUILD_ID:
            guild = discord.Object(id=int(GUILD_ID))
            self.tree.copy_global_to(guild=guild)
            await self.tree.sync(guild=guild)
            logger.info("Slash Commands synchronisiert")

    async def on_ready(self):
        logger.info(f"Bot online: {self.user}")

        # Threads aus Discord wiederherstellen (falls DB leer nach Neustart)
        await self._recover_threads_from_discord()

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
                for banner in banners:
                    try:
                        # Pruefe ob Banner neu ist
                        existing = await self.db.get_banner(banner.pack_id)

                        # Banner mit 0 Packs: Thread löschen falls vorhanden
                        if banner.current_packs is not None and banner.current_packs == 0:
                            logger.info(f"Banner {banner.pack_id} hat 0 Packs - prüfe Löschung")
                            if existing:
                                logger.info(f"   Banner {banner.pack_id} existiert in DB - lösche Thread")
                                # Thread löschen
                                deleted = await self._delete_banner_thread(banner.pack_id)
                                if deleted:
                                    deleted_count += 1
                                    logger.info(f"   Banner {banner.pack_id} Thread gelöscht!")
                                else:
                                    logger.warning(f"   Banner {banner.pack_id} konnte nicht gelöscht werden")
                            else:
                                logger.debug(f"   Banner {banner.pack_id} nicht in DB")
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

                    except Exception as e:
                        logger.error(f"Fehler bei Banner {banner.pack_id}: {e}")

                # === NICHT-GEFUNDEN-TRACKING ===
                # Sammle alle gefundenen Banner-IDs (inkl. der mit 0 Packs)
                found_banner_ids = {b.pack_id for b in banners}

                # Hole alle bekannten Banner aus der DB
                db_banner_ids = set(await self.db.get_all_active_banner_ids())

                # Für gefundene Banner: Zähler zurücksetzen
                for pack_id in found_banner_ids:
                    if pack_id in db_banner_ids:
                        await self.db.reset_not_found_count(pack_id)

                # Für NICHT gefundene Banner: Zähler erhöhen
                expired_count = 0
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
                logger.info(f"Scrape done: {elapsed:.1f}s, {new_count} neu, {deleted_count} gelöscht, {expired_count} abgelaufen, {skipped_empty} leer")

        except Exception as e:
            logger.error(f"Scrape-Fehler: {e}")
        finally:
            self._scraper = None

    async def _scrape_with_timeout(self):
        """Wrapper für scrape_and_post mit 3-Minuten-Timeout."""
        timeout_seconds = 180  # 3 Minuten
        try:
            await asyncio.wait_for(self.scrape_and_post(), timeout=timeout_seconds)
        except asyncio.TimeoutError:
            logger.error(f"TIMEOUT: Scrape-Job nach {timeout_seconds}s abgebrochen!")
            # Scraper aufräumen falls noch aktiv
            if self._scraper:
                try:
                    await self._scraper.close()
                except Exception:
                    pass
                self._scraper = None
        except Exception as e:
            logger.error(f"Fehler im Scrape-Job: {e}")

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

        # Thread-Titel: ID: X / Kosten: Y / Anzahl: Z / Gesamt: W
        price = banner.price_coins or 0
        entries = banner.entries_per_day if banner.entries_per_day else "unbegrenzt"
        total = banner.total_packs or 0
        title = f"ID: {banner.pack_id} / Kosten: {price} / Anzahl: {entries} / Gesamt: {total}"
        if len(title) > 100:
            title = title[:97] + "..."

        # Embed erstellen
        embed = discord.Embed(
            title=banner.title or f"Pack {banner.pack_id}",
            url=banner.detail_page_url,
            color=discord.Color.gold(),
            timestamp=datetime.now()
        )

        # Felder hinzufuegen
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
            embed.add_field(name="Ende", value=banner.sale_end_date, inline=True)

        embed.set_footer(text=f"Pack ID: {banner.pack_id}")

        # Bild hinzufuegen falls vorhanden
        if banner.image_url:
            embed.set_image(url=banner.image_url)

        try:
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

            message = f"{emoji} **Pack-Update:** {old_packs} → {new_packs} ({change})"

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
            new_title = f"ID: {banner.pack_id} / Kosten: {price} / Anzahl: {entries} / Gesamt: {total}"
            if len(new_title) > 100:
                new_title = new_title[:97] + "..."

            # Nur updaten wenn sich Titel geändert hat
            if thread.name != new_title:
                await thread.edit(name=new_title)
                logger.info(f"Thread-Titel aktualisiert: {new_title}")

        except discord.HTTPException as e:
            logger.debug(f"Discord-Fehler bei Titel-Update: {e}")
        except Exception as e:
            logger.debug(f"Fehler bei Titel-Update für {banner.pack_id}: {e}")

    async def _delete_banner_thread(self, pack_id: int) -> bool:
        """Löscht den Discord-Thread für einen Banner mit 0 Packs."""
        try:
            logger.info(f"   Lösche Thread für Banner {pack_id}...")

            thread_data = await self.db.get_thread_by_banner_id(pack_id)
            if not thread_data:
                logger.warning(f"   Kein Thread in DB für Banner {pack_id}")
                # Trotzdem Banner aus DB löschen
                await self.db.delete_banner(pack_id)
                return False

            thread_id = thread_data.get('thread_id')
            logger.info(f"   Thread-ID für {pack_id}: {thread_id}")

            if not thread_id:
                logger.warning(f"   Keine thread_id in Daten für {pack_id}")
                return False

            # Thread aus Discord löschen
            # Erst aus Cache versuchen
            thread = self.get_channel(int(thread_id))
            logger.info(f"   Thread aus Cache: {thread}")

            # Falls nicht im Cache, von API holen
            if not thread:
                try:
                    logger.info(f"   Hole Thread {thread_id} von API...")
                    thread = await self.fetch_channel(int(thread_id))
                    logger.info(f"   Thread von API: {thread}")
                except discord.NotFound:
                    logger.info(f"   Thread {thread_id} existiert nicht mehr in Discord")
                    thread = None
                except Exception as e:
                    logger.warning(f"   Fehler beim Fetchen von Thread {thread_id}: {e}")
                    thread = None

            if thread and isinstance(thread, discord.Thread):
                logger.info(f"   Lösche Discord-Thread {thread_id}...")
                await thread.delete(reason=f"Banner {pack_id} ausverkauft (0 Packs)")
                logger.info(f"   Discord-Thread {thread_id} gelöscht!")
            else:
                logger.info(f"   Kein gültiger Thread zum Löschen gefunden")

            # Aus DB entfernen (auch wenn Thread schon gelöscht war)
            logger.info(f"   Entferne aus DB...")
            await self.db.delete_thread(pack_id)
            await self.db.delete_banner(pack_id)
            logger.info(f"   DB-Einträge für {pack_id} entfernt")

            return True

        except discord.NotFound:
            # Thread existiert nicht mehr - trotzdem aus DB entfernen
            logger.debug(f"Thread für {pack_id} nicht gefunden - entferne aus DB")
            await self.db.delete_thread(pack_id)
            await self.db.delete_banner(pack_id)
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
            # Pruefe ob Thread zu einem Banner gehoert
            thread_data = await self.db.get_thread_by_id(message.channel.id)
            if not thread_data:
                logger.debug(f"Thread {message.channel.id} nicht in DB gefunden")
                return

            user_id = message.author.id
            thread_id = message.channel.id

            # Pruefe ob Medaille schon vergeben
            existing = await self.db.get_medal(thread_id, tier)
            if existing:
                await message.reply(f"❌ {tier} wurde bereits von <@{existing['user_id']}> beansprucht!")
                return

            # Medaille vergeben
            await self.db.save_medal(thread_id, tier, user_id)

            # Emoji-Reaktion auf die ERSTE Nachricht im Thread (Banner-Post)
            emoji = {'T1': '🥇', 'T2': '🥈', 'T3': '🥉'}[tier]

            # Hole die Starter-Message (erste Nachricht im Thread)
            starter_message_id = thread_data.get('starter_message_id')
            if starter_message_id:
                try:
                    starter_message = await message.channel.fetch_message(int(starter_message_id))
                    await starter_message.add_reaction(emoji)
                except Exception as e:
                    logger.debug(f"Konnte Starter-Message nicht finden: {e}")
                    # Fallback: Reaktion auf aktuelle Nachricht
                    await message.add_reaction(emoji)
            else:
                # Fallback: Reaktion auf aktuelle Nachricht
                await message.add_reaction(emoji)

            await message.reply(f"{emoji} {tier} geht an {message.author.mention}!")

            logger.info(f"Medaille: {tier} an {message.author.name} in {message.channel.name}")

        except Exception as e:
            logger.error(f"Fehler bei Medaillen-Vergabe: {e}")
            await message.reply(f"❌ Fehler: {e}")

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
