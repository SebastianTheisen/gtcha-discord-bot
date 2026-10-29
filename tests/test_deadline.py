"""Hängender Scrape (tote Tor-Verbindung, 29.10.): wird nach Zeitlimit + Gnadenfrist aufgegeben, statt ewig zu warten."""

import asyncio
import time

import pytest

from scraper.gtcha_scraper import close_quietly
from utils.maintenance import run_with_deadline


def test_finishes_normally():
    async def work():
        await asyncio.sleep(0.01)
        return 7

    assert asyncio.run(run_with_deadline(work(), 1)) == 7


def test_hanging_task_whose_cleanup_also_hangs_is_abandoned():
    async def hang():
        try:
            await asyncio.sleep(3600)          # Abfrage hängt
        finally:
            await asyncio.sleep(3600)          # ... und das Aufräumen (Browser schließen) auch

    async def run():
        started = time.monotonic()
        with pytest.raises(asyncio.TimeoutError):
            await run_with_deadline(hang(), 0.2, grace=0.2, name="Scrape")
        return time.monotonic() - started

    assert asyncio.run(run()) < 2             # früher: nie fertig, Sperre ewig gehalten


def test_close_quietly_gives_up():
    async def run():
        started = time.monotonic()
        await close_quietly(asyncio.sleep(3600), timeout=0.2)
        return time.monotonic() - started

    assert asyncio.run(run()) < 1
