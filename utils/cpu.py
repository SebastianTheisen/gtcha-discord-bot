"""Aufwendige Rechnungen (Hit-Kombinatorik, Versand-Lernen) in einem eigenen Prozess.

In einem Thread (asyncio.to_thread) teilen sie sich Pythons globale Sperre (GIL) mit dem Bot: Solange gerechnet
wird, kommen Abruf, Scrape und Discord kaum dran (Jobs "missed", Zeitlimits laufen ab). Ein eigener Prozess hat
seine eigene Sperre. Fällt der Prozess aus, wird im Thread weitergerechnet - langsamer, aber nichts geht verloren.
"""

import asyncio
import multiprocessing
import os
from concurrent.futures import ProcessPoolExecutor
from concurrent.futures.process import BrokenProcessPool

from loguru import logger

_pool = None


def _executor():
    global _pool
    if _pool is None:
        # spawn statt fork: der Bot hat Threads/offene Verbindungen, die ein fork mitkopieren würde
        _pool = ProcessPoolExecutor(max_workers=1, mp_context=multiprocessing.get_context("spawn"))
    return _pool


async def run_cpu(fn, *args):
    """fn(*args) in einem eigenen Prozess (Argumente und Ergebnis müssen picklebar sein)."""
    global _pool
    if os.getenv("GT_CPU_INLINE") == "1":   # Tests: ohne Extra-Prozess
        return await asyncio.to_thread(fn, *args)
    try:
        return await asyncio.get_running_loop().run_in_executor(_executor(), fn, *args)
    except BrokenProcessPool:
        logger.warning("Rechen-Prozess ausgefallen - starte neu, diese Rechnung im Thread")
        _pool = None
        return await asyncio.to_thread(fn, *args)
