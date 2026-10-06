"""Überwachung und Wartung: Pool-Wechsel, Scrape-Probleme, Backups."""

import os
import shutil

from bot.common import *  # noqa: F401,F403

WEBAPP_HEALTH_URL = f"http://127.0.0.1:{os.getenv('WEBAPP_PORT') or '8080'}/api/health"
WEBAPP_ALERT_AFTER = 3   # Prüfungen alle 5 Minuten


class MonitoringMixin:
    async def _check_pool_switch(self, banner_count: int):
        """Viele abgefangene Pack-Anstiege auf einmal = die Seite liefert wohl wieder den falschen Pool."""
        rises = self._rises_ignored
        if rises < max(POOL_SWITCH_MIN_RISES, banner_count * 0.2):
            return
        logger.warning(f"[ÜBERWACHUNG] {rises} von {banner_count} Bannern mit gestiegenen Packs - Pool-Wechsel?")
        now = datetime.now()
        if self._last_pool_alert and now - self._last_pool_alert < timedelta(hours=3):
            return
        self._last_pool_alert = now
        await notify_critical_error(
            f"Bei {rises} von {banner_count} Bannern meldet die Seite gerade **mehr** Packs als zuvor.\n"
            f"Das spricht dafür, dass der Bot wieder einen falschen Pack-Pool sieht. Die Anstiege werden "
            f"ignoriert, aber neue Käufe könnten fehlen.\n"
            f"Prüfen: `docker restart gtcha-tor` und danach die Pack-Zahlen mit der Seite vergleichen."
        )

    async def _backup_database(self):
        try:
            target = await asyncio.to_thread(backup_database, self.db.db_path)
            logger.info(f"Datenbank-Backup erstellt: {target}")
            # Datenbank der Web-App (Verknüpfungen, Push-Abos, Verlauf) und ihr Push-Schlüssel
            data_dir = os.path.dirname(os.path.abspath(self.db.db_path))
            app_db = os.path.join(data_dir, "webapp.db")
            if os.path.exists(app_db):
                target = await asyncio.to_thread(backup_database, app_db)
                logger.info(f"App-Backup erstellt: {target}")
            key = os.path.join(data_dir, "webapp_vapid.pem")
            if os.path.exists(key):
                await asyncio.to_thread(shutil.copy2, key, os.path.join(data_dir, "backups", "webapp_vapid.pem"))
        except Exception as e:
            logger.error(f"Datenbank-Backup fehlgeschlagen: {e}")
            await notify_critical_error(f"Datenbank-Backup fehlgeschlagen: {e}")

    async def _check_webapp(self):
        """Läuft die Web-App? Meldung im Admin-Kanal nach 3 Fehlversuchen in Folge (und wenn sie wieder läuft).
        Erst aktiv, nachdem die App einmal erreichbar war - ohne App-Container keine Meldungen."""
        import aiohttp
        try:
            async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=10)) as session:
                async with session.get(WEBAPP_HEALTH_URL) as resp:
                    ok = resp.status == 200 and (await resp.json()).get("ok")
        except Exception:
            ok = False
        if ok:
            if self._webapp_fails >= WEBAPP_ALERT_AFTER:
                await notify_critical_error("✅ Der GTCHA Tracker ist wieder erreichbar.")
            self._webapp_seen, self._webapp_fails = True, 0
            return
        if not self._webapp_seen:
            return
        self._webapp_fails += 1
        if self._webapp_fails == WEBAPP_ALERT_AFTER:
            await notify_critical_error(
                f"Der GTCHA Tracker antwortet seit {WEBAPP_ALERT_AFTER * 5} Minuten nicht.\n"
                f"Prüfen: `docker logs --tail 50 gtcha-app` und `docker compose up -d gtcha-app`")

    async def _report_scrape_problem(self, reason: str):
        """Zählt Probleme in Folge; ab SCRAPE_PROBLEM_ALERT_AFTER einmalig Meldung im Admin-Kanal."""
        self._scrape_problems += 1
        logger.warning(f"[ÜBERWACHUNG] Problem {self._scrape_problems}x in Folge: {reason}")
        if self._scrape_problems == SCRAPE_PROBLEM_ALERT_AFTER:
            self._problem_alerted = True
            await notify_critical_error(
                f"Der Bot hat seit {self._scrape_problems} Scrapes in Folge Probleme.\n"
                f"Letzter Grund: {reason}\n\n"
                f"Prüfen: `docker logs --tail 50 gtcha-tor` und `docker logs --tail 50 gtcha-discord-bot`"
            )

    async def _report_scrape_ok(self):
        if self._problem_alerted:
            await send_notification(
                title="Scrape läuft wieder",
                description=f"Nach {self._scrape_problems} fehlerhaften Scrapes kommen wieder Daten.",
                color=0x2ECC71,
            )
        self._scrape_problems = 0
        self._problem_alerted = False
