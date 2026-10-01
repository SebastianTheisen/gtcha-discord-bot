"""Hot-Banner-Kanal (optional) und Slash-Commands."""

from bot.common import *  # noqa: F401,F403


class HotBannerMixin:
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
