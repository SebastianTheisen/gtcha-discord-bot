"""GTCHA Tracker - Web-App (PWA) mit den Daten des Bots.

Läuft als eigener Container neben dem Bot, liest dessen Datenbank nur und lauscht nur auf
127.0.0.1 - erreichbar ist die App ausschließlich über Tailscale (tailscale serve), nicht offen im Internet.

Start: python -m webapp.server
"""

import asyncio
import hashlib
import json
import os
import re
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Dict

from aiohttp import web
from loguru import logger

from database.db import Database
from utils.app_bridge import MAX_IMPORT_BYTES, AppBridge
from utils.banner_info import berlin_time
from webapp.accuracy import AccuracyStore
from webapp.history import JST_OFFSET, build_from_stored, ingest, local_time, plan_claims, profile, stored_events
from webapp.images import ImageCache, content_type
from webapp.push import DEFAULTS, EVENTS, WATCH_EVENTS, PushService, build_events
from webapp.view import BannerView, banner_label

STATIC = Path(__file__).parent / "static"
REFRESH_SECONDS = 20
BOOKMARKLET_VERSION = 3   # = SYNC_VERSION in app.js; ältere Lesezeichen bekommen einen Hinweis
POOL_KEYS = ("hits", "hit_keys_detected", "cards_brief", "out_ids")   # nur intern / Detailseite
SEARCH_LIMIT = 40


RESULT_PAGE = """<!doctype html><html lang="de"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1"><title>GTCHA Tracker</title>
<style>body{{margin:0;font:16px -apple-system,system-ui,sans-serif;background:#eef0f4;color:#1d2433}}
.box{{max-width:480px;margin:40px auto;padding:20px;background:#fff;border-radius:14px;box-shadow:0 2px 8px rgba(20,30,60,.08)}}
h1{{font-size:20px;margin:0 0 12px;color:{color}}}p{{margin:8px 0;line-height:1.4}}
table{{width:100%;border-collapse:collapse;font-size:14px;margin-top:12px}}td,th{{text-align:left;padding:5px 4px;
border-top:1px solid #dfe3ea}}th{{color:#6b7385;font-weight:600;border-top:0}}
a{{display:block;margin-top:16px;padding:12px;border-radius:10px;text-align:center;text-decoration:none;font-weight:700;
background:#2563c4;color:#fff}}a.alt{{background:#e6e8ee;color:#1d2433}}
@media (prefers-color-scheme:dark){{body{{background:#0f1320;color:#e8ebf5}}.box{{background:#1a2033}}
a.alt{{background:#232a40;color:#e8ebf5}}h1{{color:{dark_color}}}td,th{{border-top-color:#2e3753}}th{{color:#98a1bd}}}}
</style></head><body><div class="box"><h1>{title}</h1>{lines}
<a href="https://gtchaxonline.com/">Zurück zu GTCHA</a><a class="alt" href="/#/settings">GTCHA Tracker öffnen</a>
<p style="font-size:13px;color:#6b7385">Die Daten liegen nur auf deinem VPS. In der installierten App unter „Ich“ →
„Mein Verlauf“ ansehen.</p></div></body></html>"""


AREA_NAMES = {"undecided-detail": "Gacha – unentschieden", "pending-detail": "Gacha – angefordert",
              "shipped-detail": "Versand", "downloaded-detail": "Downloads", "buy-point-history": "Münzen",
              "purchase-history": "Käufe", "ticket-history": "Tickets", "change-member": "Kontoseite (Rang, ¥)"}


def import_result_page(lines: list, ok: bool = True, report: list = None) -> web.Response:
    """Ergebnis von "Alles übertragen" als eigenständige Seite (alles eingebettet). Sie öffnet sich im
    Safari-Tab - dort hat die App weder Verknüpfung noch aktuellen Zwischenspeicher, daher nicht die App laden."""
    import html
    body = "".join(f"<p>{html.escape(line)}</p>" for line in lines)
    if report:   # je Bereich: Seiten und ob komplett / nur Neues / Fehler - zum Prüfen des Ablaufs
        rows = "".join(
            f"<tr><td>{html.escape(AREA_NAMES.get(p, p))}</td><td>{'–' if n is None else n}</td>"
            f"<td>{'⚠️ ' + html.escape(how) if n is None else html.escape(how)}</td></tr>" for p, n, how in report)
        body += (f"<table><tr><th>Bereich</th><th>Seiten</th><th></th></tr>{rows}</table>")
    return web.Response(text=RESULT_PAGE.format(title="Übertragen" if ok else "Nicht übertragen", lines=body,
                                                color="#1f3a6e" if ok else "#e5383b",
                                                dark_color="#9db8ff" if ok else "#ff6b6e"),
                        content_type="text/html", headers={"Cache-Control": "no-store"})


def device_kind(agent: str) -> str:
    """Grober Gerätetyp aus dem User-Agent (nur zur Anzeige in „Geräte verwalten“)."""
    for key, name in (("iPhone", "iPhone"), ("iPad", "iPad"), ("Android", "Android"), ("Macintosh", "Mac"),
                      ("Windows", "Windows"), ("Linux", "Linux")):
        if key in agent:
            return name
    return "Gerät"


def search_cards(banners: list, q: str, ids: set) -> list:
    """Karten (gleiche Karten-ID = gleiche Karte) mit allen aktiven Bannern, in denen sie stecken."""
    found = {}
    for b in banners:
        for cid, (name, value, image, copies) in (b.get("cards_brief") or {}).items():
            if ids:
                if cid not in ids:
                    continue
            elif q not in (name or "").lower() and q != cid:
                continue
            card = found.setdefault(cid, {"id": cid, "name": name, "image": image, "value": value, "banners": []})
            card["value"] = max(card["value"] or 0, value or 0)
            card["banners"].append({"id": b["id"], "title": b["title"], "price": b["price"], "value": value,
                                    "copies": copies, "remaining": b["remaining"], "ev_pct": b["ev_pct"],
                                    "status": b["status"], "out": cid in (b.get("out_ids") or [])})
    cards = sorted(found.values(), key=lambda c: (-(c["value"] or 0), c["name"] or ""))[:SEARCH_LIMIT]
    for c in cards:
        c["banners"].sort(key=lambda x: (x["out"], -(x["ev_pct"] or 0)))
    return cards
IMAGE_MAX_AGE = 30 * 24 * 3600
PUSH_CHECK_SECONDS = 60
REMIND_AFTER = 3 * 86400


class App:
    def __init__(self, db_path: str, data_dir: str, contact: str):
        self.view = BannerView(Database(db_path))
        self.push = PushService(data_dir, contact)
        self.images = ImageCache(data_dir)
        self.bridge = AppBridge(os.path.join(data_dir, "webapp.db"))
        self.accuracy = AccuracyStore(os.path.join(data_dir, "webapp.db"))
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
            try:
                await self.accuracy.record(self._data)
            except Exception as e:
                logger.debug(f"Treffsicherheit nicht gespeichert: {e}")
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
        lite = [{k: v for k, v in b.items() if k not in POOL_KEYS} for b in await self.banners()]
        return web.json_response({"banners": lite, "updated": self._updated or int(time.time())})

    async def api_hot(self, request):
        hot = self.view.hot(await self.banners())
        return web.json_response({"hot": [{k: v for k, v in b.items() if k not in POOL_KEYS} for b in hot]})

    async def api_cards(self, request):
        """Kartensuche über alle aktiven Banner (Name oder Kartennummer-ID) bzw. bestimmte Karten (ids=1,2)."""
        q = request.query.get("q", "").strip().lower()
        ids = [i for i in request.query.get("ids", "").split(",") if i.strip().isdigit()][:200]
        if len(q) < 2 and not ids:
            return web.json_response({"cards": []})
        return web.json_response({"cards": search_cards(await self.banners(), q, set(ids))})

    async def api_banner(self, request):
        try:
            pack_id = int(request.match_info["id"])
        except ValueError:
            raise web.HTTPBadRequest()
        data = await self.view.detail(pack_id)
        if not data:
            raise web.HTTPNotFound()
        data.pop("hit_keys_detected", None)
        data.pop("cards_brief", None)
        data["ev_history"] = await self.accuracy.history(pack_id)
        return web.json_response(data)

    async def image(self, request):
        """Bild aus dem Zwischenspeicher auf dem VPS (beim ersten Mal von GTCHA geladen)."""
        path = await self.images.get(request.query.get("u", ""))
        if not path:
            raise web.HTTPNotFound()
        width = request.query.get("w", "")
        if width in ("320", "640", "960"):
            try:
                path = await asyncio.get_running_loop().run_in_executor(None, self.images.resized, path, int(width))
            except Exception as e:
                logger.debug(f"Bild nicht verkleinert: {e}")
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
        device = await self.bridge.redeem_code(str(body.get("code", "")), device_kind(request.headers.get("User-Agent", "")))
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

    async def api_accuracy(self, request):
        return web.json_response(await self.accuracy.report())

    async def api_health(self, request):
        """Für die Überwachung durch den Bot: läuft, und wie alt die berechneten Daten sind."""
        age = int(time.time()) - self._updated if self._updated else None
        return web.json_response({"ok": True, "data_age": age})

    async def api_import_form(self, request):
        """"Alles übertragen"-Lesezeichen: Formular-POST von gtchaxonline.com (kein CORS nötig).

        Der Schlüssel steckt im Formular (Safari kennt die App-Verknüpfung nicht). Danach zurück in die App.
        """
        form = await request.post()
        try:
            data = json.loads(form.get("d", ""))
        except ValueError:
            raise web.HTTPBadRequest(text="Ungültige Daten")
        user = await self.bridge.device(str(data.get("t", "")))
        if not user:
            return import_result_page(["⚠️ Dieses Lesezeichen gehört zu keinem verknüpften Gerät mehr – bitte in der "
                                       "App unter „Ich“ neu kopieren."], ok=False)
        saved = pages = partial = 0
        entries, report = [], []
        for entry in data.get("pages") or []:
            path = str(entry.get("path", ""))
            if not re.fullmatch(r"[a-z0-9-]{1,40}", path):
                continue
            n = len(entry.get("pages") or [])
            if entry.get("error") or not n:
                # Bereich nicht geladen: nichts überschreiben (ein leerer Münzverlauf würde sonst alles ersetzen)
                report.append((path, None, str(entry.get("error") or "keine Seite")))
                continue
            await self.bridge.add_import(user, "sync", f"https://gtchaxonline.com/{path}",
                                         json.dumps(entry, ensure_ascii=False))
            entries.append({"path": path, "pages": entry.get("pages") or [], "partial": bool(entry.get("partial"))})
            report.append((path, n, "nur Neues" if entry.get("partial") else "komplett"))
            saved += 1
            partial += bool(entry.get("partial"))
            pages += n
        seconds = round(int(data.get("ms") or 0) / 1000)
        logger.info(f"Sync von {user['name']}: {saved} Bereiche, {pages} Seiten ({partial} nur Neues)"
                    + (f" in {seconds} s" if seconds else "") + " · "
                    + ", ".join(f"{p}={n if n is not None else 'FEHLER ' + how}" for p, n, how in report))
        gap, added = False, {}
        try:
            stored = await self.bridge.get_history(user["user_id"])
            before = {a: len((stored.get(a) or {}).get("items") or []) for a in ("coins", "shipped")}
            changed = ingest(stored, entries)
            await self.bridge.set_history(user["user_id"], changed)
            gap = any(a.get("gap") for a in changed.values())
            added = {a: len(changed[a]["items"]) - before[a] for a in before if a in changed}
        except Exception as e:
            logger.warning(f"Verlauf nicht übernommen: {type(e).__name__}: {e}")
        claims = 0
        try:
            claims = len(await self.auto_claim(user))
        except Exception as e:
            logger.warning(f"Automatische Medaillen fehlgeschlagen: {type(e).__name__}: {e}")
        failed = [p for p, n, _ in report if n is None]
        lines = [f"✅ {saved} Bereiche mit zusammen {pages} Seiten übertragen"
                 + (f" in {seconds} Sekunden." if seconds else ".")]
        if partial:
            lines.append(f"➕ {partial} Bereich(e) nur mit neuen Einträgen.")
        if "coins" in added:
            lines.append(f"🪙 Münzverlauf: {max(0, added['coins'])} neue Buchung(en).")
        if "shipped" in added:
            lines.append(f"📦 Versand: {max(0, added['shipped'])} neue Karte(n).")
        if int(data.get("v") or 0) < BOOKMARKLET_VERSION:
            lines.append("🔁 Dein Lesezeichen ist veraltet – in der App unter „Ich“ → „Eigene GTCHA-Daten“ neu "
                         "kopieren und im Safari-Lesezeichen ersetzen.")
        if failed:
            lines.append(f"⚠️ Nicht geladen: {', '.join(AREA_NAMES.get(p, p) for p in failed)} – der gespeicherte Stand "
                         f"bleibt erhalten. Einfach noch einmal übertragen.")
        if gap:
            lines.append("⚠️ Zwischen alt und neu fehlt evtl. etwas – einmal „Komplett übertragen“ benutzen.")
        if claims:
            lines.append(f"🏅 {claims} Medaille(n) automatisch gemeldet.")
        return import_result_page(lines, report=report)

    async def auto_claim(self, user: Dict) -> list:
        """Angeforderte Karten (noch nicht verschickt) automatisch als Medaille melden, wenn eindeutig."""
        cards = ((await self.history(user)).get("pending") or {}).get("items") or []
        if not cards:
            return []
        banners, _ = await self.view.history_context(None)
        planned = plan_claims(cards, banners, await self.view.claim_targets(), user["user_id"],
                              await self.bridge.auto_claim_keys(user["user_id"]))
        for p in planned:
            await self.bridge.add_auto_claim(user, p["key"], p["pack_id"], p["tier"])
            logger.info(f"Automatische Medaille für {user['name']}: {p['card']} -> {p['pack_id']} {p['tier']}")
        return planned

    async def history(self, user: Dict) -> Dict:
        """Gespeicherter Verlauf; beim ersten Mal aus dem letzten vollständigen Lauf übernommen."""
        stored = await self.bridge.get_history(user["user_id"])
        if not stored:
            areas = await self.bridge.latest_sync(user["user_id"])
            if areas:
                await self.bridge.set_history(user["user_id"], ingest({}, [
                    {"path": p, "pages": a.get("pages") or []} for p, a in areas.items()]))
                stored = await self.bridge.get_history(user["user_id"])
        return stored

    async def api_my_profile(self, request):
        """Rang und Aufladung aus dem letzten Übertragen (zum automatischen Ausfüllen)."""
        user = await self._user(request)
        if not user:
            raise web.HTTPUnauthorized(text="Gerät nicht mit Discord verknüpft")
        return web.json_response(profile(await self.history(user)))

    async def api_my_history(self, request):
        """Eigener Verlauf aus allen "Alles übertragen"-Läufen (nur für die verknüpfte Person)."""
        user = await self._user(request)
        if not user:
            raise web.HTTPUnauthorized(text="Gerät nicht mit Discord verknüpft")
        stored = await self.history(user)
        if not stored:
            return web.json_response({"empty": True})
        since = min((e["t"] for e in stored_events(stored)), default=None)
        banners, moves = await self.view.history_context(since - JST_OFFSET if since else None)
        # Rechnen außerhalb der Ereignisschleife - die App bleibt währenddessen bedienbar
        data = await asyncio.get_running_loop().run_in_executor(None, build_from_stored, stored, banners, moves)
        data["saved_at"] = local_time(max(a.get("updated_at") or "" for a in stored.values()))
        data["profile"] = profile(stored)
        data["auto_claims"] = await self.bridge.auto_claims(user["user_id"])
        for c in data["auto_claims"]:
            c["title"] = (banners.get(c["pack_id"]) or {}).get("title")
        return web.json_response(data)

    async def api_my_devices(self, request):
        user = await self._user(request)
        if not user:
            raise web.HTTPUnauthorized(text="Gerät nicht mit Discord verknüpft")
        current = hashlib.sha256(request.headers.get("X-Device-Token", "").encode()).hexdigest()[:16]
        devices = await self.bridge.devices(user["user_id"])
        for d in devices:
            d["current"] = d["id"] == current
            d["created_at"], d["last_seen"] = local_time(d["created_at"]), local_time(d["last_seen"])
        return web.json_response({"devices": devices})

    async def api_remove_device(self, request):
        user = await self._user(request)
        if not user:
            raise web.HTTPUnauthorized(text="Gerät nicht mit Discord verknüpft")
        body = await request.json()
        return web.json_response({"ok": await self.bridge.remove_device(user["user_id"], str(body.get("id", "")))})

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
        user = await self._user(request)
        await self.push.subscribe(sub, body.get("prefs") or {}, user["user_id"] if user else None)
        return web.json_response({"ok": True})

    async def api_push_unsubscribe(self, request):
        body = await request.json()
        await self.push.unsubscribe(str(body.get("endpoint", "")))
        return web.json_response({"ok": True})

    async def api_push_prefs(self, request):
        body = await request.json()
        return web.json_response({"prefs": await self.push.prefs(str(body.get("endpoint", "")))})

    async def api_push_inbox(self, request):
        """Verlauf der Pushes dieses Geräts (erkannt am Push-Abo, wie die Einstellungen)."""
        body = await request.json()
        endpoint = str(body.get("endpoint", ""))
        if not endpoint:
            return web.json_response({"items": [], "unread": 0, "more": False})
        user = await self._user(request)
        if user:   # Gerät mit Discord verknüpft -> eigene Pushes (Medaillen, Erinnerung) gehen hierhin
            await self.push.set_user(endpoint, user["user_id"])
        limit = max(1, min(100, int(body.get("limit") or 30)))
        return web.json_response(await self.push.inbox(endpoint, limit, int(body.get("before") or 0)))

    async def api_push_read(self, request):
        body = await request.json()
        endpoint = str(body.get("endpoint", ""))
        if endpoint:
            ids = body.get("ids")
            await self.push.mark_read(endpoint, None if body.get("all") else [int(i) for i in (ids or [])][:200])
        return web.json_response({"ok": True})

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

    # --- Eigene Pushes: Ergebnis automatischer Medaillen, Erinnerung ans Übertragen ---
    async def personal_pushes(self, now: float = None):
        now = now or time.time()
        for c in await self.bridge.finished_auto_claims():
            row = await self.view.db.get_banner(c["pack_id"]) or {}
            title = banner_label(row.get("title"), row.get("best_hit"), row.get("category"), row.get("price_coins")) \
                or f"Banner {c['pack_id']}"
            if c["status"] == "ok":
                await self.push.send_user(c["discord_user_id"], "auto_ok", f"🏅 Medaille {c['tier']} gemeldet",
                                          f"Automatisch aus deinen angeforderten Karten · {title}",
                                          f"/#/banner/{c['pack_id']}")
            else:
                await self.push.send_user(c["discord_user_id"], "auto_rejected", "❌ Automatische Medaille abgelehnt",
                                          f"{c['tier']} · {title}: {c.get('reason') or 'ohne Grund'}", "/#/settings")
            await self.bridge.mark_auto_claim_notified(c["discord_user_id"], c["card_key"])
        # Erinnerung: 3 Tage nichts übertragen -> einmal (dann wieder nach 3 Tagen), nur tagsüber
        if not 10 <= berlin_time(now).hour < 21:
            return
        state = await self.push.load_state("reminders")
        changed = False
        for user_id, last in (await self.bridge.last_syncs()).items():
            age = now - datetime.fromisoformat(last).timestamp()
            if age < REMIND_AFTER or now - state.get(user_id, 0) < REMIND_AFTER:
                continue
            if await self.push.send_user(user_id, "remind", "📥 Zeit zum Übertragen",
                                         f"Seit {int(age // 86400)} Tagen nichts übertragen – auf gtchaxonline.com das "
                                         f"Lesezeichen „Alles übertragen“ antippen.", "/#/settings"):
                state[user_id] = now
                changed = True
        if changed:
            await self.push.save_state(state, "reminders")

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
                await self.personal_pushes()
            except Exception as e:
                logger.warning(f"Push-Prüfung fehlgeschlagen: {type(e).__name__}: {e}")
            await asyncio.sleep(PUSH_CHECK_SECONDS)


@web.middleware
async def security_headers(request, handler):
    response = await handler(request)
    # App-Dateien (JS/CSS/Manifest) nie ungefragt aus dem Browser-Cache nehmen - sonst sieht man
    # nach einem Update noch tagelang die alte Version
    if request.path.startswith("/static/") and not request.path.endswith(".png"):
        response.headers["Cache-Control"] = "no-cache"
    response.headers.setdefault("X-Content-Type-Options", "nosniff")
    response.headers.setdefault("Referrer-Policy", "no-referrer")
    response.headers.setdefault("X-Frame-Options", "DENY")
    return response


def make_app(app: App) -> web.Application:
    web_app = web.Application(middlewares=[security_headers], client_max_size=MAX_IMPORT_BYTES + 1024)
    web_app.add_routes([
        web.get("/", app.index),
        web.get("/sw.js", app.service_worker),
        web.get("/manifest.webmanifest", app.manifest),
        web.get("/api/banners", app.api_banners),
        web.get("/api/hot", app.api_hot),
        web.get("/api/cards", app.api_cards),
        web.get(r"/api/banner/{id}", app.api_banner),
        web.get("/img", app.image),
        web.post("/api/link", app.api_link),
        web.get("/api/me", app.api_me),
        web.get("/api/me/medals", app.api_my_medals),
        web.get("/api/me/history", app.api_my_history),
        web.get("/api/me/profile", app.api_my_profile),
        web.post("/api/unlink", app.api_unlink),
        web.get("/api/me/devices", app.api_my_devices),
        web.post("/api/me/devices/remove", app.api_remove_device),
        web.get("/api/health", app.api_health),
        web.get("/api/accuracy", app.api_accuracy),
        web.post("/api/import-form", app.api_import_form),
        web.post("/api/medal", app.api_medal),
        web.get(r"/api/medal/{id:\d+}", app.api_medal_status),
        web.get("/api/push/key", app.api_push_key),
        web.post("/api/push/subscribe", app.api_push_subscribe),
        web.post("/api/push/unsubscribe", app.api_push_unsubscribe),
        web.post("/api/push/prefs", app.api_push_prefs),
        web.post("/api/push/test", app.api_push_test),
        web.post("/api/push/inbox", app.api_push_inbox),
        web.post("/api/push/read", app.api_push_read),
    ])
    web_app.router.add_static("/static", STATIC)

    async def start_background(_):
        await app.push.init()
        await app.bridge.init()
        await app.accuracy.init()
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
