import asyncio
import io
from datetime import datetime
from typing import Optional, List
import discord
from discord import app_commands
from discord.ext import commands
from loguru import logger
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from config import config, CATEGORIES, MEDAL_EMOJIS
from database import Database, Banner
from scraper import GTCHAScraper, ScrapedBanner


class GTCHABot(commands.Bot):
    def __init__(self):
        intents = discord.Intents.default()
        intents.message_content = True
        intents.guilds = True
        intents.members = True
        super().__init__(command_prefix="!", intents=intents, help_command=None)
        self.db = Database(config.database_path)
        self.scheduler = AsyncIOScheduler()
        self.last_scrape: Optional[datetime] = None
        self.is_scraping: bool = False
        self.guild_id = config.guild_id

    async def setup_hook(self):
        await self.db.init_db()
        await self._setup_commands()
        self.scheduler.add_job(
            self.run_scrape_job,
            "interval",
            minutes=config.scrape_interval_minutes,
            id="scrape_job",
            replace_existing=True
        )
        self.scheduler.start()
        logger.info(f"Scheduler: Alle {config.scrape_interval_minutes} Min")

    async def _setup_commands(self):
        @self.tree.command(name="refresh", description="Sofortiger Scrape")
        @app_commands.checks.has_permissions(administrator=True)
        async def refresh_cmd(interaction: discord.Interaction):
            if self.is_scraping:
                await interaction.response.send_message("Scrape läuft bereits...", ephemeral=True)
                return
            await interaction.response.send_message("Starte Scrape...", ephemeral=True)
            await self.run_scrape_job()
            await interaction.followup.send(f"Fertig! {self.last_scrape}", ephemeral=True)

        @self.tree.command(name="status", description="Bot-Status")
        async def status_cmd(interaction: discord.Interaction):
            stats = await self.db.get_stats()
            embed = discord.Embed(title="GTCHA Bot Status", color=discord.Color.green())
            embed.add_field(
                name="Stats",
                value=f"Aktiv: {stats['active_banners']}\nGesamt: {stats['total_banners']}\nThreads: {stats['total_threads']}\nMedaillen: {stats['total_medals']}",
                inline=False
            )
            embed.add_field(
                name="Letzter Scrape",
                value=str(self.last_scrape) if self.last_scrape else "Noch nicht",
                inline=True
            )
            await interaction.response.send_message(embed=embed)

        @self.tree.command(name="sync", description="Commands sync (Admin)")
        @app_commands.checks.has_permissions(administrator=True)
        async def sync_cmd(interaction: discord.Interaction):
            await interaction.response.defer(ephemeral=True)
            guild = discord.Object(id=self.guild_id)
            self.tree.copy_global_to(guild=guild)
            synced = await self.tree.sync(guild=guild)
            await interaction.followup.send(f"{len(synced)} Commands synced!", ephemeral=True)

        guild = discord.Object(id=self.guild_id)
        self.tree.copy_global_to(guild=guild)

    async def on_ready(self):
        logger.info(f"Bot online: {self.user}")
        try:
            guild = discord.Object(id=self.guild_id)
            await self.tree.sync(guild=guild)
        except Exception as e:
            logger.error(f"Sync error: {e}")
        asyncio.create_task(self.run_scrape_job())

    async def on_message(self, message: discord.Message):
        if message.author.bot:
            return
        if not isinstance(message.channel, discord.Thread):
            return
        content = message.content.strip().upper()
        if content not in MEDAL_EMOJIS:
            return
        thread_data = await self.db.get_thread_by_thread_id(message.channel.id)
        if not thread_data:
            return
        medal = await self.db.claim_medal(
            thread_id=message.channel.id,
            tier=content,
            user_id=message.author.id
        )
        if medal and thread_data.starter_message_id:
            try:
                starter = await message.channel.fetch_message(thread_data.starter_message_id)
                await starter.add_reaction(MEDAL_EMOJIS[content])
                logger.info(f"{MEDAL_EMOJIS[content]} an {message.author}")
            except Exception as e:
                logger.error(f"Reaktion error: {e}")

    async def run_scrape_job(self):
        if self.is_scraping:
            return
        self.is_scraping = True
        start = datetime.utcnow()
        logger.info("Scrape startet...")
        try:
            async with GTCHAScraper(base_url=config.base_url) as scraper:
                scraped = await scraper.scrape_all_banners()
                known_ids = set(await self.db.get_all_pack_ids())
                scraped_ids = {b.pack_id for b in scraped}

                for banner in scraped:
                    if banner.pack_id not in known_ids:
                        await self._process_new_banner(scraper, banner)
                    else:
                        await self._check_banner_update(banner)

                for db_banner in await self.db.get_all_active_banners():
                    if db_banner.pack_id not in scraped_ids:
                        await self._mark_banner_expired(db_banner)
        except Exception as e:
            logger.error(f"Scrape error: {e}")
        finally:
            self.is_scraping = False
            self.last_scrape = datetime.utcnow()
            logger.info(f"Scrape done in {(self.last_scrape - start).total_seconds():.1f}s")

    async def _process_new_banner(self, scraper: GTCHAScraper, scraped: ScrapedBanner):
        logger.info(f"Neu: {scraped.pack_id} ({scraped.category})")
        try:
            best_hit, detail_ss = await scraper.scrape_banner_details(scraped.pack_id)
            scraped.best_hit = best_hit
            scraped.detail_screenshot = detail_ss
            if scraped.image_url:
                scraped.banner_screenshot = await scraper.download_image(scraped.image_url)
            db_banner = await self.db.create_banner(**scraped.to_dict())
            await self._create_discord_thread(db_banner, scraped)
        except Exception as e:
            logger.error(f"Error {scraped.pack_id}: {e}")

    async def _create_discord_thread(self, db_banner: Banner, scraped: ScrapedBanner):
        channel_id = config.get_channel_id(scraped.category)
        if not channel_id:
            return
        channel = self.get_channel(channel_id)
        if not channel or not isinstance(channel, discord.ForumChannel):
            return
        try:
            title = scraped.thread_title[:100]
            embed = discord.Embed(
                title=db_banner.title or f"Pack #{db_banner.pack_id}",
                url=scraped.detail_page_url,
                color=discord.Color.gold()
            )
            embed.add_field(name="Preis", value=f"{db_banner.price_coins}c", inline=True)
            embed.add_field(name="Eintritte", value=f"x{db_banner.entries_per_day}", inline=True)
            embed.add_field(name="Packs", value=f"{db_banner.current_packs}/{db_banner.total_packs}", inline=True)
            if db_banner.best_hit:
                embed.add_field(name="Best Hit", value=db_banner.best_hit, inline=False)

            files = []
            if scraped.banner_screenshot:
                files.append(discord.File(io.BytesIO(scraped.banner_screenshot), filename="banner.png"))
                embed.set_image(url="attachment://banner.png")

            thread, msg = await channel.create_thread(
                name=title,
                embed=embed,
                files=files if files else discord.utils.MISSING
            )
            logger.info(f"Thread: {title}")

            if scraped.detail_screenshot:
                await thread.send(
                    content=f"{scraped.detail_page_url}",
                    file=discord.File(io.BytesIO(scraped.detail_screenshot), filename="detail.png")
                )

            await self.db.create_discord_thread(
                banner_id=db_banner.id,
                thread_id=thread.id,
                channel_id=channel.id,
                starter_message_id=msg.id
            )
        except Exception as e:
            logger.error(f"Thread error: {e}")

    async def _check_banner_update(self, scraped: ScrapedBanner):
        db_banner = await self.db.get_banner_by_pack_id(scraped.pack_id)
        if not db_banner or scraped.current_packs is None or db_banner.current_packs is None:
            return
        if scraped.current_packs != db_banner.current_packs:
            old, new = db_banner.current_packs, scraped.current_packs
            await self.db.add_pack_history(db_banner.id, old, new)
            await self.db.update_banner(db_banner.pack_id, current_packs=new)
            if db_banner.discord_thread:
                try:
                    thread = self.get_channel(db_banner.discord_thread.thread_id)
                    if thread:
                        await thread.send(f"**Rückstand:** {old} -> {new}")
                except:
                    pass

    async def _mark_banner_expired(self, db_banner: Banner):
        logger.info(f"Abgelaufen: {db_banner.pack_id}")
        await self.db.mark_banner_inactive(db_banner.pack_id)
        if db_banner.discord_thread and not db_banner.discord_thread.is_expired:
            try:
                thread = self.get_channel(db_banner.discord_thread.thread_id)
                if thread:
                    await thread.edit(name=db_banner.expired_thread_title[:100])
                    await self.db.mark_thread_expired(thread.id)
            except:
                pass
