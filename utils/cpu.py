"""Aufwendige Rechnungen (Hit-Kombinatorik, Versand-Lernen) in einem eigenen Prozess.

In einem Thread (asyncio.to_thread) teilen sie sich Pythons globale Sperre (GIL) mit dem Bot: Solange gerechnet
wird, kommen Abruf, Scrape und Discord kaum dran (Jobs "missed", Zeitlimits laufen ab). Ein eigener Prozess hat
seine eigene Sperre. Fällt der Prozess aus, wird im Thread weitergerechnet - langsamer, aber nichts geht verloren.

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

_pool = None

# Lesbare Namen der Rechnungen für den System-Status
LABELS = {
    "match_shipment_history": "Versand-Auswertung",
    "match_coin_conversions": "Umwandlungen",
    "match_coin_history": "Umwandlungs-Verlauf",
    "observations": "Versand-Lernen",
}

_jobs: list = []          # angefragte, noch nicht fertige Rechnungen (ein Arbeiter: die erste läuft)
_stats = {"done": 0, "fallbacks": 0, "last": None, "slowest": None, "last_end": 0.0, "since": time.time()}


def _executor():
    global _pool
    if _pool is None:
        # spawn statt fork: der Bot hat Threads/offene Verbindungen, die ein fork mitkopieren würde
        _pool = ProcessPoolExecutor(max_workers=1, mp_context=multiprocessing.get_context("spawn"))
    return _pool


def cpu_status() -> dict:
    """Stand des Rechen-Prozesses (Zeiten als Unix-Sekunden)."""
    running = None
    if _jobs:
        job = _jobs[0]
        running = {"name": job["name"], "label": job["label"], "since": round(max(job["queued"], _stats["last_end"]))}
    return {"running": running, "waiting": max(0, len(_jobs) - 1), "done": _stats["done"],
            "fallbacks": _stats["fallbacks"], "last": _stats["last"], "slowest": _stats["slowest"],
            "since": round(_stats["since"]), "alive": _pool is not None}


async def run_cpu(fn, *args, label: str = ""):
    """fn(*args) in einem eigenen Prozess (Argumente und Ergebnis müssen picklebar sein); label z. B. die Banner-ID."""
    global _pool
    name = LABELS.get(getattr(fn, "__name__", ""), getattr(fn, "__name__", "Rechnung"))
    job = {"name": name, "label": str(label), "queued": time.time()}
    _jobs.append(job)
    try:
        if os.getenv("GT_CPU_INLINE") == "1":   # Tests: ohne Extra-Prozess
            return await asyncio.to_thread(fn, *args)
        try:
            return await asyncio.get_running_loop().run_in_executor(_executor(), fn, *args)
        except BrokenProcessPool:
            logger.warning("Rechen-Prozess ausgefallen - starte neu, diese Rechnung im Thread")
            _pool = None
            _stats["fallbacks"] += 1
            return await asyncio.to_thread(fn, *args)
    finally:
        end = time.time()
        start = max(job["queued"], _stats["last_end"])
        seconds = round(end - start, 1)
        done = {"name": name, "label": job["label"], "seconds": seconds, "at": round(end)}
        _stats.update(done=_stats["done"] + 1, last=done, last_end=end)
        if not _stats["slowest"] or seconds >= _stats["slowest"]["seconds"]:
            _stats["slowest"] = done
        if job in _jobs:
            _jobs.remove(job)
