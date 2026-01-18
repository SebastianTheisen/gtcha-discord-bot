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
                for banner in banners:
                    try:
                        # Pruefe ob Banner neu ist
                        existing = await self.db.get_banner(banner.pack_id)

                        if not existing:
                            # Neuer Banner - Best Hit von Detail-Seite holen
                            if not banner.best_hit:
                                try:
                                    best_hit, _ = await scraper.scrape_banner_details(banner.pack_id)
                                    if best_hit:
                                        banner.best_hit = best_hit
                                        logger.debug(f"Best Hit fuer {banner.pack_id}: {best_hit}")
                                except Exception as e:
                                    logger.debug(f"Best Hit Fehler: {e}")

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
                logger.info(f"Scrape done: {elapsed:.1f}s, {new_count} neue Banner")

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

        # Thread-Titel
        title = banner.title or f"Pack {banner.pack_id}"
        if len(title) > 100:
            title = title[:97] + "..."

        # Embed erstellen
        embed = discord.Embed(
            title=title,
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

    async def on_message(self, message: discord.Message):
        """Listener fuer T1/T2/T3 Reaktionen."""
        if message.author.bot:
            return

        # Pruefe ob in einem unserer Threads
        if not isinstance(message.channel, discord.Thread):
            return

        content = message.content.strip().upper()
        if content not in ['T1', 'T2', 'T3']:
            return

        # Pruefe ob Thread zu einem Banner gehoert
        thread_data = await self.db.get_thread_by_id(message.channel.id)
        if not thread_data:
            return

        tier = content
        user_id = message.author.id
        thread_id = message.channel.id

        # Pruefe ob Medaille schon vergeben
        existing = await self.db.get_medal(thread_id, tier)
        if existing:
            await message.reply(f"Fehler: {tier} wurde bereits von <@{existing['user_id']}> beansprucht!")
            return

        # Medaille vergeben
        await self.db.save_medal(thread_id, tier, user_id)

        # Emoji-Reaktion
        emoji = {'T1': '🥇', 'T2': '🥈', 'T3': '🥉'}[tier]
        await message.add_reaction(emoji)
        await message.reply(f"{emoji} {tier} geht an {message.author.mention}!")

        logger.info(f"Medaille: {tier} an {message.author.name} in {message.channel.name}")

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
