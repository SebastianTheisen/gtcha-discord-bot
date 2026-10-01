"""Discord-Threads: Startbeitrag, Thread anlegen, Titel, Wiederherstellung."""

from bot.common import *  # noqa: F401,F403


class ThreadsMixin:
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

                        title_info = parse_thread_title(thread_name)
                        if not title_info["pack_id"]:
                            logger.debug(f"Thread-Titel passt nicht: {thread_name}")
                            continue

                        pack_id = title_info["pack_id"]

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
                            banner = RecoveredBanner(
                                pack_id=pack_id,
                                category=category,
                                price_coins=title_info["price"],
                                entries_per_day=title_info["entries"],
                                total_packs=title_info["total"],
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

    def _get_banner_value(self, banner, key, default=None):
        """Holt einen Wert aus Banner-Objekt oder Dict."""
        if isinstance(banner, dict):
            return banner.get(key, default)
        return getattr(banner, key, default)

    def _build_banner_embed(self, banner, title_prefix: str = None, stats: Optional[dict] = None,
                            tempo: Optional[str] = None, conditions: Optional[str] = None,
                            shipped: Optional[str] = None, minimum: Optional[str] = None,
                            starts_at: Optional[int] = None,
                            pool_value: Optional[str] = None,
                            hit_link: Optional[str] = None) -> discord.Embed:
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
            # Discord-Zeitstempel: Datum in der Zeitzone des Lesers plus Countdown; sonst Text der Seite
            end_ts = sale_end_timestamp(get('sale_end_date'))
            countdown = (f"<t:{end_ts}:f> (<t:{end_ts}:R>)" if end_ts
                         else format_end_date_countdown(get('sale_end_date')))
            embed.add_field(name="Ende", value=countdown, inline=True)

        if stats:
            ev_text = f"{fmt_coins(stats['ev'])} Coins"
            if stats['ev_pct'] is not None:
                ev_text += f" ({fmt_pct(stats['ev_pct'])} % vom Preis)"
            if stats.get('data_based'):
                ev_text += "\n*aus Poolwert minus umgewandelten und verschickten Coins der Seite*"
            elif stats['estimated']:
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
        if hit_link:
            embed.add_field(name="Hit-Liste", value=f"[🏆 Zur Hit-Liste springen]({hit_link})", inline=False)

        if minimum:
            embed.add_field(name="Mindestens zurück pro Zug", value=minimum, inline=False)
        if pool_value:
            embed.add_field(name="Gesamtwert", value=pool_value, inline=False)
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

        title = thread_title(banner.pack_id, banner.price_coins, banner.total_packs, banner.entries_per_day,
                             "upcoming" if starts_at else "running", starts_at)

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

    async def _update_thread_title(self, banner):
        """Aktualisiert den Thread-Titel wenn sich Banner-Daten geändert haben."""
        await self._sync_thread_title(self._get_banner_value(banner, 'pack_id'))

    async def _thread_status(self, row: dict, thread_data: dict) -> str:
        """upcoming / hits_out / endspurt / running für den Thread-Titel."""
        if row.get('starts_at') and not row.get('start_announced'):
            return "upcoming"
        stats = await self._pool_stats(row, thread_data)
        if stats:
            if stats['tracked_hits'] and stats['hits_total'] and not stats['hits_open']:
                # "Hits raus" nur, wenn jeder Hit sicher raus ist (Medaille oder eindeutig erkannt),
                # nicht schon, wenn ❓-Stellvertreter die Rechnung auffüllen
                pool = await self.db.get_card_pool(row['pack_id'])
                _, sure, winners, _ = await self._pulled_cards(int(thread_data['thread_id']), row['pack_id'], pool)
                price = _int(row.get('price_coins')) or None
                if all(u['key'] in sure or u['key'] in winners for u in relevant_units(pool, price)):
                    return "hits_out"
            elif not stats['tracked_hits'] and not stats['open_tiers']:
                return "hits_out"
        if thread_data.get('endspurt_sent'):
            return "endspurt"
        return "running"

    async def _sync_thread_title(self, pack_id: int):
        """Benennt den Thread um, wenn Status, Preis, Packs oder Limit nicht mehr zum Titel passen."""
        try:
            row = await self.db.get_banner(pack_id)
            thread_data = await self.db.get_thread_by_banner_id(pack_id)
            if not row or not thread_data or thread_data.get('is_expired'):
                return
            status = await self._thread_status(row, thread_data)
            title = thread_title(pack_id, row.get('price_coins'), row.get('total_packs'),
                                 row.get('entries_per_day'), status, row.get('starts_at'))
            thread_id = int(thread_data['thread_id'])
            if thread_data.get('title') == title:
                return
            thread = self.get_channel(thread_id) or await self.fetch_channel(thread_id)
            if not isinstance(thread, discord.Thread):
                return
            if thread.name != title:
                if thread.archived:
                    await discord_rate_limiter.acquire("thread_edit")
                    await thread.edit(archived=False)
                await discord_rate_limiter.acquire("thread_edit")
                await thread.edit(name=title)
                logger.info(f"Thread-Titel: {title}")
            await self.db.set_thread_title(thread_id, title)
        except discord.NotFound:
            pass
        except Exception as e:
            logger.warning(f"Thread-Titel von {pack_id} nicht aktualisiert: {type(e).__name__}: {e}")

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
                logger.warning(f"Startbeitrag von {pack_id}: Thread- oder Nachrichten-ID fehlt in der DB")
                return

            # Thread holen
            thread = self.get_channel(int(thread_id))
            if not thread:
                try:
                    thread = await self.fetch_channel(int(thread_id))
                except Exception as e:
                    logger.warning(f"Startbeitrag von {pack_id}: Thread {thread_id} nicht abrufbar: {e}")
                    return

            if not isinstance(thread, discord.Thread):
                return

            if thread.archived:
                try:
                    await discord_rate_limiter.acquire("thread_edit")
                    await thread.edit(archived=False)
                except Exception as e:
                    logger.warning(f"Startbeitrag von {pack_id}: Thread nicht reaktivierbar: {e}")
                    return

            # Starter-Message holen
            try:
                message = await thread.fetch_message(int(starter_message_id))
            except Exception as e:
                logger.warning(f"Startbeitrag für {pack_id} nicht gefunden: {e}")
                return

            stats = await self._pool_stats(banner, thread_data)
            tempo = await self._sales_tempo(pack_id, _int(self._get_banner_value(banner, 'current_packs')))
            row = await self.db.get_banner(pack_id) or {}
            conditions = format_conditions(row.get('conditions'))
            shipped = format_shipping(row.get('site_stats'))
            pool = await self.db.get_card_pool(pack_id)
            minimum = self._minimum_text(pool, row.get('price_coins'))
            pool_value = self._pool_value_text(pool, row.get('price_coins'), row.get('total_packs'))
            hit_ids = json.loads(thread_data.get('hit_message_ids') or 'null') or (
                [thread_data['top5_message_id']] if thread_data.get('top5_message_id') else [])
            hit_link = (f"https://discord.com/channels/{thread.guild.id}/{thread.id}/{hit_ids[0]}"
                        if hit_ids else None)
            new_embed = self._build_banner_embed(banner, stats=stats, tempo=tempo, conditions=conditions,
                                                 shipped=shipped, minimum=minimum, pool_value=pool_value,
                                                 hit_link=hit_link)

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

        except Exception as e:
            logger.warning(f"Startbeitrag von {pack_id} nicht aktualisiert: {type(e).__name__}: {e}")

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
                await self._refresh_pool_views(pid, force=True)
            else:
                await self._update_thread_embed(row)
        await self.db.set_meta('embed_version', str(EMBED_VERSION))
        logger.info("Startbeiträge aktualisiert")

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

    @staticmethod
    def _pool_value_text(pool: Optional[dict], price, total_packs) -> Optional[str]:
        """Summe aller Kartenwerte im Banner und was alle Packs zusammen kosten."""
        if not pool or not pool.get('total_value'):
            return None
        total = _int(pool['total_value'])
        lines = [f"Alle Karten: {fmt_coins(total)} Coins"]
        cost = _int(price) * _int(total_packs or pool.get('total_count'))
        if cost:
            lines.append(f"Alle Packs kaufen: {fmt_coins(cost)} Coins "
                         f"({fmt_pct(total / cost * 100)} % zurück)")
        return "\n".join(lines)

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
