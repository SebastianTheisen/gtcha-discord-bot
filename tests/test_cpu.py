"""Aufwendige Rechnungen laufen in einem eigenen Prozess (nicht unter der GIL des Bots)."""

import asyncio
import os

from utils.cpu import run_cpu


def test_runs_in_other_process():
    async def run():
        return await run_cpu(os.getpid), await run_cpu(sum, [1, 2, 3])

    pid, total = asyncio.run(run())
    assert pid != os.getpid() and total == 6
