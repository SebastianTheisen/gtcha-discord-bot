"""Aufwendige Rechnungen (Hit-Kombinatorik, Versand-Lernen) in einem eigenen Prozess.

In einem Thread (asyncio.to_thread) teilen sie sich Pythons globale Sperre (GIL) mit dem Bot: Solange gerechnet
wird, kommen Abruf, Scrape und Discord kaum dran (Jobs "missed", Zeitlimits laufen ab). Ein eigener Prozess hat
seine eigene Sperre.

Mehrere Arbeiter (CPU_WORKERS, Standard: Kerne - 1, höchstens 4): Eine lange Rechnung (z. B. 24164 mit 284
Versand-Hits) hält die anderen nicht auf. Ein Zeitlimit gibt es nur, wenn CPU_TIMEOUT_SECONDS gesetzt ist; dann
wird der Prozess danach beendet und der Aufrufer bekommt CpuTimeout (bisheriges Ergebnis bleibt).

cpu_status() liefert den Stand (was läuft gerade, wie viel wartet, wie lange dauerten die Rechnungen) für den
System-Status in der App.
"""

import asyncio
import multiprocessing
import os
import time
from concurrent.futures import ProcessPoolExecutor
from concurrent.futures.process import BrokenProcessPool

from loguru import logger

CPU_TIMEOUT = float(os.getenv("CPU_TIMEOUT_SECONDS") or "0")   # 0 = kein Zeitlimit
CPU_WORKERS = int(os.getenv("CPU_WORKERS") or max(1, min(4, (os.cpu_count() or 2) - 1)))

_pool = None
_slots: dict = {}   # je Event-Loop: höchstens CPU_WORKERS gleichzeitig - so ist bekannt, wann eine Rechnung startet

# Lesbare Namen der Rechnungen für den System-Status
LABELS = {
    "match_shipment_history": "Versand-Auswertung",
    "match_coin_conversions": "Umwandlungen",
    "match_coin_history": "Umwandlungs-Verlauf",
    "observations": "Versand-Lernen",
}

_jobs: list = []          # angefragte, noch nicht fertige Rechnungen (die erste läuft)
_stats = {"done": 0, "fallbacks": 0, "timeouts": 0, "last": None, "slowest": None, "last_timeout": None,
          "last_end": 0.0, "since": time.time()}


class CpuTimeout(Exception):
    """Rechnung hat das Zeitlimit überschritten und wurde abgebrochen."""


def _executor():
    global _pool
    if _pool is None:
        # spawn statt fork: der Bot hat Threads/offene Verbindungen, die ein fork mitkopieren würde
        _pool = ProcessPoolExecutor(max_workers=CPU_WORKERS, mp_context=multiprocessing.get_context("spawn"))
    return _pool


def _kill_pool():
    """Rechen-Prozess hart beenden (eine laufende Rechnung lässt sich anders nicht abbrechen)."""
    global _pool
    pool, _pool = _pool, None
    if pool is None:
        return
    for proc in list(getattr(pool, "_processes", {}).values()):
        try:
            proc.kill()
        except Exception:
            pass
    pool.shutdown(wait=False, cancel_futures=True)


def _slot() -> asyncio.Semaphore:
    loop = asyncio.get_running_loop()
    if id(loop) not in _slots:
        _slots.clear()   # alte Loops (Tests) vergessen
        _slots[id(loop)] = asyncio.Semaphore(CPU_WORKERS)
    return _slots[id(loop)]


def cpu_status() -> dict:
    """Stand des Rechen-Prozesses (Zeiten als Unix-Sekunden)."""
    started = sorted((j for j in _jobs if j.get("started")), key=lambda j: j["started"])
    running = [{"name": j["name"], "label": j["label"], "since": round(j["started"])} for j in started]
    return {"running": running[0] if running else None, "running_all": running,
            "waiting": len(_jobs) - len(started), "done": _stats["done"], "workers": CPU_WORKERS,
            "fallbacks": _stats["fallbacks"], "timeouts": _stats["timeouts"], "last_timeout": _stats["last_timeout"],
            "last": _stats["last"], "slowest": _stats["slowest"], "since": round(_stats["since"]),
            "alive": _pool is not None, "limit": CPU_TIMEOUT or None}


async def run_cpu(fn, *args, label: str = "", timeout: float = None):
    """fn(*args) in einem eigenen Prozess (Argumente und Ergebnis müssen picklebar sein); label z. B. die Banner-ID.

    Wirft CpuTimeout, wenn die Rechnung länger als das Zeitlimit braucht.
    """
    name = LABELS.get(getattr(fn, "__name__", ""), getattr(fn, "__name__", "Rechnung"))
    job = {"name": name, "label": str(label), "queued": time.time()}
    _jobs.append(job)
    limit = timeout or CPU_TIMEOUT or None
    try:
        async with _slot():
            job["started"] = time.time()
            if os.getenv("GT_CPU_INLINE") == "1":   # Tests: ohne Extra-Prozess
                return await asyncio.to_thread(fn, *args)
            for attempt in (1, 2):
                future = asyncio.get_running_loop().run_in_executor(_executor(), fn, *args)
                try:
                    return await (asyncio.wait_for(future, limit) if limit else future)
                except asyncio.TimeoutError:
                    _kill_pool()
                    _stats["timeouts"] += 1
                    _stats["last_timeout"] = {"name": name, "label": job["label"], "at": round(time.time())}
                    logger.warning(f"[RECHNEN] {name} {job['label']} nach {limit:.0f}s abgebrochen - "
                                   f"bisheriges Ergebnis bleibt")
                    raise CpuTimeout(f"{name} {job['label']}")
                except BrokenProcessPool:
                    # Prozess ausgefallen (oder wegen Zeitlimit einer anderen Rechnung beendet): neu starten
                    if _pool is not None:
                        _kill_pool()
                    _stats["fallbacks"] += 1
                    if attempt == 2:
                        raise
                    logger.warning("Rechen-Prozess ausgefallen - starte neu")
    finally:
        end = time.time()
        if job.get("started"):
            seconds = round(end - job["started"], 1)
            done = {"name": name, "label": job["label"], "seconds": seconds, "at": round(end)}
            _stats.update(done=_stats["done"] + 1, last=done, last_end=end)
            if not _stats["slowest"] or seconds >= _stats["slowest"]["seconds"]:
                _stats["slowest"] = done
        if job in _jobs:
            _jobs.remove(job)
