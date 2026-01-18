"""
Discord Bot Client - Forum-Channel Version
"""

import asyncio
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

        # Scheduler starten
        self.scheduler.add_job(
            self.scrape_and_post,
            'interval',
            minutes=SCRAPE_INTERVAL_MINUTES,
            id='scrape_job',
            replace_existing=True,
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

        # Erster Scrape nach 5 Sekunden
        await asyncio.sleep(5)
        await self.scrape_and_post()

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
                            if banner.current_packs != existing.get('current_packs'):
                                await self.db.update_banner_packs(
                                    banner.pack_id,
                                    banner.current_packs
                                )
                                logger.debug(f"Update: {banner.pack_id} Packs: {banner.current_packs}")

                    except Exception as e:
                        logger.error(f"Fehler bei Banner {banner.pack_id}: {e}")

                elapsed = (datetime.now() - start_time).total_seconds()
                logger.info(f"Scrape done: {elapsed:.1f}s, {new_count} neu, {deleted_count} gelöscht, {skipped_empty} leer")

        except Exception as e:
            logger.error(f"Scrape-Fehler: {e}")
        finally:
            self._scraper = None

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

        # Thread-Titel: PackID/Kosten/Entries/TotalPacks
        price = banner.price_coins or 0
        entries = banner.entries_per_day or 1
        total = banner.total_packs or 0
        title = f"{banner.pack_id}/{price}/{entries}x/{total}"
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

        content = message.content.strip().upper()
        if content not in ['T1', 'T2', 'T3']:
            return

        logger.debug(f"T-Nachricht erkannt: {content} von {message.author.name} in Thread {message.channel.id}")

        try:
            # Pruefe ob Thread zu einem Banner gehoert
            thread_data = await self.db.get_thread_by_id(message.channel.id)
            if not thread_data:
                logger.debug(f"Thread {message.channel.id} nicht in DB gefunden")
                return

            tier = content
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
        await self.scrape_and_post()
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
