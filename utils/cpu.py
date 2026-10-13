"""Aufwendige Rechnungen (Hit-Kombinatorik, Versand-Lernen) in einem eigenen Prozess.

In einem Thread (asyncio.to_thread) teilen sie sich Pythons globale Sperre (GIL) mit dem Bot: Solange gerechnet
wird, kommen Abruf, Scrape und Discord kaum dran (Jobs "missed", Zeitlimits laufen ab). Ein eigener Prozess hat
seine eigene Sperre.

Zeitlimit: Manche Banner (z. B. 24164 mit 284 Versand-Hits) lassen die Kombinatorik praktisch endlos laufen und
hielten so alle anderen Rechnungen auf. Nach CPU_TIMEOUT Sekunden wird der Prozess beendet und neu gestartet; der
Aufrufer bekommt CpuTimeout und behält sein bisheriges Ergebnis (lieber nichts abhaken als falsch).

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

CPU_TIMEOUT = float(os.getenv("CPU_TIMEOUT_SECONDS") or "60")

_pool = None
_locks: dict = {}   # je Event-Loop: Rechnungen nacheinander, damit das Zeitlimit ab dem echten Start zählt

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
        _pool = ProcessPoolExecutor(max_workers=1, mp_context=multiprocessing.get_context("spawn"))
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


def _lock() -> asyncio.Lock:
    loop = asyncio.get_running_loop()
    if id(loop) not in _locks:
        _locks.clear()   # alte Loops (Tests) vergessen
        _locks[id(loop)] = asyncio.Lock()
    return _locks[id(loop)]


def cpu_status() -> dict:
    """Stand des Rechen-Prozesses (Zeiten als Unix-Sekunden)."""
    running = None
    started = [j for j in _jobs if j.get("started")]
    if started:
        job = started[0]
        running = {"name": job["name"], "label": job["label"], "since": round(job["started"])}
    return {"running": running, "waiting": len(_jobs) - len(started), "done": _stats["done"],
            "fallbacks": _stats["fallbacks"], "timeouts": _stats["timeouts"], "last_timeout": _stats["last_timeout"],
            "last": _stats["last"], "slowest": _stats["slowest"], "since": round(_stats["since"]),
            "alive": _pool is not None, "limit": CPU_TIMEOUT}


async def run_cpu(fn, *args, label: str = "", timeout: float = None):
    """fn(*args) in einem eigenen Prozess (Argumente und Ergebnis müssen picklebar sein); label z. B. die Banner-ID.

    Wirft CpuTimeout, wenn die Rechnung länger als das Zeitlimit braucht.
    """
    name = LABELS.get(getattr(fn, "__name__", ""), getattr(fn, "__name__", "Rechnung"))
    job = {"name": name, "label": str(label), "queued": time.time()}
    _jobs.append(job)
    limit = timeout or CPU_TIMEOUT
    try:
        async with _lock():
            job["started"] = time.time()
            if os.getenv("GT_CPU_INLINE") == "1":   # Tests: ohne Extra-Prozess
                return await asyncio.to_thread(fn, *args)
            for attempt in (1, 2):
                future = asyncio.get_running_loop().run_in_executor(_executor(), fn, *args)
                try:
                    return await asyncio.wait_for(future, limit)
                except asyncio.TimeoutError:
                    _kill_pool()
                    _stats["timeouts"] += 1
                    _stats["last_timeout"] = {"name": name, "label": job["label"], "at": round(time.time())}
                    logger.warning(f"[RECHNEN] {name} {job['label']} nach {limit:.0f}s abgebrochen - "
                                   f"bisheriges Ergebnis bleibt")
                    raise CpuTimeout(f"{name} {job['label']}")
                except BrokenProcessPool:
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
