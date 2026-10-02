"""Hot-Banner-Kanal (optional): eine Rangliste der besten ziehbaren Banner, und Slash-Commands."""

from bot.common import *  # noqa: F401,F403
from utils.banner_info import berlin_time
from utils.hot_list import HOT_TOP, hot_line, min_rank, needs_password, new_alerts, rank_entries

HOT_VERSION = "2"   # 2 = eine Ranglisten-Nachricht statt zehn Threads


class HotBannerMixin:
    async def _hot_entries(self) -> list:
        """Alle Banner, die man gerade ziehen kann, mit Ø Rückgabe und offenen Hits."""
        entries = []
        for pid, row in (await self.db.get_active_banners()).items():
            price, remaining = _int(row.get('price_coins')), _int(row.get('current_packs'))
            if row.get('category') == 'Bonus' or price <= 0 or remaining <= 0:
                continue
            if needs_password(row.get('conditions')):
                continue
            thread_data = await self.db.get_thread_by_banner_id(pid)
            if not thread_data or thread_data.get('is_expired'):
                continue
            status = await self._thread_status(row, thread_data)
            if status in ("upcoming", "hits_out"):
                continue
            stats = await self._pool_stats(row, thread_data)
            if not stats or stats.get('ev_pct') is None:
                continue
            pool = await self.db.get_card_pool(pid)
            _, _, _, open_groups = await self._pulled_cards(int(thread_data['thread_id']), pid, pool)
            entries.append({
                "pack_id": pid, "thread_id": int(thread_data['thread_id']), "price": price,
                "pct": stats['ev_pct'], "remaining": remaining, "cost_to_hit": stats.get('cost_to_hit'),
                "tracked_hits": stats['tracked_hits'],
                "hits_open": stats['hits_open'] if stats['tracked_hits'] else len(stats['open_tiers']),
                "hits_total": stats['hits_total'], "unsure": bool(open_groups),
                "endspurt": status == "endspurt", "rank": min_rank(row.get('conditions')),
            })
        return entries

    async def _hot_thread(self, channel: discord.ForumChannel) -> Optional[discord.Thread]:
        thread_id = await self.db.get_meta('hot_thread_id')
        if not thread_id:
            return None
        try:
            thread = self.get_channel(int(thread_id)) or await self.fetch_channel(int(thread_id))
        except discord.NotFound:
            return None
        if thread.archived:
            await discord_rate_limiter.acquire("thread_edit")
            await thread.edit(archived=False)
        return thread

    async def _update_hot_banners(self, force: bool = False):
        """Pflegt die Rangliste 'Top 10 nach Ø Rückgabe' im Hot-Banner-Kanal (eine Nachricht, wird bearbeitet).

        Steigt ein Banner neu über 100 %, kommt eine stille Meldung (ohne Ping) in denselben Thread.
        """
        if not HOT_BANNER_CHANNEL_ID or not HOT_BANNER_ENABLED or await self._slim():
            return   # schlank: Top 10 nur in der App
        try:
            channel = self.get_channel(HOT_BANNER_CHANNEL_ID) or await self.fetch_channel(HOT_BANNER_CHANNEL_ID)
            if not isinstance(channel, discord.ForumChannel):
                logger.error("Hot-Banner Channel ist kein Forum-Channel!")
                return
            if await self.db.get_meta('hot_version') != HOT_VERSION:
                await self._cleanup_hot_banner_threads(channel)   # alte Einzel-Threads weg
                for key in ('hot_thread_id', 'hot_message_id', 'hot_sig', 'hot_alerted'):
                    await self.db.set_meta(key, '')
                await self.db.set_meta('hot_version', HOT_VERSION)

            entries = await self._hot_entries()
            top = rank_entries(entries)
            guild_id = channel.guild.id
            lines = [hot_line(i, e, f"https://discord.com/channels/{guild_id}/{e['thread_id']}")
                     for i, e in enumerate(top, 1)]
            body = "\n".join(lines) or "Gerade kein ziehbarer Banner mit bekanntem Kartenpool."
            sig = body
            thread = await self._hot_thread(channel)
            if thread and not force and await self.db.get_meta('hot_sig') == sig:
                return

            now = berlin_time(int(datetime.now().timestamp()))
            embed = discord.Embed(title=f"🔥 Top {HOT_TOP} nach Ø Rückgabe pro Zug", description=body,
                                  color=0xE67E22)
            embed.set_footer(text=f"Stand {now:%d.%m. %H:%M} · ❓ = Hit nicht eindeutig erkannt · "
                                  f"ohne Bonus-, Gratis- und Passwort-Banner")
            message = None
            if thread:
                try:
                    message = await thread.fetch_message(int(await self.db.get_meta('hot_message_id') or 0))
                    await discord_rate_limiter.acquire("message_edit")
                    await message.edit(content=None, embed=embed)
                except (discord.NotFound, ValueError):
                    message = None
            if message is None:
                await discord_rate_limiter.acquire("thread_create")
                thread, message = await channel.create_thread(name="🔥 Hot-Banner", embed=embed,
                                                              reason="Hot-Banner-Rangliste")
                await self.db.set_meta('hot_thread_id', str(thread.id))
                await self.db.set_meta('hot_message_id', str(message.id))
                logger.info("Hot-Banner: Ranglisten-Thread angelegt")
            await self.db.set_meta('hot_sig', sig)

            # Stille Meldung, wenn ein Banner neu über 100 % liegt (erste Runde nur merken)
            stored = await self.db.get_meta('hot_alerted')
            alerted = set(json.loads(stored)) if stored else set()
            fresh, alerted = new_alerts(entries, alerted)
            if stored:
                for e in fresh:
                    await discord_rate_limiter.acquire("message_send")
                    await thread.send(
                        f"💰 <#{e['thread_id']}> liegt jetzt bei Ø **{fmt_pct(e['pct'])} %** zurück "
                        f"({fmt_coins(e['price'])} Coins pro Zug, {fmt_coins(e['remaining'])} Packs übrig)",
                        silent=True, allowed_mentions=discord.AllowedMentions.none())
                    logger.info(f"Hot-Banner: {e['pack_id']} über 100 % ({e['pct']:.1f} %)")
            await self.db.set_meta('hot_alerted', json.dumps(sorted(alerted)))
        except Exception as e:
            logger.error(f"Fehler bei Hot-Banner Update: {type(e).__name__}: {e}")

    async def _cleanup_hot_banner_threads(self, channel: discord.ForumChannel):
        """Löscht alle Threads im Hot-Banner Channel (Umstellung auf die Ranglisten-Nachricht)."""
        deleted = 0
        threads = list(channel.threads)
        try:
            async for thread in channel.archived_threads(limit=100):
                threads.append(thread)
        except Exception as e:
            logger.debug(f"Archivierte Hot-Banner-Threads nicht lesbar: {e}")
        for thread in threads:
            try:
                await discord_rate_limiter.acquire("thread_delete")
                await thread.delete()
                deleted += 1
            except Exception as e:
                logger.debug(f"Konnte Hot-Banner Thread nicht löschen: {e}")
        if deleted:
            logger.info(f"Hot-Banner: {deleted} alte Threads gelöscht")

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
        await self._update_hot_banners(force=True)
        await interaction.followup.send("🔥 Hot-Banner aktualisiert!")
