"""Web-App: Discord-Verknüpfung (/tracker-verknüpfen) und Medaillen, die in der App gemeldet werden."""

import os
import time

from bot.common import *  # noqa: F401,F403
from database.db import STORE, store_thread_id
from config import DATABASE_PATH
from utils.app_bridge import CODE_MINUTES, AppBridge

APP_REQUEST_SECONDS = 5


class AppLinkMixin:
    @property
    def app_bridge(self) -> AppBridge:
        if not hasattr(self, "_app_bridge"):
            self._app_bridge = AppBridge(os.path.join(os.path.dirname(str(DATABASE_PATH)) or ".", "webapp.db"))
        return self._app_bridge

    async def app_link_command(self, interaction: discord.Interaction):
        """Code für die Web-App, nur für dich sichtbar."""
        await self.app_bridge.init()
        name = interaction.user.display_name
        if await self.app_bridge.is_blocked(interaction.user.id):
            await interaction.response.send_message("⛔ Du bist für den GTCHA Tracker gesperrt.", ephemeral=True)
            return
        code = await self.app_bridge.create_code(interaction.user.id, name)
        await interaction.response.send_message(
            f"🔗 Dein Code für den GTCHA Tracker: **`{code}`**\n"
            f"Im Tracker unter **Ich → Discord verknüpfen** eingeben. Gültig {CODE_MINUTES} Minuten, "
            f"nur einmal benutzbar. Danach kannst du im Tracker Hits melden – sie erscheinen hier als {name}.",
            ephemeral=True)
        logger.info(f"App-Code erstellt für {name}")

    async def _process_app_requests(self):
        """Medaillen aus der App abarbeiten (läuft alle paar Sekunden)."""
        try:
            await self.app_bridge.init()
            for req in await self.app_bridge.pending():
                try:
                    ok, reason = await self._apply_app_medal(req)
                except Exception as e:
                    ok, reason = False, f"Fehler: {e}"
                    logger.warning(f"App-Medaille {req['id']} fehlgeschlagen: {e}")
                await self.app_bridge.finish(req["id"], ok, reason)
        except Exception as e:
            logger.warning(f"App-Meldungen nicht abgearbeitet: {e}")

    async def _apply_app_medal(self, req: dict) -> tuple:
        """(ok, Grund). Gleiche Regeln wie eine Medaille im Discord-Thread."""
        pack_id, tier, user_id = int(req["pack_id"]), str(req["tier"]).upper(), int(req["discord_user_id"])
        if req["action"] in ("admin_reject", "admin_unreject"):
            # Admin: automatische Erkennung als falsch markieren / wieder zulassen (Feld "tier" = Kartenschlüssel)
            await self.db.set_reject(pack_id, str(req["tier"]), req["action"] == "admin_reject")
            logger.info(f"Admin: Erkennung {req['tier']} bei {pack_id} "
                        f"{'als falsch markiert' if req['action'] == 'admin_reject' else 'wieder zugelassen'}")
            if (await self.db.get_banner(pack_id) or {}).get("is_active") == 1:
                await self._refresh_pool_views(pack_id)
            return True, None
        if not re.fullmatch(r"T([1-9]\d?)", tier):
            return False, "Ungültige Medaille"
        if (await self.db.get_banner(pack_id) or {}).get("is_active") == STORE:
            return await self._apply_store_medal(req, pack_id, tier, user_id)
        thread_data = await self.db.get_thread_by_banner_id(pack_id)
        if not thread_data or thread_data.get("is_expired"):
            return False, "Zu diesem Banner gibt es keinen aktiven Thread"
        thread_id = int(thread_data["thread_id"])
        thread = self.get_channel(thread_id) or await self.fetch_channel(thread_id)
        if thread.archived:
            await discord_rate_limiter.acquire("thread_edit")
            await thread.edit(archived=False)
        emoji = MEDAL_EMOJIS.get(tier, MEDAL_EMOJI_DEFAULT)
        existing = await self.db.get_medal(thread_id, tier)

        if req["action"] == "admin_mark":
            # Admin hakt ab ("Hit ist raus"), ohne Person - in Discord als Admin-Korrektur
            if existing:
                return False, f"{tier} ist schon vergeben"
            problem = await self._invalid_medal_reason(pack_id, tier)
            if problem:
                return False, problem.replace("❌ ", "")
            await self.db.save_medal(thread_id, tier, 0, source="admin")
            await self._set_starter_reaction(thread, thread_data, emoji, add=True)
            await discord_rate_limiter.acquire("message_send")
            await thread.send(f"🛠️ {tier} als raus abgehakt *(Admin)*", allowed_mentions=discord.AllowedMentions.none())
            logger.info(f"Admin-Korrektur: {tier} bei {pack_id} abgehakt (ohne Person)")
        elif req["action"] in ("admin_remove", "admin_assign"):
            # Korrektur durch den Admin (in der App geprüft): sofort, auch in Discord
            if req["action"] == "admin_remove":
                if not existing:
                    return False, f"{tier} ist nicht vergeben"
                await self.db.delete_medal(thread_id, tier)
                await self._set_starter_reaction(thread, thread_data, emoji, add=False)
                text = (f"🛠️ {tier} von <@{existing.get('user_id')}> entfernt *(Admin)*" if existing.get('user_id')
                        else f"🛠️ {tier}: Abhaken aufgehoben *(Admin)*")
            else:
                problem = await self._invalid_medal_reason(pack_id, tier)
                if problem:
                    return False, problem.replace("❌ ", "")
                if existing:
                    await self.db.delete_medal(thread_id, tier)
                await self.db.save_medal(thread_id, tier, user_id)
                if not existing:
                    await self._set_starter_reaction(thread, thread_data, emoji, add=True)
                text = f"🛠️ {tier} an <@{user_id}> umgetragen *(Admin)*"
            await discord_rate_limiter.acquire("message_send")
            await thread.send(text, allowed_mentions=discord.AllowedMentions.none())
            logger.info(f"Admin-Korrektur: {req['action']} {tier} bei {pack_id}")
        elif req["action"] == "unclaim":
            if not existing:
                return False, f"{tier} ist nicht vergeben"
            if int(existing.get("user_id") or 0) != user_id:
                return False, f"{tier} hat jemand anderes gemeldet"
            await self.db.delete_medal(thread_id, tier)
            await self._post_app_medal(thread, thread_data, pack_id, emoji, add=False, silent=True,
                                       text=f"↩️ {tier} von <@{user_id}> zurückgenommen")
            logger.info(f"App-Medaille zurückgenommen: {tier} von {req['discord_name']} bei {pack_id}")
        else:
            problem = await self._invalid_medal_reason(pack_id, tier)
            if problem:
                return False, problem.replace("❌ ", "")
            if existing:
                return False, f"{tier} ist schon vergeben"
            await self.db.save_medal(thread_id, tier, user_id, source="app")
            await self._post_app_medal(thread, thread_data, pack_id, emoji, add=True, silent=False,
                                       text=f"{emoji} {tier} geht an <@{user_id}>!")
            logger.info(f"App-Medaille: {tier} an {req['discord_name']} bei {pack_id}")

        await self._update_probability_message(thread_id, pack_id)
        await self._refresh_pool_views(pack_id)
        return True, None

    async def _apply_store_medal(self, req: dict, pack_id: int, tier: str, user_id: int) -> tuple:
        """Store-Pack: dieselben Regeln wie bei normalen Bannern, aber nichts in Discord."""
        medal_thread = store_thread_id(pack_id)
        existing = await self.db.get_medal(medal_thread, tier)
        action = req["action"]
        if action in ("unclaim", "admin_remove"):
            if not existing:
                return False, f"{tier} ist nicht vergeben"
            if action == "unclaim" and int(existing.get("user_id") or 0) != user_id:
                return False, f"{tier} hat jemand anderes gemeldet"
            await self.db.delete_medal(medal_thread, tier)
        else:   # claim / admin_assign / admin_mark
            problem = await self._invalid_medal_reason(pack_id, tier)
            if problem:
                return False, problem.replace("❌ ", "")
            if existing and action in ("claim", "admin_mark"):
                return False, f"{tier} ist schon vergeben"
            if existing:
                await self.db.delete_medal(medal_thread, tier)
            await self.db.save_medal(medal_thread, tier, user_id, source="admin" if action == "admin_mark" else "app")
        logger.info(f"Store-Medaille: {action} {tier} bei {pack_id} ({req.get('discord_name')})")
        return True, None

    async def _post_app_medal(self, thread, thread_data: dict, pack_id: int, emoji: str, add: bool, silent: bool,
                              text: str):
        """In der App gemeldet: in der App sofort, in Discord nach der eingestellten Verzögerung."""
        delay = (await self._view())["delay"]
        if delay:
            await self.db.queue_discord("app_medal", pack_id, thread.id,
                                        {"text": text, "emoji": emoji, "add": add, "silent": silent}, time.time() + delay)
            return
        await self._set_starter_reaction(thread, thread_data, emoji, add=add)
        await discord_rate_limiter.acquire("message_send")
        await thread.send(text, **({"allowed_mentions": discord.AllowedMentions.none()} if silent else {}))

    async def _set_starter_reaction(self, thread, thread_data: dict, emoji: str, add: bool):
        starter_id = thread_data.get("starter_message_id")
        if not starter_id:
            return
        try:
            msg = await thread.fetch_message(int(starter_id))
            if add:
                await msg.add_reaction(emoji)
            else:
                await msg.remove_reaction(emoji, self.user)
        except Exception as e:
            logger.debug(f"Reaktion am Startbeitrag nicht geändert: {e}")
