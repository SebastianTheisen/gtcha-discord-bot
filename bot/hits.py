"""Hits: Kartenpool, Erkennung, Hit-Liste, Hit-Chance, Endspurt und Lohnt-sich-Hinweis."""

from bot.common import *  # noqa: F401,F403
from database.db import STORE, store_thread_id


class HitsMixin:
    async def _hit_chance_text(self, banner: dict, thread_data: Optional[dict], thread_id: int) -> str:
        """Text der 🎯-Nachricht: Chance auf mindestens einen Hit bei 1 / 10 / 50 Zügen."""
        remaining = _int(banner.get('current_packs'))
        pool = await self.db.get_card_pool(banner['pack_id'])
        if pool and pool.get('version') == 2 and pool.get('hits') and thread_data:
            stats = await self._pool_stats(banner, thread_data)
            hits_open, label = stats['hits_open'], f"{stats['hits_open']} von {stats['hits_total']} Hits noch drin"
        else:
            claimed = await self._claimed_tiers(thread_id, banner['pack_id'])
            hits_open = sum(1 for t in TIERS if not claimed.get(t))
            label = f"{hits_open} von 3 Top-Karten noch drin"
        if hits_open <= 0:
            return "🎯 **Hit-Chance:** Alle Hits wurden gezogen!"
        parts = []
        for pulls in HIT_CHANCE_PULLS:
            if pulls > remaining:
                break
            pct = chance_at_least_one(remaining, hits_open, pulls)
            parts.append(f"{pulls} {'Zug' if pulls == 1 else 'Züge'}: {fmt_pct(pct, 2 if pct < 10 else 1)} %")
        text = "🎯 **Hit-Chance:** " + (" · ".join(parts) if parts else "100 %")
        text += f"\n{label} · {fmt_coins(remaining)} Packs übrig"
        if banner.get('entries_per_day'):
            text += f"\n*Max. {banner['entries_per_day']} Züge pro Tag*"
        return text

    async def _pool_stats(self, banner, thread_data: dict) -> Optional[dict]:
        """Ø Rückgabe und Hit-Chance aus Kartenpool, Rest-Packs, Medaillen und erkannten Hits.

        Die Ø Rückgabe kommt aus den Zahlen der Seite (umgewandelt + verschickt), sobald sie da sind.
        """
        get = lambda key: self._get_banner_value(banner, key)
        pid = get('pack_id')
        pool = await self.db.get_card_pool(pid)
        if not pool:
            return None
        pulled, _, winners, _ = await self._pulled_cards(int(thread_data['thread_id']), pid, pool)
        row = await self.db.get_banner(pid) or {}
        shipped_keys = set((await self._public_pull_tracking(pid))["pulled"])
        site = json.loads(row['site_stats']) if row.get('site_stats') else {}
        out_value = out_of_banner_value(pool, row.get('converted'), _int(site.get('coins')),
                                        set(winners) - shipped_keys, shipped_keys)
        return estimate(pool, get('current_packs'), get('total_packs'), pulled, get('price_coins'), out_value)

    async def _pulled_cards(self, thread_id: int, pack_id: int, pool: dict) -> tuple:
        """(gezogene Karten inkl. Stellvertreter, nur automatisch erkannte, Gewinner, offene ❓-Gruppen).

        Medaille Tn zählt für Platz n der Hit-Liste; Gewinner = Schlüssel -> Discord-User-ID.
        Für Discord: öffentlicher (ggf. zeitversetzter) Stand - die App rechnet mit dem echten.
        """
        medals = await self._public_medals(int(thread_id))
        state = await self._public_pull_tracking(pack_id)
        keys = tier_keys(pool)
        winners = {keys[t]: user for t, user in medals.items() if t in keys}
        pulled, sure, open_groups = resolve_pulled(state["pulled"], state["unsure"], set(winners))
        return pulled, sure - set(winners), winners, open_groups

    async def _claimed_tiers(self, thread_id: int, pack_id: int) -> dict:
        """T1-T3 als gezogen (Medaille oder automatisch erkannt) für die 🎯-Nachricht."""
        medals = await self._public_medals(int(thread_id))
        pool = await self.db.get_card_pool(pack_id)
        if not pool:
            return {t: t in medals for t in TIERS}
        state = await self._public_pull_tracking(pack_id)
        _, detected, _ = resolve_pulled(state["pulled"], state["unsure"], set())
        keys = tier_keys(pool)
        return {t: t in medals or keys.get(t) in detected for t in TIERS}

    async def _check_endspurt(self, thread: discord.Thread, thread_data: dict, stats: dict, banner, silent: bool):
        """Einmaliger Alarm, wenn nur noch wenige Packs übrig und noch Hits drin sind."""
        if thread_data.get('endspurt_sent'):
            return
        get = lambda key: self._get_banner_value(banner, key)
        remaining, total = _int(get('current_packs')), _int(get('total_packs'))
        open_hits = [u for u in stats['open_units'] if u.get('hit', u['shipping_only'])] or (
            [] if stats['tracked_hits'] else stats['open_units'])
        if not total or remaining <= 0 or remaining > total * ENDSPURT_PERCENT / 100 or not open_hits:
            return
        await self.db.set_endspurt_sent(thread.id)
        if silent or await self._slim():   # schlank: nur in der App
            return
        units = tracked_units(await self.db.get_card_pool(get('pack_id')))
        rank = {u['key']: i for i, u in enumerate(units, 1)}
        top = ", ".join(f"{self._rank_icon(rank[u['key']])} {u['name']} ({fmt_coins(u['value'])})"
                        for u in open_hits[:3])
        more = f" und {len(open_hits) - 3} weitere" if len(open_hits) > 3 else ""
        chance = chance_at_least_one(remaining, len(open_hits), min(10, remaining))
        mention = "@everyone " if MENTION_ON_PACK_UPDATE else ""
        await discord_rate_limiter.acquire("message_send")
        await thread.send(
            f"{mention}⚡ **Endspurt:** nur noch {fmt_coins(remaining)} von {fmt_coins(total)} Packs!\n"
            f"Noch drin: {top}{more}\n"
            f"Chance auf mindestens einen Hit bei {min(10, remaining)} Zügen: {fmt_pct(chance)} %"
        )
        logger.info(f"Endspurt-Alarm gepostet: Banner {get('pack_id')} ({remaining}/{total})")

    async def _check_value_alert(self, thread: discord.Thread, thread_data: dict, stats: dict, banner,
                                 silent: bool = False):
        """Einmaliger Hinweis, wenn die Ø Rückgabe über 100 % des Preises steigt."""
        pct = stats.get('ev_pct')
        if pct is None:
            return
        alert_sent = bool(thread_data.get('value_alert_sent'))
        silent = silent or await self._slim()   # schlank: "Lohnt sich" nur in der App
        if pct > 100 and not alert_sent and silent:
            await self.db.set_value_alert_sent(thread.id, True)
        elif pct > 100 and not alert_sent:
            price = self._get_banner_value(banner, 'price_coins')
            mention = "@everyone " if MENTION_ON_PACK_UPDATE else ""
            await discord_rate_limiter.acquire("message_send")
            await thread.send(
                f"{mention}💰 **Lohnt sich gerade:** Ø Rückgabe pro Zug ca. {fmt_coins(stats['ev'])} Coins "
                f"= {fmt_pct(pct)} % des Preises ({fmt_coins(price)} Coins)"
            )
            await self.db.set_value_alert_sent(thread.id, True)
            logger.info(f"Lohnt-sich-Hinweis gepostet: Thread {thread.id} ({pct:.1f} %)")
        elif pct < 97 and alert_sent:
            # Hysterese: erst deutlich unter 100 % zurücksetzen, damit es nicht hin und her springt
            await self.db.set_value_alert_sent(thread.id, False)

    async def _detect_pulled_hits(self, api_items: dict):
        """Erkennt gezogene Hits an den Zählern aus pack/list.

        Versand-Hits ("Versand nur") können nur verschickt werden: ein Anstieg von total_sendcount
        und total_sendprice wird exakt mit den Kartenwerten abgeglichen. Banner ohne Versand-Hits
        nutzen als Rückfall den Sprung von total_kangen + total_sendprice für T1-T3.
        """
        for pid, item in api_items.items():
            try:
                pool = await self.db.get_card_pool(pid)
                if not pool or pool.get('version') != 2 or not pool.get('total_count'):
                    continue
                store = (await self.db.get_banner(pid) or {}).get('is_active') == STORE
                if store:   # Store-Pack: Erkennung genauso, aber ohne Discord (Medaillen an der internen Nummer)
                    medal_thread = store_thread_id(pid)
                else:
                    thread_data = await self.db.get_thread_by_banner_id(pid)
                    if not thread_data or thread_data.get('is_expired'):
                        continue
                    medal_thread = int(thread_data['thread_id'])

                state = await self.db.get_pull_tracking(pid)
                pulled, unsure = list(state["pulled"]), list(state["unsure"])
                ships = shipment_values(item) or (None, None)
                value = decided_value(item)
                match = {"certain": [], "groups": [], "maybe": []}
                first_look, reason = False, ""

                if pool.get('hits'):
                    # Alle Versand-Schübe gemeinsam auswerten: jeder Hit kann nur einmal verschickt
                    # werden, spätere Schübe klären so frühere ❓ auf. Gespeichert wird immer das
                    # Gesamtergebnis; gemeldet wird nur, was gegenüber vorher neu ist.
                    count, ship_value = ships
                    prev_count, prev_value = state["ship_count"], state["ship_value"]
                    batches = state["batches"]
                    changed = False
                    if count is None:
                        pass
                    elif batches is None or prev_count is None:
                        batches = await self.db.rebuild_ship_batches(pid, count, ship_value)
                        first_look, changed = True, True
                        reason = (f"bisher {count} Karten / {fmt_coins(card_value(ship_value))} Coins Kartenwert "
                                  f"in {len(batches)} Schüben verschickt")
                    elif count > prev_count and ship_value > prev_value:
                        batches = batches + [[count - prev_count, ship_value - prev_value, int(time.time())]]
                        changed = True
                        reason = (f"{count - prev_count} Karte(n) / {fmt_coins(card_value(ship_value - prev_value))} "
                                  f"Coins Kartenwert verschickt")
                    if not changed:
                        await self.db.set_pull_tracking(pid, value, ships[0], ships[1], pulled, unsure)
                        continue
                    # im Hintergrund-Thread: die Auftrags-Rechnung kann bei großen Werten etwas dauern
                    # Regel der Gruppe: Medaille gesetzt = Versand angefordert -> der Hit steckt spätestens im
                    # ersten Schub nach der Medaille
                    keys = tier_keys(pool)
                    medal_t = {keys[t]: m["at"] for t, m in (await self.db.medal_rows(medal_thread)).items()
                               if t in keys and m.get("at") and m["source"] != "admin"}   # Admin: nur "raus", kein Versand
                    deadlines = batch_deadlines(batches, medal_t)
                    price = _int((await self.db.get_banner(pid) or {}).get('price_coins')) or None
                    joint = await asyncio.to_thread(match_shipment_history, pool, batches, VALUE_TOLERANCE, deadlines,
                                                    price)
                    if joint.get("ignored_deadlines"):
                        logger.info(f"[HIT] {pid}: Medaillen-Frist passt nicht zu den Schüben, ignoriert: "
                                    f"{joint['ignored_deadlines']}")
                    old_groups = {(frozenset(g["keys"]), g["pulled"]) for g in unsure}
                    match = {
                        "certain": [k for k in joint["certain"] if k not in set(pulled)],
                        "groups": [g for g in joint["groups"] if (frozenset(g["keys"]), g["pulled"]) not in old_groups],
                        "maybe": [],
                    }
                    await self.db.set_pull_tracking(pid, value, ships[0], ships[1],
                                                    joint["certain"], joint["groups"], batches)
                else:
                    known, _, _ = resolve_pulled(pulled, unsure, set())
                    if value is not None and state["decided_value"] is not None and value > state["decided_value"]:
                        match["certain"] = detect_jump_pulls(pool, value - state["decided_value"], known)
                        reason = f"Anstieg {value - state['decided_value']:,} Coins"
                    await self.db.set_pull_tracking(pid, value, ships[0], ships[1],
                                                    pulled + match["certain"], unsure)
                if not (match["certain"] or match["groups"] or match["maybe"]):
                    continue

                logger.info(f"[HIT] {pid}: {reason} -> sicher {match['certain']}, "
                            f"wertgleich {match['groups']}, möglich {match['maybe']}")
                if store:
                    continue   # gespeichert ist alles (die App liest es) - kein Post, keine Hit-Liste in Discord
                thread_id = medal_thread
                text = None
                if not first_look:
                    medals = await self.db.get_medals(thread_id)
                    medal_keys = {k for t, k in tier_keys(pool).items() if t in medals}
                    price = _int((await self.db.get_banner(pid) or {}).get('price_coins')) or None
                    worth = {u["key"] for u in tracked_units(pool) if is_relevant_hit(u, price)}
                    certain = [k for k in match["certain"] if k not in medal_keys and k in worth]
                    groups = [g for g in match["groups"] if set(g["keys"]) & worth]
                    maybe = [g for g in match["maybe"] if set(g["keys"]) & worth]
                    if certain or groups or maybe:
                        text = self._detected_hits_text(pool, certain, groups, maybe)
                # in Discord ggf. zeitversetzt (Post und Markierung in Hit-Liste/Startbeitrag)
                await self._publish_pulls(pid, thread_id, {"pulled": state["pulled"], "unsure": state["unsure"]},
                                          text, immediate=first_look)
                await self._refresh_pool_views(pid)
                await self._update_probability_message(thread_id, pid)
            except Exception as e:
                logger.warning(f"[HIT] Fehler bei Banner {pid}: {e}")

    def _detected_hits_text(self, pool: dict, certain: list, groups: list = (), maybe: list = ()) -> str:
        """Text der "Hit gezogen"-Meldung (wird sofort oder zeitversetzt gepostet)."""
        units = tracked_units(pool)
        rank = {u["key"]: i for i, u in enumerate(units, 1)}
        label = {u["key"]: f"{self._rank_icon(rank[u['key']])} {u['name']}" for u in units}
        lines = []
        for key in certain:
            unit = units[rank[key] - 1]
            card_text = f"{label[key]} ({fmt_coins(unit['value'])} Coins)"
            if unit["shipping_only"]:
                lines.append(f"🔥 **Hit gezogen:** {card_text}")
            elif rank[key] == 1:
                lines.append(f"🔥 **T1 gezogen:** {card_text}")
            else:
                lines.append(f"🔥 **Großer Hit gezogen**, vermutlich T{rank[key]}: {card_text}")
        for group in groups:
            names = " oder ".join(label[k] for k in group["keys"])
            amount = "eine der Karten" if group["pulled"] == 1 else f"{group['pulled']} der Karten"
            lines.append(f"🔥 **Hit gezogen:** {amount} mit {self._value_span(group)} ❓ ({names})")
        for group in maybe:
            names = " oder ".join(label[k] for k in group["keys"])
            lines.append(f"❓ **Möglicher Hit:** Eine Karte mit {self._value_span(group)} wurde verschickt. "
                         f"Das kann {names} sein, aber auch eine normale Karte mit gleichem Wert.")
        key_tier = {k: t for t, k in tier_keys(pool).items()}
        asks = [f"**{key_tier[k]}**" for k in certain if k in key_tier]
        for group in list(groups) + list(maybe):
            tiers = [f"**{key_tier[k]}**" for k in group["keys"] if k in key_tier]
            if tiers:
                asks.append(" oder ".join(tiers))
        if asks:
            lines.append(f"Warst du's? Schreib {' bzw. '.join(asks)} hier rein und hol dir deine Medaille 🏅")
        if all(u["shipping_only"] for u in units):
            lines.append("*Automatisch erkannt: die Karte wurde gerade zum Versand angefordert.*")
        else:
            lines.append("*Automatisch erkannt: die Karte wurde gerade in Coins umgewandelt oder verschickt.*")
        mention = "@everyone " if MENTION_ON_PACK_UPDATE and (certain or groups) else ""
        return mention + "\n".join(lines)

    @staticmethod
    def _value_span(group: dict) -> str:
        low, high = group['value'], group.get('value_max') or group['value']
        if high == low:
            return f"{fmt_coins(low)} Coins"
        return f"{fmt_coins(low)}–{fmt_coins(high)} Coins"

    @staticmethod
    def _rank_icon(rank: int) -> str:
        return {1: "🥇", 2: "🥈", 3: "🥉"}.get(rank, f"{rank}.")

    @staticmethod
    def _card_status(key: str, pulled: set, detected: set, unsure: list, winners: dict) -> tuple:
        """(Text, Farbe) für eine Karte in der Hit-Liste; None-Text = noch drin."""
        if winners.get(key):
            return f"✅ gezogen von <@{winners[key]}>", 0x95A5A6
        for group in unsure:
            if key in group["keys"] and group["pulled"] > 0 and not set(group["keys"]) <= pulled:
                amount = "eine" if group["pulled"] == 1 else str(group["pulled"])
                return f"❓ {amount} von {len(group['keys'])} ähnlich teuren Karten gezogen", 0xE67E22
        if key in pulled:
            return "✅ gezogen" + (" (erkannt)" if key in detected else ""), 0x95A5A6
        for group in unsure:
            if key in group["keys"] and group["pulled"] == 0:
                return "❓ möglicherweise gezogen (gleicher Wert wie eine normale Karte)", 0xE67E22
        return None, 0xFFD700

    def _build_hit_messages(self, pool: dict, pulled: set, detected: set, unsure: list,
                            winners: Optional[dict] = None, price: Optional[int] = None) -> list:
        """[(Überschrift, Embeds), ...]: alle Versand-Hits ab Packpreis in Nachrichten zu je 10, sonst Top 5."""
        units = relevant_units(pool, price) if pool.get('hits') else tracked_units(pool)
        if pool.get('hits') and not units:
            units = tracked_units(pool)[:5]
        if pool.get('hits'):
            entries = units[:MAX_LISTED]
            open_count = sum(1 for u in units if u["key"] not in pulled)
            header = f"🏆 **Hits im Pool** (nur Versand) · noch drin: {open_count} von {len(units)}"
        elif len(claimable_units(pool, price)) > len(units):
            # ohne Versand-Hits: alle Karten ab Packpreis, Platz = Medaille (T1, T2, ...)
            units = claimable_units(pool, price)
            entries = units[:MAX_LISTED]
            open_count = sum(1 for u in units if u["key"] not in pulled and u["key"] not in (winners or {}))
            header = f"🏆 **Karten ab Packpreis** · noch drin: {open_count} von {len(units)}"
        else:
            # verfolgte Exemplare (T1-T3, gleiche Karte ggf. mehrfach), danach weitere teure Karten
            seen = {str(u.get("id")) for u in units}
            entries = list(units) + [{**c, "key": None} for c in pool.get('top', [])
                                     if str(c.get("id")) not in seen]
            entries = entries[:5]
            header = "🏆 **Top 5 Karten** (Coin-Wert)"
        embeds = []
        for position, card in enumerate(entries, 1):
            # Platz = Medaille (streng nach Wert über alle Karten), nicht die Position in dieser Liste
            rank = int(card["tier"][1:]) if card.get("tier") else position
            status, color = self._card_status(card.get("key"), pulled, detected, unsure, winners or {})
            description = f"**{fmt_coins(card['value'])} Coins**" + (f" · {status}" if status else "")
            embed = discord.Embed(title=f"{self._rank_icon(rank)} {card['name']}"[:256],
                                  description=description, color=color)
            if card.get('image'):
                embed.set_thumbnail(url=card['image'])
            embeds.append(embed)
        chunks = [embeds[i:i + EMBEDS_PER_MESSAGE] for i in range(0, len(embeds), EMBEDS_PER_MESSAGE)]
        if len(chunks) <= 1:
            return [(header, chunks[0])] if chunks else []
        return [(f"{header} · Teil 1/{len(chunks)}" if i == 0 else f"🏆 **Hits im Pool** · Teil {i + 1}/{len(chunks)}",
                 chunk) for i, chunk in enumerate(chunks)]

    async def _refresh_pool_views(self, pack_id: int, initial_pool: bool = False, embed: bool = True,
                                  force: bool = False):
        """Aktualisiert Startbeitrag (Ø Rückgabe, Hits) und die Hit-Nachricht(en) eines Banners.

        Die Hit-Nachrichten werden nur bearbeitet, wenn sich ihr Inhalt gegenüber dem zuletzt
        geposteten Stand geändert hat (Signatur in der DB) oder force gesetzt ist.
        Die erste Hit-Nachricht wird angeheftet, der Startbeitrag verlinkt auf sie.
        """
        try:
            banner = await self.db.get_banner(pack_id)
            thread_data = await self.db.get_thread_by_banner_id(pack_id)
            pool = await self.db.get_card_pool(pack_id)
            if not banner or not thread_data or not pool or thread_data.get('is_expired'):
                return

            if embed:
                await self._update_thread_embed(banner, initial_pool=initial_pool)

            thread_id = int(thread_data['thread_id'])
            thread = self.get_channel(thread_id)
            if not thread:
                thread = await self.fetch_channel(thread_id)
            if not isinstance(thread, discord.Thread):
                return

            pulled, detected, winners, open_groups = await self._pulled_cards(thread_id, pack_id, pool)
            maybe = [g for g in (await self._public_pull_tracking(pack_id))["unsure"] if g.get("pulled", 0) == 0]
            messages = self._build_hit_messages(pool, pulled, detected, open_groups + maybe, winners,
                                                price=_int(banner.get('price_coins')) or None)
            if not messages:
                return

            old_ids = json.loads(thread_data.get('hit_message_ids') or 'null') or (
                [thread_data['top5_message_id']] if thread_data.get('top5_message_id') else [])
            sig = json.dumps([[c, [[e.title, e.description, e.color.value if e.color else None,
                                    e.thumbnail.url if e.thumbnail else None] for e in em]]
                              for c, em in messages], ensure_ascii=False)
            if old_ids and thread_data.get('hit_list_sig') == sig and not force:
                return
            new_ids = []
            for i, (content, embeds) in enumerate(messages):
                msg = None
                if i < len(old_ids):
                    try:
                        msg = await thread.fetch_message(int(old_ids[i]))
                        await discord_rate_limiter.acquire("message_edit")
                        await msg.edit(content=content, embeds=embeds)
                    except discord.NotFound:
                        msg = None
                if msg is None:
                    await discord_rate_limiter.acquire("message_send")
                    msg = await thread.send(content=content, embeds=embeds)
                    logger.info(f"Hit-Nachricht {i + 1}/{len(messages)} gepostet: Banner {pack_id}")
                if i == 0:
                    await self._pin_hit_message(msg, pack_id)
                new_ids.append(msg.id)
            for extra_id in old_ids[len(messages):]:
                try:
                    old = await thread.fetch_message(int(extra_id))
                    await discord_rate_limiter.acquire("message_delete")
                    await old.delete()
                except discord.NotFound:
                    pass
            if new_ids != old_ids:
                await self.db.set_hit_message_ids(thread_id, new_ids)
                if embed and (not old_ids or new_ids[0] != old_ids[0]):
                    await self._update_thread_embed(banner)   # Link zur neuen Hit-Liste
            await self.db.set_hit_list_sig(thread_id, sig)
            if old_ids:
                logger.info(f"Hit-Liste aktualisiert: Banner {pack_id} ({len(messages)} Nachricht(en))")
        except Exception as e:
            logger.warning(f"Fehler bei Hit-Nachricht/Ø-Update für {pack_id}: {e}")

    async def _pin_hit_message(self, msg: discord.Message, pack_id: int):
        """Heftet die Hit-Liste an, damit sie über 'Angeheftete Nachrichten' direkt erreichbar ist."""
        if msg.pinned:
            return
        try:
            await discord_rate_limiter.acquire("message_edit")
            await msg.pin(reason="Hit-Liste")
        except discord.Forbidden:
            logger.warning(f"Hit-Liste von {pack_id} nicht angeheftet: Bot fehlt die Berechtigung 'Nachrichten anheften'")
        except discord.HTTPException as e:
            logger.warning(f"Hit-Liste von {pack_id} nicht angeheftet: {e}")

    async def _find_existing_probability_message(self, thread: discord.Thread) -> Optional[discord.Message]:
        """
        Sucht im Thread nach einer existierenden Wahrscheinlichkeits-Nachricht.
        Gibt die Nachricht zurück, falls gefunden, sonst None.
        """
        try:
            # Die letzten 50 Nachrichten durchsuchen (sollte ausreichen)
            async for message in thread.history(limit=50):
                # Nur Bot-Nachrichten prüfen
                if message.author.id != self.user.id:
                    continue
                # Prüfen ob es eine Probability-Nachricht ist (beginnt mit dem Hit-Chance Emoji)
                if message.content and message.content.startswith("🎯 **Hit-Chance:**"):
                    logger.debug(f"Existierende Probability-Nachricht gefunden in Thread {thread.id}: {message.id}")
                    return message
        except Exception as e:
            logger.debug(f"Fehler beim Suchen der Probability-Nachricht: {e}")
        return None

    async def _update_probability_message(self, thread_id: int, banner_id: int):
        """Erstellt oder aktualisiert die Wahrscheinlichkeits-Nachricht im Thread (nicht im schlanken Modus)."""
        if await self._slim():
            return
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

            thread_id_int = int(thread_id)
            full_message = await self._hit_chance_text(banner, thread_data, thread_id_int)

            # Thread holen (falls nicht schon im Fallback geholt)
            thread = self.get_channel(thread_id_int)
            if not thread:
                try:
                    thread = await self.fetch_channel(thread_id_int)
                except (discord.NotFound, Exception):
                    return

            if not isinstance(thread, discord.Thread):
                return

            # Prüfe ob bereits eine Probability-Message existiert (in der Datenbank)
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
                    # Message wurde gelöscht, im Thread suchen
                    logger.debug(f"Probability-Message {existing_msg_id} nicht mehr vorhanden, suche im Thread...")
                except Exception as e:
                    logger.debug(f"Fehler beim Editieren der Probability-Message: {e}")

            # Fallback: Im Thread nach existierender Probability-Nachricht suchen
            # (z.B. nach Bot-Neustart wenn Message-ID nicht in DB war)
            existing_msg = await self._find_existing_probability_message(thread)
            if existing_msg:
                try:
                    await discord_rate_limiter.acquire("message_edit")
                    await existing_msg.edit(content=full_message)
                    # Message-ID in DB speichern für zukünftige Updates
                    await self.db.update_probability_message_id(thread_id, existing_msg.id)
                    logger.info(f"Existierende Probability-Message gefunden und aktualisiert in Thread {thread_id}")
                    return
                except Exception as e:
                    logger.debug(f"Fehler beim Aktualisieren der gefundenen Probability-Message: {e}")

            # Keine existierende Nachricht gefunden - neue erstellen
            await discord_rate_limiter.acquire("message_send")
            new_msg = await thread.send(full_message)
            await self.db.update_probability_message_id(thread_id, new_msg.id)
            logger.debug(f"Neue Probability-Message erstellt in Thread {thread_id}")

        except Exception as e:
            logger.debug(f"Fehler bei Probability-Update: {e}")

    async def _cleanup_duplicate_probability_messages(self):
        """Löscht doppelte Wahrscheinlichkeits-Nachrichten in allen Threads (behält nur die neueste)."""
        logger.info("Räume doppelte Wahrscheinlichkeits-Nachrichten auf...")
        total_deleted = 0
        threads_cleaned = 0

        try:
            # Alle aktiven Threads aus der DB holen
            async with aiosqlite.connect(self.db.db_path) as db:
                db.row_factory = aiosqlite.Row
                cursor = await db.execute(
                    "SELECT thread_id FROM discord_threads WHERE is_expired = 0"
                )
                threads = await cursor.fetchall()

            for thread_row in threads:
                thread_id = thread_row['thread_id']

                try:
                    # Thread holen
                    thread = self.get_channel(thread_id)
                    if not thread:
                        thread = await self.fetch_channel(thread_id)

                    if not thread or not isinstance(thread, discord.Thread):
                        continue

                    # Alle Probability-Nachrichten im Thread finden
                    probability_messages = []
                    async for message in thread.history(limit=100):
                        # Nur Bot-Nachrichten prüfen
                        if message.author.id != self.user.id:
                            continue
                        # Prüfen ob es eine Probability-Nachricht ist
                        if message.content and message.content.startswith("🎯 **Hit-Chance:**"):
                            probability_messages.append(message)

                    # Wenn mehr als eine Probability-Nachricht gefunden wurde
                    if len(probability_messages) > 1:
                        # Nach Erstellungsdatum sortieren (neueste zuerst)
                        probability_messages.sort(key=lambda m: m.created_at, reverse=True)

                        # Die neueste behalten, alle anderen löschen
                        newest_message = probability_messages[0]
                        messages_to_delete = probability_messages[1:]

                        for msg in messages_to_delete:
                            try:
                                await discord_rate_limiter.acquire("message_delete")
                                await msg.delete()
                                total_deleted += 1
                                logger.debug(f"Doppelte Probability-Nachricht {msg.id} in Thread {thread_id} gelöscht")
                            except discord.NotFound:
                                pass  # Nachricht bereits gelöscht
                            except Exception as e:
                                logger.debug(f"Fehler beim Löschen der Nachricht {msg.id}: {e}")

                        # Message-ID der neuesten in DB speichern
                        await self.db.update_probability_message_id(thread_id, newest_message.id)
                        threads_cleaned += 1
                        logger.info(f"Thread {thread_id}: {len(messages_to_delete)} doppelte Nachricht(en) gelöscht")

                    elif len(probability_messages) == 1:
                        # Nur eine Nachricht - ID in DB speichern falls nicht vorhanden
                        existing_id = await self.db.get_probability_message_id(thread_id)
                        if not existing_id:
                            await self.db.update_probability_message_id(thread_id, probability_messages[0].id)
                            logger.debug(f"Thread {thread_id}: Probability-Message-ID in DB gespeichert")

                except discord.NotFound:
                    logger.debug(f"Thread {thread_id} nicht mehr gefunden")
                except Exception as e:
                    logger.debug(f"Fehler bei Cleanup für Thread {thread_id}: {e}")

        except Exception as e:
            logger.error(f"Fehler bei Probability-Message-Cleanup: {e}")

        if total_deleted > 0:
            logger.info(f"Cleanup abgeschlossen: {total_deleted} doppelte Nachricht(en) in {threads_cleaned} Thread(s) gelöscht")
        else:
            logger.info("Cleanup abgeschlossen: Keine doppelten Probability-Nachrichten gefunden")
