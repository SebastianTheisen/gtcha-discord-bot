"""Betrieb: tägliche Datenbank-Backups und ein Wächter, der einen hängenden Bot neu startet."""

import os
import sqlite3
import threading
import time
from datetime import datetime
from pathlib import Path

from loguru import logger

BACKUP_KEEP_DAYS = 7
HEARTBEAT_FILE = Path("data/heartbeat")


def backup_database(db_path: str, keep: int = BACKUP_KEEP_DAYS) -> Path:
    """Konsistente Kopie der SQLite-Datenbank nach data/backups/, ältere als `keep` Stück werden gelöscht."""
    backup_dir = Path(db_path).parent / "backups"
    backup_dir.mkdir(parents=True, exist_ok=True)
    target = backup_dir / f"{Path(db_path).stem}_{datetime.now():%Y-%m-%d}.db"
    source = sqlite3.connect(db_path)
    dest = sqlite3.connect(target)
    try:
        source.backup(dest)  # funktioniert auch, während der Bot schreibt
    finally:
        dest.close()
        source.close()
    backups = sorted(backup_dir.glob(f"{Path(db_path).stem}_*.db"))
    for old in backups[:-keep]:
        old.unlink()
    return target


def touch_heartbeat():
    """Nach jedem erfolgreichen Scrape: Zeitstempel für Wächter und Docker-Healthcheck."""
    HEARTBEAT_FILE.parent.mkdir(parents=True, exist_ok=True)
    HEARTBEAT_FILE.touch()


def heartbeat_age() -> float:
    try:
        return time.time() - HEARTBEAT_FILE.stat().st_mtime
    except FileNotFoundError:
        return float("inf")


def start_watchdog(max_silence_seconds: int, startup_grace_seconds: int):
    """Beendet den Prozess, wenn zu lange kein Scrape fertig wurde (Docker startet ihn dann neu).

    Läuft als eigener Thread, damit er auch greift, wenn die asyncio-Schleife hängt.
    """
    started = time.time()

    def watch():
        while True:
            time.sleep(60)
            if time.time() - started < startup_grace_seconds:
                continue
            age = heartbeat_age()
            if age > max_silence_seconds:
                logger.critical(f"[WÄCHTER] Seit {age / 60:.0f} Min kein erfolgreicher Scrape - starte neu")
                os._exit(1)

    threading.Thread(target=watch, name="watchdog", daemon=True).start()
