"""
Discord Bot Client - Forum-Channel Version

Die Logik ist auf Mixins verteilt (bot/scraping.py, threads.py, hits.py, medals.py,
monitoring.py, hot_banner.py); gemeinsame Importe und Konstanten stehen in bot/common.py.
"""

from bot.common import *  # noqa: F401,F403
from bot.scraping import ScrapingMixin
from bot.monitoring import MonitoringMixin
from bot.threads import ThreadsMixin
from bot.hits import HitsMixin
from bot.medals import MedalsMixin
from bot.hot_banner import HotBannerMixin
from bot.fast_poll import FastPollMixin
from bot.app_link import AppLinkMixin


class GTCHABot(FastPollMixin, ScrapingMixin, MonitoringMixin, ThreadsMixin, HitsMixin, MedalsMixin, HotBannerMixin,
              AppLinkMixin, commands.Bot):
    def __init__(self):
        intents = discord.Intents.default()
        intents.message_content = True
        intents.guilds = True

        super().__init__(
            command_prefix="!",
            intents=intents,
        )

        self.db = Database()
        self.scheduler = AsyncIOScheduler()
        self._scraper: Optional[GTCHAScraper] = None
        self._scrape_lock = asyncio.Lock()  # Verhindert parallele Scrape-Läufe
        self._last_full_scrape: Optional[datetime] = None
        self._scrape_problems = 0
        self._problem_alerted = False
        self._main_scrape_running = False
        self._rises_ignored = 0
        self._last_pool_alert: Optional[datetime] = None

    async def setup_hook(self):
        """Setup beim Start."""
        await self.db.init()
        logger.info(f"Datenbank initialisiert: {self.db.db_path}")

        # Slash Commands registrieren
        self.tree.add_command(app_commands.Command(
            name="refresh",
            description="Manuelles Scraping starten",
            callback=self.refresh_command
        ))
        self.tree.add_command(app_commands.Command(
            name="status",
            description="Bot-Status anzeigen",
            callback=self.status_command
        ))
        self.tree.add_command(app_commands.Command(
            name="hotbanner",
            description="Hot-Banner manuell aktualisieren",
            callback=self.hotbanner_command
        ))
        self.tree.add_command(app_commands.Command(
            name="app-verknüpfen",
            description="Code, um die GTCHA-Tracker-App mit deinem Discord-Konto zu verknüpfen",
            callback=self.app_link_command
        ))

        # Scheduler starten (mit Timeout-Wrapper)
        # Läuft alle X Minuten um xx:00:20, xx:05:20, xx:10:20, etc.
        # (20 Sekunden nach der vollen Minute, da neue Banner um :00 und :30 kommen)
        self.scheduler.add_job(
            self._scrape_with_timeout,
            'cron',
            minute=f'*/{SCRAPE_INTERVAL_MINUTES}',  # Intervall aus Config
            second=20,     # 20 Sekunden nach der Minute
            id='scrape_job',
            replace_existing=True,
            coalesce=True,  # Verpasste Jobs zusammenfassen
            max_instances=1,  # Maximal eine Instanz gleichzeitig
            misfire_grace_time=SCRAPE_INTERVAL_MINUTES * 60,  # Grace Time = Intervall
        )
        # In der Web-App gemeldete Medaillen abarbeiten
        self.scheduler.add_job(
            self._process_app_requests, 'interval', seconds=5,
            id='app_requests_job', replace_existing=True, coalesce=True, max_instances=1,
        )
        self.scheduler.add_job(
            self._backup_database, 'cron', hour=3, minute=30,
            id='db_backup_job', replace_existing=True, coalesce=True, max_instances=1,
        )
        self.scheduler.start()
        # Wächter: ohne erfolgreichen Scrape in 20 Min wird der Prozess neu gestartet
        start_watchdog(max_silence_seconds=20 * 60, startup_grace_seconds=20 * 60)
        logger.info(f"Scheduler: Alle {SCRAPE_INTERVAL_MINUTES} Min um xx:xx:20")

        if HOT_BANNER_CHANNEL_ID and HOT_BANNER_ENABLED:
            logger.info("Hot-Banner: Rangliste wird nach jedem Scrape abgeglichen")

        # Archiv-Bereinigung: Alle 30 Min alte archivierte Daten löschen
        self.scheduler.add_job(
            self._purge_archived_data,
            'interval',
            minutes=30,
            id='purge_archived_job',
            replace_existing=True,
            coalesce=True,
            max_instances=1,
        )
        logger.info("Archiv-Bereinigung Scheduler: Alle 30 Min (löscht Daten älter als 1 Stunde)")

        # Täglicher Auto-Restart (Railway)
        if DAILY_RESTART_TIME:
            try:
                hour, minute = DAILY_RESTART_TIME.split(":")
                self.scheduler.add_job(
                    self._daily_restart,
                    'cron',
                    hour=int(hour),
                    minute=int(minute),
                    id='daily_restart_job',
                    replace_existing=True,
                )
                logger.info(f"Daily Restart Scheduler: Täglich um {DAILY_RESTART_TIME} UTC")
            except ValueError:
                logger.error(f"Ungültiges DAILY_RESTART_TIME Format: '{DAILY_RESTART_TIME}' (erwartet HH:MM)")

        # Commands synchronisieren
        if GUILD_ID:
            guild = discord.Object(id=int(GUILD_ID))
            self.tree.copy_global_to(guild=guild)
            await self.tree.sync(guild=guild)
            logger.info("Slash Commands synchronisiert")

    async def on_ready(self):
        logger.info(f"Bot online: {self.user}")

        # Notification-System initialisieren
        set_bot_client(self)

        # Memory-Monitor starten
        await memory_monitor.start()

        # Startup-Benachrichtigung senden
        await notify_bot_started()

        # Nur die Thread-Wiederherstellung muss vor dem ersten Scrape fertig sein
        # (sonst entstehen doppelte Threads); der Rest läuft parallel zum Scrape.
        await self._recover_threads_from_discord()

        # Erster Scrape sofort - über Scheduler triggern statt direkt aufrufen,
        # das vermeidet Konflikte mit dem regulären Scheduler-Job
        self.scheduler.modify_job('scrape_job', next_run_time=datetime.now())
        self._start_fast_poll()

        self._startup_tasks = asyncio.gather(
            self._migrate_then_sync_medals(),
            self._cleanup_duplicate_probability_messages(),
            self._refresh_all_embeds_once(),
            return_exceptions=True,
        )

    async def _daily_restart(self):
        """Beendet den Bot-Prozess für einen automatischen Neustart (Railway)."""
        logger.warning("Täglicher Auto-Restart wird ausgeführt...")
        try:
            from utils.notifications import notify_critical_error
            await notify_critical_error("Geplanter täglicher Neustart wird durchgeführt.")
        except Exception:
            pass
        await asyncio.sleep(2)
        logger.warning("Prozess wird mit Exit-Code 1 beendet - Railway startet automatisch neu.")
        os._exit(1)
