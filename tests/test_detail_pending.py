"""Detailseite: dauert die Versand-Erklärung zu lange, kommt die Seite sofort ohne sie - die volle danach."""

import asyncio
import time

import webapp.server as server


def test_slow_detail_returns_quick_version_then_full(tmp_path, monkeypatch):
    calls = []

    def fake_job(db_path, what, arg=None):
        calls.append(what)
        if what == "detail":
            time.sleep(0.5)                     # Banner mit sehr vielen Versand-Hits
            return {"id": arg, "shipments": [{"explain": ["✅"]}]}
        return {"id": arg, "shipments": [{"explain": []}], "explain_pending": True}

    monkeypatch.setenv("WEBAPP_VIEW_INLINE", "1")
    monkeypatch.setattr(server, "_view_job", fake_job)
    monkeypatch.setattr(server, "DETAIL_WAIT", 0.1)

    async def run():
        app = server.App(str(tmp_path / "b.db"), str(tmp_path), "x")
        first = await app._detail(24164)        # sofort, ohne Erklärung
        second = await app._detail(24164)       # läuft noch: teilt sich die Rechnung, wieder ohne
        await asyncio.sleep(0.6)
        third = await app._detail(24164)        # fertig: volle Seite aus dem Zwischenspeicher
        return first, second, third

    first, second, third = asyncio.run(run())
    assert first["explain_pending"] and second["explain_pending"]
    assert "explain_pending" not in third and third["shipments"][0]["explain"] == ["✅"]
    assert calls.count("detail") == 1          # nur einmal voll gerechnet
