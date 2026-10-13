"""Aufwendige Rechnungen laufen in einem eigenen Prozess (nicht unter der GIL des Bots)."""

import asyncio
import os

from utils.cpu import run_cpu


def test_runs_in_other_process():
    async def run():
        return await run_cpu(os.getpid), await run_cpu(sum, [1, 2, 3])

    pid, total = asyncio.run(run())
    assert pid != os.getpid() and total == 6


def test_status_shows_last_and_slowest_job_with_label():
    from utils.cpu import cpu_status
    from webapp.server import worker_summary

    async def run():
        await run_cpu(sum, [1, 2], label=24191)
        return cpu_status()

    st = asyncio.run(run())
    assert st["running"] is None and st["waiting"] == 0 and st["done"] >= 1
    assert st["last"]["name"] == "sum" and st["last"]["label"] == "24191"
    summary = worker_summary({"cpu": st, "hits": {"last": {"banners": 3, "seconds": 1.5, "at": 100}, "waiting": 2},
                              "views": {"waiting": 4, "busy": True}, "at": 95}, now=100)
    assert summary["age"] == 5 and summary["hits_last"]["ago"] == 0 and summary["hits_waiting"] == 2
    assert summary["views_waiting"] == 4 and summary["cpu_last"]["label"] == "24191"
    assert worker_summary(None, 100) is None
