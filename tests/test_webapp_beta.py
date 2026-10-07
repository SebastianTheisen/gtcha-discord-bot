"""Beta-Container (WEBAPP_ROLE=beta): Routen auch unter /beta, Live-Updates per Server-Sent Events, keine Pushes."""

import asyncio

from aiohttp.test_utils import TestClient, TestServer

from tests.test_ui_smoke import _seed
from webapp.server import App, make_app


async def _client(tmp_path, role):
    await _seed(tmp_path)
    app = App(str(tmp_path / "gtcha_bot.db"), str(tmp_path), "x", role=role)
    client = TestClient(TestServer(make_app(app)))
    await client.start_server()
    return app, client


def test_beta_serves_api_under_prefix_and_streams_updates(tmp_path, monkeypatch):
    monkeypatch.setenv("APP_ADMIN_IDS", "42")

    async def run():
        app, client = await _client(tmp_path, "beta")
        try:
            assert (await client.get("/beta/api/health")).status == 200
            assert (await client.get("/api/health")).status == 200
            assert "push_task" not in client.server.app   # Pushes verschickt nur live
            token = (await app.bridge.redeem_code(await app.bridge.create_code(42, "Basti")))["token"]
            assert (await client.get("/beta/api/stream")).status == 403   # ohne Verknüpfung nichts
            resp = await client.get(f"/beta/api/stream?t={token}")
            assert resp.status == 200 and resp.headers["Content-Type"].startswith("text/event-stream")
            assert b"event: hello" in await asyncio.wait_for(resp.content.readuntil(b"\n\n"), 5)
            app._digest = "alt"   # Daten "geändert": nächste Berechnung meldet ein Update
            await app._compute()
            assert b"event: update" in await asyncio.wait_for(resp.content.readuntil(b"\n\n"), 5)
            resp.close()
        finally:
            await client.close()

    asyncio.run(run())


def test_live_has_no_beta_prefix(tmp_path):
    async def run():
        _, client = await _client(tmp_path, "live")
        try:
            assert (await client.get("/beta/api/health")).status == 404
            assert "push_task" in client.server.app
        finally:
            await client.close()

    asyncio.run(run())


def test_beta_bookmarklet_matches_live_app():
    """Beide Apps müssen dasselbe Lesezeichen erzeugen (gleicher Code, gleiche Version, gleiche Seiten)."""
    import re
    from pathlib import Path
    root = Path(__file__).resolve().parent.parent / "webapp"
    live = (root / "static" / "app.js").read_text()
    beta = (root / "beta" / "src" / "bookmarklet.ts").read_text()

    def body(src):
        return re.search(r"const src = `(.*?)`;", src, re.S).group(1)

    def const(src, name):
        return re.search(rf"const {name} = ([^;]+);", src, re.S).group(1).split("//")[0].strip()

    assert body(live) == body(beta)
    for name in ("SYNC_PAGES", "SYNC_INCREMENTAL", "SYNC_PARALLEL", "SYNC_VERSION"):
        assert re.sub(r"\s+", "", const(live, name)) == re.sub(r"\s+", "", const(beta, name)), name


def test_link_code_for_another_app(tmp_path):
    """Verknüpftes Gerät holt einen Code, mit dem sich z. B. die installierte Beta verknüpft - ohne Discord."""
    async def run():
        app, client = await _client(tmp_path, "beta")
        try:
            assert (await client.post("/beta/api/me/link_code")).status == 401   # nur verknüpft
            token = (await app.bridge.redeem_code(await app.bridge.create_code(42, "Basti")))["token"]
            code = (await (await client.post("/beta/api/me/link_code", headers={"X-Device-Token": token})).json())["code"]
            linked = await (await client.post("/beta/api/link", json={"code": code})).json()
            assert linked["user_id"] == "42" and linked["token"] != token
            assert (await client.post("/beta/api/link", json={"code": code})).status == 400   # einmalig
        finally:
            await client.close()

    asyncio.run(run())
