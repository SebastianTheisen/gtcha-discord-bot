"""Scrape-Ablauf: pack/list, Tab-Scrape, Banner anlegen, Pack-Updates, Löschen."""

from bot.common import *  # noqa: F401,F403


class ScrapingMixin:
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

                # === THREAD-TITEL abgleichen (Status: angekündigt, Endspurt, Hits raus) ===
                for pid in await self.db.get_active_banners():
                    await self._sync_thread_title(pid)

                # === KAUFBEDINGUNGEN und VERSAND-ZAHLEN aus pack/list ===
                await self._apply_site_data(getattr(scraper, '_api_pack_data', {}) or {})

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

    async def _apply_site_data(self, api_items: dict):
        """Preis, Kaufbedingungen und Versand-Zahlen aus pack/list übernehmen; Startbeitrag bei Änderung."""
        for pid, item in api_items.items():
            changed = await self.db.update_conditions(pid, banner_conditions(item))
            if _int(item.get('point')) > 0:
                changed = await self.db.update_price(pid, _int(item.get('point'))) or changed
            changed = await self.db.update_site_stats(pid, shipping_stats(item)) or changed
            if changed:
                row = await self.db.get_banner(pid)
                if row and row.get('is_active'):
                    await self._update_thread_embed(row)

    async def _scrape_with_timeout(self):
        """Wrapper für scrape_and_post mit konfigurierbarem Timeout und Retry-Logik."""
        # Verhindert doppelte Scrapes (Scheduler + /refresh); auf den schnellen Abfrager wird gewartet
        if self._main_scrape_running:
            logger.warning("Scrape läuft bereits - überspringe diesen Aufruf")
            return

        self._main_scrape_running = True
        try:
            await self._scrape_with_timeout_locked()
        finally:
            self._main_scrape_running = False

    async def _scrape_with_timeout_locked(self):
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
