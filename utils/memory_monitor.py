"""
Memory Monitor - Überwacht Speicherverbrauch und warnt bei hoher Auslastung
"""

import os
import asyncio
from loguru import logger

# psutil ist optional - falls nicht installiert, wird Monitoring deaktiviert
try:
    import psutil
    PSUTIL_AVAILABLE = True
except ImportError:
    PSUTIL_AVAILABLE = False
    logger.warning("psutil nicht installiert - Memory-Monitoring deaktiviert")


class MemoryMonitor:
    """
    Überwacht den Speicherverbrauch des Prozesses.
    Kann bei hoher Auslastung Warnungen ausgeben oder Aktionen auslösen.
    """

    def __init__(
        self,
        warning_threshold_mb: int = 500,
        critical_threshold_mb: int = 800,
        check_interval_seconds: int = 60
    ):
        """
        Args:
            warning_threshold_mb: Schwellwert für Warnung (MB)
            critical_threshold_mb: Schwellwert für kritische Warnung (MB)
            check_interval_seconds: Prüfintervall in Sekunden
        """
        self.warning_threshold = warning_threshold_mb * 1024 * 1024  # zu Bytes
        self.critical_threshold = critical_threshold_mb * 1024 * 1024
        self.check_interval = check_interval_seconds
        self._running = False
        self._task = None
        self._on_critical_callback = None

    def set_critical_callback(self, callback):
        """Setzt eine Callback-Funktion die bei kritischem Speicher aufgerufen wird."""
        self._on_critical_callback = callback

    def get_memory_usage(self) -> dict:
        """Gibt aktuelle Speichernutzung zurück."""
        if not PSUTIL_AVAILABLE:
            return {"available": False}

        process = psutil.Process(os.getpid())
        mem_info = process.memory_info()

        return {
            "available": True,
            "rss_bytes": mem_info.rss,
            "rss_mb": mem_info.rss / (1024 * 1024),
            "vms_bytes": mem_info.vms,
            "vms_mb": mem_info.vms / (1024 * 1024),
        }

    async def _monitor_loop(self):
        """Hauptschleife für Memory-Monitoring."""
        while self._running:
            try:
                mem = self.get_memory_usage()

                if not mem.get("available"):
                    await asyncio.sleep(self.check_interval)
                    continue

                rss = mem["rss_bytes"]
                rss_mb = mem["rss_mb"]

                if rss >= self.critical_threshold:
                    logger.error(f"KRITISCH: Speicherverbrauch bei {rss_mb:.0f} MB!")
                    if self._on_critical_callback:
                        try:
                            await self._on_critical_callback()
                        except Exception as e:
                            logger.error(f"Fehler im Critical-Callback: {e}")

                elif rss >= self.warning_threshold:
                    logger.warning(f"Hoher Speicherverbrauch: {rss_mb:.0f} MB")

                else:
                    logger.debug(f"Memory: {rss_mb:.0f} MB")

            except Exception as e:
                logger.error(f"Fehler im Memory-Monitor: {e}")

            await asyncio.sleep(self.check_interval)

    async def start(self):
        """Startet das Memory-Monitoring."""
        if not PSUTIL_AVAILABLE:
            logger.warning("Memory-Monitoring nicht verfügbar (psutil fehlt)")
            return

        if self._running:
            return

        self._running = True
        self._task = asyncio.create_task(self._monitor_loop())
        logger.info(f"Memory-Monitor gestartet (Warnung: {self.warning_threshold // (1024*1024)} MB, "
                   f"Kritisch: {self.critical_threshold // (1024*1024)} MB)")

    async def stop(self):
        """Stoppt das Memory-Monitoring."""
        self._running = False
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        logger.info("Memory-Monitor gestoppt")


# Globale Instanz
memory_monitor = MemoryMonitor()
