"""GTCHA Tracker - Web-App (PWA) mit den Daten des Bots.

Läuft als eigener Container neben dem Bot, liest dessen Datenbank nur und lauscht nur auf
127.0.0.1 - erreichbar ist die App ausschließlich über Tailscale (tailscale serve), nicht offen im Internet.

Start: python -m webapp.server
"""

import asyncio
import os
import re
import sys
import time
from pathlib import Path

from aiohttp import web
from loguru import logger

from database.db import Database
from utils.app_bridge import AppBridge
from webapp.images import ImageCache, content_type
from webapp.push import DEFAULTS, EVENTS, WATCH_EVENTS, PushService, build_events
from webapp.view import BannerView

STATIC = Path(__file__).parent / "static"
REFRESH_SECONDS = 20
IMAGE_MAX_AGE = 30 * 24 * 3600
PUSH_CHECK_SECONDS = 60


class App:
    def __init__(self, db_path: str, data_dir: str, contact: str):
        self.view = BannerView(Database(db_path))
        self.push = PushService(data_dir, contact)
        self.images = ImageCache(data_dir)
        self.bridge = AppBridge(os.path.join(data_dir, "webapp.db"))
        self._link_fails = []
        self._data = None
        self._updated = 0
        self._lock = asyncio.Lock()
        self._syncing = False

    async def _compute(self, only_if_missing: bool = False) -> list:
        async with self._lock:
            if only_if_missing and self._data is not None:
                return self._data
            started = time.monotonic()
            self._data = await self.view.all_banners(with_pool=True)
            self._updated = int(time.time())
            logger.debug(f"Daten neu berechnet in {time.monotonic() - started:.2f}s")
            return self._data

    async def banners(self) -> list:
        """Alle aktiven Banner - im Hintergrund vorberechnet, Anfragen warten nie auf die Datenbank."""
        return self._data if self._data is not None else await self._compute(only_if_missing=True)

    async def sync_images(self):
        """Lädt alle Bilder aktiver Banner vorab und löscht die von beendeten Bannern."""
        try:
            urls = await self.view.image_urls()
            if not urls:
                return   # keine aktiven Banner gelesen - lieber nichts löschen
            loaded = await self.images.warm(urls)
            removed = self.images.cleanup(urls)
            if loaded or removed:
                count, size = self.images.stats()
                logger.info(f"Bilder: {count} gespeichert ({size / 1024 / 1024:.0f} MB)")
        except Exception as e:
            logger.warning(f"Bilder-Abgleich fehlgeschlagen: {type(e).__name__}: {e}")
        finally:
            self._syncing = False

    async def refresh_loop(self):
        """Hält die Daten frisch und lädt neue Banner-Bilder vorab."""
        while True:
            try:
                await self._compute()
                if not self._syncing:
                    self._syncing = True
                    asyncio.create_task(self.sync_images())
            except Exception as e:
                logger.warning(f"Daten-Aktualisierung fehlgeschlagen: {type(e).__name__}: {e}")
            await asyncio.sleep(REFRESH_SECONDS)

    # --- API ---
    async def api_banners(self, request):
        lite = [{k: v for k, v in b.items() if k not in ("hits", "hit_keys_detected")} for b in await self.banners()]
        return web.json_response({"banners": lite, "updated": self._updated or int(time.time())})

    async def api_hot(self, request):
        hot = self.view.hot(await self.banners())
        return web.json_response({"hot": [{k: v for k, v in b.items() if k not in ("hits", "hit_keys_detected")}
                                          for b in hot]})

    async def api_banner(self, request):
        try:
            pack_id = int(request.match_info["id"])
        except ValueError:
            raise web.HTTPBadRequest()
        data = await self.view.detail(pack_id)
        if not data:
            raise web.HTTPNotFound()
        data.pop("hit_keys_detected", None)
        return web.json_response(data)

    async def image(self, request):
        """Bild aus dem Zwischenspeicher auf dem VPS (beim ersten Mal von GTCHA geladen)."""
        path = await self.images.get(request.query.get("u", ""))
        if not path:
            raise web.HTTPNotFound()
        return web.FileResponse(path, headers={"Cache-Control": f"public, max-age={IMAGE_MAX_AGE}, immutable",
                                               "Content-Type": content_type(path)})

    # --- Discord-Verknüpfung und Medaillen ---
    async def _user(self, request):
        return await self.bridge.device(request.headers.get("X-Device-Token"))

    async def api_link(self, request):
        now = time.time()
        self._link_fails = [t for t in self._link_fails if now - t < 600]
        if len(self._link_fails) >= 10:
            raise web.HTTPTooManyRequests(text="Zu viele falsche Codes – bitte 10 Minuten warten")
        body = await request.json()
        device = await self.bridge.redeem_code(str(body.get("code", "")))
        if not device:
            self._link_fails.append(now)
            return web.json_response({"error": "Code ungültig oder abgelaufen"}, status=400)
        logger.info(f"App mit Discord verknüpft: {device['name']}")
        return web.json_response(device)

    async def api_me(self, request):
        user = await self._user(request)
        return web.json_response(user or {}, status=200 if user else 401)

    async def api_my_medals(self, request):
        user = await self._user(request)
        if not user:
            raise web.HTTPUnauthorized(text="Gerät nicht mit Discord verknüpft")
        return web.json_response({"medals": await self.view.my_medals(user["user_id"])})

    async def api_unlink(self, request):
        token = request.headers.get("X-Device-Token")
        if token:
            await self.bridge.unlink(token)
        return web.json_response({"ok": True})

    async def api_medal(self, request):
        user = await self._user(request)
        if not user:
            raise web.HTTPUnauthorized(text="Gerät nicht mit Discord verknüpft")
        body = await request.json()
        tier, action = str(body.get("tier", "")).upper(), body.get("action")
        try:
            pack_id = int(body.get("pack_id"))
        except (TypeError, ValueError):
            raise web.HTTPBadRequest(text="Banner fehlt")
        if action not in ("claim", "unclaim") or not re.fullmatch(r"T([1-9]\d?)", tier):
            raise web.HTTPBadRequest(text="Ungültige Meldung")
        if not await self.view.db.get_banner(pack_id):
            raise web.HTTPNotFound(text="Banner unbekannt")
        request_id = await self.bridge.add_request(pack_id, tier, user, action)
        return web.json_response({"id": request_id})

    async def api_medal_status(self, request):
        user = await self._user(request)
        req = await self.bridge.get_request(int(request.match_info["id"]))
        if not user or not req or req["discord_user_id"] != user["user_id"]:
            raise web.HTTPNotFound()
        return web.json_response({"status": req["status"], "reason": req["reason"]})

    async def api_push_key(self, request):
        return web.json_response({"key": self.push.public_key(), "events": list(EVENTS),
                                  "defaults": DEFAULTS, "watch_events": list(WATCH_EVENTS)})

    async def api_push_subscribe(self, request):
        body = await request.json()
        sub = body.get("subscription") or {}
        if not str(sub.get("endpoint", "")).startswith("https://") or not sub.get("keys"):
            raise web.HTTPBadRequest(text="ungültiges Abo")
        await self.push.subscribe(sub, body.get("prefs") or {})
        return web.json_response({"ok": True})

    async def api_push_unsubscribe(self, request):
        body = await request.json()
        await self.push.unsubscribe(str(body.get("endpoint", "")))
        return web.json_response({"ok": True})

    async def api_push_prefs(self, request):
        body = await request.json()
        return web.json_response({"prefs": await self.push.prefs(str(body.get("endpoint", "")))})

    async def api_push_test(self, request):
        body = await request.json()
        await self.push.send("test", "🔔 Test", "Push-Benachrichtigungen funktionieren.",
                             only=str(body.get("endpoint", "")))
        return web.json_response({"ok": True})

    # --- Seiten ---
    async def index(self, request):
        return web.FileResponse(STATIC / "index.html", headers={"Cache-Control": "no-cache"})

    async def service_worker(self, request):
        return web.FileResponse(STATIC / "sw.js", headers={"Cache-Control": "no-cache",
                                                          "Content-Type": "application/javascript"})

    async def manifest(self, request):
        return web.FileResponse(STATIC / "manifest.webmanifest",
                                headers={"Content-Type": "application/manifest+json"})

    # --- Push-Überwachung ---
    async def push_loop(self):
        while True:
            try:
                banners = await self.banners()
                state = await self.push.load_state()
                messages, new_state = build_events(banners, self.view.hot(banners), state)
                for kind, title, body, _, _ in messages:
                    if kind != "packs":   # Pack-Bewegungen kämen jede Minute - nicht ins Log
                        logger.info(f"Ereignis: {title} - {body}")
                await self.push.deliver(messages)
                await self.push.save_state(new_state)
            except Exception as e:
                logger.warning(f"Push-Prüfung fehlgeschlagen: {type(e).__name__}: {e}")
            await asyncio.sleep(PUSH_CHECK_SECONDS)


@web.middleware
async def security_headers(request, handler):
    response = await handler(request)
    response.headers.setdefault("X-Content-Type-Options", "nosniff")
    response.headers.setdefault("Referrer-Policy", "no-referrer")
    response.headers.setdefault("X-Frame-Options", "DENY")
    return response


def make_app(app: App) -> web.Application:
    web_app = web.Application(middlewares=[security_headers], client_max_size=64 * 1024)
    web_app.add_routes([
        web.get("/", app.index),
        web.get("/sw.js", app.service_worker),
        web.get("/manifest.webmanifest", app.manifest),
        web.get("/api/banners", app.api_banners),
        web.get("/api/hot", app.api_hot),
        web.get(r"/api/banner/{id}", app.api_banner),
        web.get("/img", app.image),
        web.post("/api/link", app.api_link),
        web.get("/api/me", app.api_me),
        web.get("/api/me/medals", app.api_my_medals),
        web.post("/api/unlink", app.api_unlink),
        web.post("/api/medal", app.api_medal),
        web.get(r"/api/medal/{id:\d+}", app.api_medal_status),
        web.get("/api/push/key", app.api_push_key),
        web.post("/api/push/subscribe", app.api_push_subscribe),
        web.post("/api/push/unsubscribe", app.api_push_unsubscribe),
        web.post("/api/push/prefs", app.api_push_prefs),
        web.post("/api/push/test", app.api_push_test),
    ])
    web_app.router.add_static("/static", STATIC)

    async def start_background(_):
        await app.push.init()
        await app.bridge.init()
        web_app["refresh_task"] = asyncio.create_task(app.refresh_loop())
        web_app["push_task"] = asyncio.create_task(app.push_loop())

    async def stop_background(_):
        web_app["refresh_task"].cancel()
        web_app["push_task"].cancel()
        await app.images.close()

    web_app.on_startup.append(start_background)
    web_app.on_cleanup.append(stop_background)
    return web_app


def main():
    from utils.banner_info import berlin_time
    logger.remove()
    logger.configure(patcher=lambda r: r["extra"].update(berlin=f"{berlin_time(r['time'].timestamp()):%H:%M:%S}"))
    logger.add(sys.stderr, level=os.getenv("LOG_LEVEL", "INFO").upper(),
               format="{extra[berlin]} | {level: <7} | {message}")
    data_dir = os.getenv("WEBAPP_DATA_DIR", "data")
    app = App(os.getenv("DATABASE_PATH", os.path.join(data_dir, "gtcha_bot.db")), data_dir,
              os.getenv("WEBAPP_CONTACT", "https://github.com"))
    host, port = os.getenv("WEBAPP_HOST", "127.0.0.1"), int(os.getenv("WEBAPP_PORT", "8080"))
    logger.info(f"GTCHA Tracker läuft auf http://{host}:{port}")
    web.run_app(make_app(app), host=host, port=port, print=None, access_log=None)


if __name__ == "__main__":
    main()
