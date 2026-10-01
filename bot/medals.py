"""Medaillen T1-T10: Vergabe per Chat, Reaktionen, Abgleich beim Start."""

from bot.common import *  # noqa: F401,F403


class MedalsMixin:
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
        tier_match = re.search(r'\bT([1-9]\d?)\b', content)
        if not tier_match:
            return

        tier = f"T{int(tier_match.group(1))}"  # "T1" bis "T50"
        logger.debug(f"T-Nachricht erkannt: {tier} von {message.author.name} in Thread {message.channel.id}")

        try:
            user_id = message.author.id
            thread_id = message.channel.id
            emoji = MEDAL_EMOJIS.get(tier, MEDAL_EMOJI_DEFAULT)

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
