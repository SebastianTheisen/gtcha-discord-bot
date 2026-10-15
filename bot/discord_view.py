"""Was Discord zu sehen bekommt - schlank und zeitversetzt (die App und der VPS haben immer alles).

Einstellung (in der App, nur für Admins): Modus "minimal"/"slim"/"full" der normalen Foren, Modus der Premium-Foren
("slim"/"full") und Verzögerung in Minuten. Sind Premium-Foren eingerichtet (PREMIUM_CHANNEL_* in der .env), bekommt
jeder Banner dort einen zweiten Thread; Medaillen gelten für beide (gespeichert am normalen Thread).

Schlank:
  - Startbeitrag ohne Auswertungen, nur Ampel 🟢/🟡/🔴 und "Hits noch drin"; neutraler Thread-Titel
  - Pack-Updates kommen weiter; kein "Lohnt sich", kein Endspurt, keine Hit-Chance, kein Top-10-Kanal
  - automatisch erkannte Hits und in der App gemeldete Medaillen erscheinen erst nach der Verzögerung -
    auch in Hit-Liste und "Hits noch drin" (öffentlicher Stand in discord_public, Posts in discord_outbox)
  - im Thread geschriebene Medaillen ("T2") zählen sofort
Die Datenbank (und damit die App) bekommt jede Änderung sofort.
"""

import time
from contextvars import ContextVar
from datetime import timezone

from bot.common import *  # noqa: F401,F403

# Discord-Pause (Admin in der App): "notice" = statt jedes Updates nur der Hinweis, "off" = gar nichts mehr.
# Gesperrt wird zentral in der HTTP-Schicht von discord.py (jeder schreibende Zugriff); nur der Hinweis selbst und
# Antworten auf Slash-Commands (/tracker-verknüpfen, nur für die Person sichtbar) dürfen durch.
DISCORD_ALLOW = ContextVar("discord_allow", default=False)
# Meldung je Thread in unregelmäßigen Abständen (zufällig 5-25 Min) - Banner mit Updates jede Minute: sonst Flut
PAUSE_NOTICE_MIN, PAUSE_NOTICE_MAX = 5 * 60, 25 * 60


class DiscordPaused(discord.DiscordException):
    """Schreibender Discord-Zugriff während der Discord-Pause (wird nicht als Fehler geloggt)."""

# Foren aus der .env (Stand beim Start) - in der App eingetragene kommen dazu bzw. überschreiben sie
_ENV_CHANNELS = dict(CHANNEL_IDS)
_ENV_PREMIUM = dict(PREMIUM_CHANNEL_IDS)

SETTINGS_CACHE_SECONDS = 20
# Posts, die es im schlanken Modus nicht mehr gibt - beim Umstellen löscht der Bot seine alten
# (Pack-Updates bleiben in Discord - die werden nie gelöscht)
OLD_POST_PREFIXES = ("💰 **Lohnt sich", "@everyone 💰 **Lohnt sich", "⚡ **Endspurt", "@everyone ⚡ **Endspurt",
                     "🎯 **Hit-Chance")
CLEANUP_VERSION = "1"
# minimal: diese eigenen Posts verschwinden - alle Arten aus früheren Versionen: Hit-Listen (auch ohne gespeicherte
# ID), automatisch erkannte Hits, Hit-Chance, "Lohnt sich", Endspurt. Medaillen und Pack-Updates bleiben.
MINIMAL_DROP_PREFIXES = ("🏆 **", "🔥 **", "@everyone 🔥 **", "❓ **Möglicher Hit", "@everyone ❓ **Möglicher Hit",
                         "🎯 **Hit-Chance", "🎯 Hit-Chance") + OLD_POST_PREFIXES
MINIMAL_CLEANUP_VERSION = "2"


def minimal_drop(content: str) -> bool:
    """Eigener Post, den es im minimalen Modus nicht mehr gibt? Admin-Haken ohne Person ebenfalls weg;
    Admin-Korrekturen an Medaillen von Personen ("umgetragen", "entfernt") bleiben."""
    text = content or ""
    if text.startswith(MINIMAL_DROP_PREFIXES):
        return True
    return text.startswith("🛠️ ") and ("als raus abgehakt" in text or "Abhaken aufgehoben" in text)


class DiscordViewMixin:
    async def _discord_pause_state(self, fresh: bool = False) -> str:
        """"on" | "notice" | "off" (aus den App-Einstellungen, 20 s zwischengespeichert)."""
        cached = getattr(self, "_pause", None)
        if fresh or not cached or time.time() - cached["read"] > SETTINGS_CACHE_SECONDS:
            try:
                await self.app_bridge.init()
                cached = {**await self.app_bridge.discord_pause(), "read": time.time()}
            except Exception as e:
                logger.debug(f"Discord-Pause nicht gelesen: {e}")
                cached = cached or {"state": "on", "notice": "", "read": time.time()}
            if getattr(self, "_pause", {}).get("state") != cached["state"]:
                logger.info(f"[DISCORD] Pause: {cached['state']}")
            self._pause = cached
        return cached["state"]

    def _install_discord_guard(self):
        """Jeder schreibende Discord-Zugriff läuft durch http.request - dort während der Pause sperren."""
        if getattr(self, "_guard_installed", False):
            return
        original = self.http.request

        async def guarded(route, **kwargs):
            state = (getattr(self, "_pause", None) or {}).get("state", "on")
            if (state != "on" and route.method != "GET" and not DISCORD_ALLOW.get()
                    and not route.path.startswith(("/interactions", "/webhooks"))):
                raise DiscordPaused(f"Discord-Pause ({state}): {route.method} {route.path}")
            return await original(route, **kwargs)

        self.http.request = guarded
        self._guard_installed = True

    async def _post_pause_notice(self, pack_id: int) -> None:
        """Statt des Updates eine Meldung in die Threads des Banners: eine Zeile des Hinweistexts, zufällig gewählt,
        je Thread in unregelmäßigen Abständen (5-25 Min)."""
        import random
        next_at = self.__dict__.setdefault("_pause_notice_at", {})
        lines = [x.strip() for x in ((getattr(self, "_pause", None) or {}).get("notice") or "").splitlines() if x.strip()]
        if not lines:
            return
        for thread_data, _scope in await self._banner_threads(pack_id):
            tid = int(thread_data["thread_id"])
            if time.time() < next_at.get(tid, 0):
                continue
            next_at[tid] = time.time() + random.uniform(PAUSE_NOTICE_MIN, PAUSE_NOTICE_MAX)
            text = random.choice(lines)
            token = DISCORD_ALLOW.set(True)
            try:
                thread = await self._open_thread(tid)
                if thread is not None:
                    await discord_rate_limiter.acquire("message_send")
                    await thread.send(text)
            except Exception as e:
                logger.debug(f"[DISCORD] Hinweis in {tid} nicht gepostet: {e}")
            finally:
                DISCORD_ALLOW.reset(token)

    async def _discord_paused(self, pack_id: int, notice: bool = True) -> bool:
        """True, wenn Discord pausiert ist (dann ggf. Hinweis statt des Updates). Aufrufer tun so, als sei gepostet,
        damit Pack-Zahlen, Banner und Medaillen in der Datenbank (und damit in den Apps) weiterlaufen."""
        state = await self._discord_pause_state()
        if state == "on":
            return False
        if state == "notice" and notice:
            await self._post_pause_notice(pack_id)
        return True

    async def _apply_forum_ids(self):
        """In der App eingetragene Discord-Foren übernehmen (zusätzlich zur .env, die App gewinnt). Neu eingetragenes
        normales Forum: laufende Banner der Kategorie dort nachposten."""
        try:
            forums = await self.app_bridge.forum_ids()
        except Exception as e:
            logger.debug(f"Foren aus der App nicht lesbar: {e}")
            return
        added = []
        for target, env, kind in ((CHANNEL_IDS, _ENV_CHANNELS, "main"), (PREMIUM_CHANNEL_IDS, _ENV_PREMIUM, "premium")):
            wanted = {**env, **{c: int(ids[kind]) for c, ids in forums.items() if ids.get(kind)}}
            for cat in set(target) | set(wanted):
                new = wanted.get(cat, 0)
                if target.get(cat, 0) != new:
                    logger.info(f"[FOREN] {kind} {cat}: {target.get(cat, 0)} -> {new}")
                    if new and kind == "main":
                        added.append(cat)
                    target[cat] = new
        for pid in await self.db.thread_missing(added):
            row = await self.db.get_banner(pid)
            if not row:
                continue
            banner = ScrapedBanner(
                pack_id=pid, category=row['category'], title=row.get('title'), best_hit=row.get('best_hit'),
                price_coins=row.get('price_coins'), current_packs=row.get('current_packs'),
                total_packs=row.get('total_packs'), entries_per_day=row.get('entries_per_day'),
                sale_end_date=row.get('sale_end_date'), image_url=row.get('image_url'),
                detail_page_url=row.get('detail_page_url'))
            await self._post_banner_to_discord(banner, starts_at=row.get('starts_at') if not row.get('start_announced') else None)
            logger.info(f"[FOREN] Banner {pid} ({row['category']}) im neuen Forum gepostet")

    async def _settings_view(self) -> dict:
        """Einstellungen aus der App ({"mode", "slim", "minimal", "delay", "delay_minutes", "premium_mode"}),
        kurz zwischengespeichert."""
        cached = getattr(self, "_view_cache", None)
        if cached and time.monotonic() - cached[0] < SETTINGS_CACHE_SECONDS:
            return cached[1]
        try:
            await self.app_bridge.init()
            view = await self.app_bridge.discord_view()
        except Exception as e:
            logger.debug(f"Discord-Einstellungen nicht lesbar: {e}")
            view = {"mode": "minimal", "slim": True, "minimal": True, "delay": 30 * 60, "delay_minutes": 30,
                    "premium_mode": "full"}
        self._view_cache = (time.monotonic(), view)
        return view

    @staticmethod
    def _premium_enabled() -> bool:
        return any(PREMIUM_CHANNEL_IDS.values())

    async def _view(self, scope: str = "main") -> dict:
        """Was ein Thread zeigt: scope "main" = normales Forum (Einstellung discord_mode), "premium" = Premium-Forum
        (premium_mode, immer mit der eingestellten Verzögerung)."""
        view = await self._settings_view()
        delay = view.get("delay_minutes", 0) * 60
        if scope == "premium":
            mode = view.get("premium_mode") or "full"
            return {**view, "mode": mode, "slim": mode == "slim", "minimal": False, "delay": delay}
        if self._premium_enabled():
            view = {**view, "delay": delay}   # ein gemeinsamer öffentlicher Stand für beide Foren
        return view

    async def _slim(self, scope: str = "main") -> bool:
        return (await self._view(scope))["slim"]

    async def _minimal(self, scope: str = "main") -> bool:
        """Minimal: Discord nur noch mit Grundinfos, Pack-Updates und Medaillen von euch - alles andere in der App."""
        return bool((await self._view(scope)).get("minimal"))

    async def _delay(self) -> int:
        """Verzögerung (Sekunden) für erkannte Hits und Tracker-Medaillen - gilt für alle Foren gemeinsam."""
        return (await self._view())["delay"]

    # --- Threads eines Banners (normal + Premium) ---
    async def _banner_threads(self, pack_id: int) -> list:
        """[(thread_data, scope)] der aktiven Threads eines Banners: "main" (normales Forum), "premium"."""
        out = []
        main = await self.db.get_thread_by_banner_id(pack_id)
        if main and not main.get("is_expired"):
            out.append((main, "main"))
        if self._premium_enabled():
            premium = await self.db.get_premium_thread(pack_id)
            if premium and not premium.get("is_expired"):
                out.append((premium, "premium"))
        return out

    async def _medal_thread(self, thread_id: int) -> int:
        """Medaillen hängen am normalen Thread - für einen Premium-Thread dessen ID."""
        if not self._premium_enabled():
            return int(thread_id)
        premium = await self.db.get_premium_thread_by_id(int(thread_id))
        if not premium:
            return int(thread_id)
        main = await self.db.get_thread_by_banner_id(premium["banner_id"])
        return int(main["thread_id"]) if main else int(thread_id)

    async def _open_thread(self, thread_id: int):
        """Thread holen und ggf. entarchivieren (sonst kann der Bot nicht posten); None, wenn es ihn nicht gibt."""
        try:
            thread = self.get_channel(int(thread_id)) or await self.fetch_channel(int(thread_id))
        except discord.NotFound:
            return None
        if not isinstance(thread, discord.Thread):
            return None
        if thread.archived:
            await discord_rate_limiter.acquire("thread_edit")
            await thread.edit(archived=False)
        return thread

    async def _post_to_threads(self, pack_id: int, text: str, scopes=None, skip_minimal: bool = False,
                               exclude: Optional[int] = None, reaction: Optional[tuple] = None, **kwargs) -> bool:
        """Text in alle Threads des Banners posten (scopes: nur diese Foren, skip_minimal: nicht ins minimale Forum,
        exclude: diesen Thread auslassen). reaction = (Emoji, hinzufügen?) am Startbeitrag. True = mind. ein Post."""
        if await self._discord_paused(pack_id, notice=bool(text)):
            return True   # Discord-Pause: Hinweis statt des Posts, alles andere läuft weiter
        posted = False
        for thread_data, scope in await self._banner_threads(pack_id):
            if (scopes and scope not in scopes) or int(thread_data["thread_id"]) == exclude:
                continue
            if skip_minimal and await self._minimal(scope):
                continue
            try:
                thread = await self._open_thread(thread_data["thread_id"])
                if thread is None:
                    continue
                if reaction:
                    await self._set_starter_reaction(thread, thread_data, reaction[0], add=reaction[1])
                if text:
                    await discord_rate_limiter.acquire("message_send")
                    await thread.send(text, **kwargs)
                posted = True
            except Exception as e:
                logger.warning(f"[DISCORD] Post in Thread {thread_data['thread_id']} ({scope}) fehlgeschlagen: {e}")
        return posted

    # --- Öffentlicher Stand (was Discord schon wissen darf) ---
    async def _public_pull_tracking(self, pack_id: int) -> dict:
        state = await self.db.get_pull_tracking(pack_id)
        public = await self.db.get_public_pulls(pack_id)
        return {**state, **public} if public else state

    async def _public_medals(self, thread_id: int) -> dict:
        thread_id = await self._medal_thread(thread_id)
        delay = await self._delay()
        if not delay:
            return await self.db.get_medals(int(thread_id))
        since = (datetime.now() - timedelta(seconds=delay)).isoformat()
        return await self.db.get_medals(int(thread_id), hide_app_since=since)

    async def _publish_pulls(self, pack_id: int, thread_id: int, before: dict, text: Optional[str],
                             immediate: bool = False):
        """Neuer echter Stand ist gespeichert. Ohne Verzögerung sofort öffentlich (und Post), sonst den alten
        Stand für Discord festhalten und Post + neuen Stand in die Warteschlange legen."""
        delay = 0 if immediate else await self._delay()
        if not delay:
            if not await self.db.pending_discord(pack_id):
                await self.db.set_public_pulls(pack_id, None)
            if text:   # nicht ins minimale Forum: erkannte Hits dort nur in der App
                await self._post_to_threads(pack_id, text, skip_minimal=True)
            return
        await self.db.set_public_pulls(pack_id, before["pulled"], before["unsure"], only_if_missing=True)
        now_state = await self.db.get_pull_tracking(pack_id)
        await self.db.queue_discord("pulls", pack_id, thread_id,
                                    {"pulled": now_state["pulled"], "unsure": now_state["unsure"], "text": text},
                                    time.time() + delay)
        logger.info(f"[DISCORD] Hit-Erkennung bei {pack_id} erscheint in {delay // 60} Min")

    async def _process_discord_outbox(self):
        """Fällige zeitversetzte Posts senden und den öffentlichen Stand nachziehen (läuft alle 30 s)."""
        try:
            for item in await self.db.due_discord(time.time()):
                pid, tid, data = item["pack_id"], item["thread_id"], item["payload"]
                try:
                    if item["kind"] == "pulls":
                        await self.db.set_public_pulls(pid, data["pulled"], data["unsure"])
                        if data.get("text"):
                            await self._post_to_threads(pid, data["text"], skip_minimal=True)
                    elif item["kind"] == "app_medal":
                        await self._post_to_threads(
                            pid, data["text"], reaction=(data["emoji"], data.get("add", True)) if data.get("emoji") else None,
                            **({"allowed_mentions": discord.AllowedMentions.none()} if data.get("silent") else {}))
                except Exception as e:
                    logger.warning(f"[DISCORD] Zeitversetzter Post {item['id']} fehlgeschlagen: {e}")
                await self.db.done_discord(item["id"])
                if not await self.db.pending_discord(pid):
                    await self.db.set_public_pulls(pid, None)    # alles nachgeholt: wieder echter Stand
                await self._refresh_pool_views(pid)
                await self._sync_thread_title(pid)
        except Exception as e:
            logger.warning(f"[DISCORD] Warteschlange nicht abgearbeitet: {e}")

    # --- Ankündigungen aus dem Admin-Bereich der App ---
    async def _process_announcements(self):
        """Fällige Ankündigungen einmal in alle laufenden Threads posten (läuft alle 30 s). Jeder gepostete Thread
        wird sofort gemerkt - nach einem Neustart geht es dort weiter, ohne doppelt zu posten. Mit "auch bei neuen
        Bannern" kommen danach neue Threads dazu, bis der Admin die Ankündigung beendet."""
        try:
            await self.app_bridge.init()
            due = await self.app_bridge.due_announcements(time.time())
            if not due:
                return
            pids = list(await self.db.get_active_banners())
            for ann in due:
                posted = list(ann["posted"])
                before = len(posted)
                text = ("@everyone\n" if ann.get("mention") else "") + ann["text"]
                mentions = discord.AllowedMentions(everyone=bool(ann.get("mention")), users=False, roles=False)
                if ann["status"] != "sent":
                    logger.info(f"[ANKÜNDIGUNG] {ann['id']}: poste in {ann['scope']} ({len(pids)} Banner)")
                for pid in pids:
                    for thread_data, scope in await self._banner_threads(pid):
                        tid = int(thread_data["thread_id"])
                        if tid in posted or (ann["scope"] != "all" and scope != ann["scope"]):
                            continue
                        try:
                            thread = await self._open_thread(tid)
                            if thread is None:
                                continue
                            await discord_rate_limiter.acquire("message_send")
                            await thread.send(text, allowed_mentions=mentions)
                        except Exception as e:
                            logger.warning(f"[ANKÜNDIGUNG] Thread {tid} fehlgeschlagen: {e}")
                            continue
                        posted.append(tid)
                        await self.app_bridge.announcement_progress(ann["id"], posted, done=False)
                if ann["status"] == "sent" and len(posted) == before:
                    continue   # "auch bei neuen Bannern": kein neuer Thread dazugekommen
                await self.app_bridge.announcement_progress(ann["id"], posted, done=True)
                logger.info(f"[ANKÜNDIGUNG] {ann['id']}: in {len(posted) - before} Threads gepostet "
                            f"({len(posted)} insgesamt)")
        except Exception as e:
            logger.warning(f"[ANKÜNDIGUNG] nicht abgearbeitet: {e}")

    # --- Umstellen: alte Posts der wegfallenden Arten löschen ---
    async def _cleanup_old_posts(self):
        """Einmalig nach dem Umstellen auf schlank: eigene "Lohnt sich"-, Endspurt- und
        Hit-Chance-Nachrichten in allen aktiven Threads löschen; Top-10-Thread entfernen."""
        try:
            if not await self._slim() or await self.db.get_meta("slim_cleanup") == CLEANUP_VERSION:
                return
            rows = await self.db.get_active_banners()
            deleted = 0
            for pid in rows:
                thread_data = await self.db.get_thread_by_banner_id(pid)
                if not thread_data or thread_data.get("is_expired"):
                    continue
                try:
                    thread = self.get_channel(int(thread_data["thread_id"])) or \
                        await self.fetch_channel(int(thread_data["thread_id"]))
                except discord.NotFound:
                    continue
                if not isinstance(thread, discord.Thread):
                    continue
                old = [msg async for msg in thread.history(limit=None)
                       if msg.author.id == self.user.id and (msg.content or "").startswith(OLD_POST_PREFIXES)]
                deleted += await self._delete_messages(thread, old)
                await self.db.update_probability_message_id(int(thread_data["thread_id"]), None)
            hot_id = await self.db.get_meta("hot_thread_id")
            if hot_id:
                try:
                    hot = self.get_channel(int(hot_id)) or await self.fetch_channel(int(hot_id))
                    await hot.delete()
                except Exception as e:
                    logger.debug(f"Top-10-Thread nicht gelöscht: {e}")
                await self.db.set_meta("hot_thread_id", "")
            await self.db.set_meta("slim_cleanup", CLEANUP_VERSION)
            logger.info(f"[DISCORD] Schlanker Modus: {deleted} alte Posts gelöscht")
        except Exception as e:
            logger.warning(f"[DISCORD] Aufräumen fehlgeschlagen (wird beim nächsten Start erneut versucht): {e}")

    async def _watch_discord_mode(self):
        """In der App umgeschaltet (schlank/voll)? Dann alle Startbeiträge, Titel und Hit-Listen neu zeichnen
        und beim Wechsel auf schlank die alten Posts löschen."""
        if await self._discord_pause_state(fresh=True) != "on":
            return   # Discord-Pause: nichts neu zeichnen, keine Foren nachrüsten
        await self._apply_forum_ids()
        try:
            view = await self._settings_view()
            mode = view["mode"]
            # einmalig: bisher "schlank" -> neuer Standard "minimal" (Discord nur noch Grundinfos und Medaillen)
            if mode == "slim" and await self.db.get_meta("minimal_default") != "1":
                await self.app_bridge.set_setting("discord_mode", "minimal")
                self._view_cache = None
                mode = "minimal"
            await self.db.set_meta("minimal_default", "1")
            applied = await self.db.get_meta("discord_mode_applied")
            if not applied:
                # erster Start mit dieser Version: Startbeiträge zeichnet schon der EMBED_VERSION-Wechsel neu
                await self.db.set_meta("discord_mode_applied", mode)
            elif applied != mode:
                logger.info(f"[DISCORD] Ansicht umgestellt auf {mode} - zeichne alle Threads neu")
                await self.db.set_meta("discord_mode_applied", mode)
                if mode == "full":
                    await self.db.set_meta("slim_cleanup", "")      # beim nächsten Wechsel wieder aufräumen
                    for pid in await self.db.get_all_active_banner_ids():
                        if not await self.db.pending_discord(pid):
                            await self.db.set_public_pulls(pid, None)
                await self.db.set_meta("embed_version", "")
                await self._refresh_all_embeds()
            if self._premium_enabled():
                premium = view.get("premium_mode") or "full"
                applied_premium = await self.db.get_meta("premium_mode_applied")
                await self.db.set_meta("premium_mode_applied", premium)
                if applied_premium and applied_premium != premium:
                    logger.info(f"[DISCORD] Premium-Ansicht umgestellt auf {premium} - zeichne alle Threads neu")
                    await self.db.set_meta("embed_version", "")
                    await self._refresh_all_embeds()
            if mode in ("slim", "minimal"):
                await self._cleanup_old_posts()
            if mode == "minimal":
                await self._cleanup_minimal_posts()
        except Exception as e:
            logger.warning(f"[DISCORD] Ansicht nicht übernommen: {e}")

    async def _cleanup_minimal_posts(self):
        """Einmalig nach dem Umstellen auf minimal: in allen aktiven Threads Hit-Listen, Posts zu automatisch
        erkannten Hits und Admin-Haken löschen; Startbeiträge ohne Auswertung neu zeichnen. Medaillen-Posts von
        euch und Pack-Updates bleiben."""
        try:
            if await self.db.get_meta("minimal_cleanup") == MINIMAL_CLEANUP_VERSION:
                return
            deleted = 0
            for pid in await self.db.get_active_banners():
                thread_data = await self.db.get_thread_by_banner_id(pid)
                if not thread_data or thread_data.get("is_expired"):
                    continue
                try:
                    thread = self.get_channel(int(thread_data["thread_id"])) or \
                        await self.fetch_channel(int(thread_data["thread_id"]))
                except discord.NotFound:
                    continue
                if not isinstance(thread, discord.Thread):
                    continue
                hit_ids = {int(i) for i in json.loads(thread_data.get("hit_message_ids") or "null") or []}
                if thread_data.get("top5_message_id"):
                    hit_ids.add(int(thread_data["top5_message_id"]))
                old = [msg async for msg in thread.history(limit=None)
                       if msg.author.id == self.user.id and (msg.id in hit_ids or minimal_drop(msg.content))]
                deleted += await self._delete_messages(thread, old)
                await self.db.set_hit_message_ids(int(thread_data["thread_id"]), [])
                await self.db.set_hit_list_sig(int(thread_data["thread_id"]), None)
            await self.db.set_meta("embed_version", "")
            await self._refresh_all_embeds()
            await self.db.set_meta("minimal_cleanup", MINIMAL_CLEANUP_VERSION)
            logger.info(f"[DISCORD] Minimal: {deleted} Hit-Listen und Erkennungs-Posts gelöscht, Startbeiträge gekürzt")
        except Exception as e:
            logger.warning(f"[DISCORD] Minimal-Aufräumen fehlgeschlagen (nächster Start versucht es erneut): {e}")

    async def _delete_messages(self, thread, messages: list) -> int:
        """Jüngere als 14 Tage gesammelt löschen (bis 100 auf einmal - ein Aufruf statt 100, kaum Wartezeiten
        durch Discord), ältere einzeln (das erlaubt Discord nur so)."""
        limit = datetime.now(timezone.utc) - timedelta(days=13, hours=12)
        recent = [m for m in messages if m.created_at > limit]
        older = [m for m in messages if m.created_at <= limit]
        deleted = 0
        for i in range(0, len(recent), 100):
            chunk = recent[i:i + 100]
            await discord_rate_limiter.acquire("message_delete")
            try:
                if len(chunk) == 1:
                    await chunk[0].delete()
                else:
                    await thread.delete_messages(chunk)
                deleted += len(chunk)
            except discord.NotFound:
                pass
            except discord.HTTPException as e:   # z. B. eine Nachricht schon weg - dann einzeln
                logger.debug(f"[DISCORD] Sammel-Löschen fehlgeschlagen, einzeln: {e}")
                older += chunk
        for msg in older:
            await discord_rate_limiter.acquire("message_delete")
            try:
                await msg.delete()
                deleted += 1
            except discord.NotFound:
                pass
        return deleted

    async def _remember_owner_as_admin(self):
        """Admin-Tabelle = genau APP_ADMIN_IDS aus der .env (nur Anzeige/Protokoll; die App prüft die .env selbst).
        Leer = niemand ist Admin. Alle anderen Einträge fliegen raus."""
        from config import APP_ADMIN_IDS
        try:
            await self.app_bridge.init()
            await self.app_bridge.set_admins(list(APP_ADMIN_IDS))
            if APP_ADMIN_IDS:
                logger.info(f"[APP] App-Admins: {', '.join(APP_ADMIN_IDS)}")
            else:
                logger.warning("[APP] Kein App-Admin: APP_ADMIN_IDS in der .env ist leer")
        except Exception as e:
            logger.warning(f"[APP] Admins nicht gesetzt: {e}")
