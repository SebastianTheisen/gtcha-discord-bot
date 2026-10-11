"""Prüft die Schnittstellen von gtchaxonline.com auf Änderungen: Was ist neu, was ist weg, was sieht anders aus?

Wie beim letzten Mal, nur ohne eigene Probeabrufe:
  1. Code der Seite (öffentliche Skripte): Unterseiten (Routen) und Schnittstellen-Adressen, die darin vorkommen
  2. Öffentliche Unterseiten wie ein Besucher öffnen und mitlesen, welche Abfragen die Seite dabei selbst
     lädt - mit dem Aufbau der Antworten (nur Feldnamen und Typen, keine Inhalte)
  3. Vergleich mit dem letzten Stand (data/api_inventory.json; beim ersten Mal mit dem Bericht vom Umbau)

Seiten zum Kaufen, Bezahlen, Registrieren, Anmelden oder mit Adressen werden nicht geöffnet.
Das Ergebnis landet in data/api_check.txt und wird automatisch in den Admin-Kanal von Discord geschickt.

Aufruf auf dem VPS (dauert 2-4 Minuten):
    docker exec -i gtcha-discord-bot python - < tools/api_check.py
"""

import asyncio
import json
import os
import re
import sqlite3
import sys
from datetime import datetime

sys.path.insert(0, "/app")

BASE = "https://gtchaxonline.com"
DATA = "/app/data"
INVENTORY = os.path.join(DATA, "api_inventory.json")
OUT = os.path.join(DATA, "api_check.txt")
# nicht öffnen: Kaufen, Bezahlen, Konto, Anmeldung, Adressen
SKIP = re.compile(r"buy|pay|complete|confirm|password|register|address|login|oauth|carrier|combini|settlement|"
                  r"purchase-card|card-input|change-member|watch-videos|fund", re.I)
# Stand vom Umbau (Oktober 2026), falls noch kein gespeicherter Stand da ist
BASELINE = {
  "routes": [
    "/", "/amazon-pay-redirect", "/buy-point", "/buy-point-history", "/buy-product", "/card-input",
    "/change-member", "/change-password", "/coin-history", "/complete-buy-point", "/complete-buy-point-alipay",
    "/complete-buy-point-amazom-pay", "/complete-buy-point-paypal", "/complete-carrier",
    "/complete-password-reissue", "/complete-user-register", "/confirm-buy-point", "/confirm-buy-product",
    "/confirm-combini", "/confirm-user-address", "/contact", "/copied", "/downloaded-detail", "/error-carrier",
    "/faq", "/fund-settlement", "/gacha-animation", "/gem-terms-of-service", "/get-card-list", "/login",
    "/notice-list", "/oauth/login", "/onetime-password-reissue", "/pack-detail", "/pack-list", "/password-reissue",
    "/pay-redirect", "/pending-detail", "/privacy-protection", "/purchase-card", "/purchase-history",
    "/purchase-history-detail", "/rewards", "/rewards-page", "/shipped-detail", "/specific-commerce", "/store",
    "/store-corporation", "/store-gacha", "/store-pack-detail", "/terms-of-service", "/ticket-history",
    "/undecided-detail", "/user-address", "/user-guide", "/user-menu", "/watch-videos",
  ],
  "api": [
    "/api/user/history/download/", "/api/user/history/preview/", "/user/buy_rank", "/user/change_country",
    "/user/check_sms", "/user/country", "/user/coupon/use", "/user/detail", "/user/language", "/user/pack/buy",
    "/user/pack/card_list/", "/user/pack/check", "/user/pack/detail/", "/user/point", "/user/send_sms",
  ],
  "calls": [
    "GET /api/user/card_type", "GET /api/user/country", "GET /api/user/detail",
    "GET /api/user/history/downloaded/{n}", "GET /api/user/history/pending/{n}", "GET /api/user/language",
    "GET /api/user/pack/all_list//{n}", "GET /api/user/pack/card_list/{n}/{n}", "GET /api/user/pack/detail/{n}",
    "GET /api/user/point", "GET /api/user/point/end", "POST /api/user/rewards/cards/{n}",
  ],
}

lines = []


def out(text=""):
    print(text)
    lines.append(text)


def norm(path):
    """Adresse ohne Zahlen (Banner-ID, Seite) und ohne Abfrageteil: /api/user/pack/detail/{n}"""
    return re.sub(r"/\d+", "/{n}", path.split("?")[0])


def shape(v, depth=0):
    """Aufbau einer Antwort: Feldnamen und Typen (keine Inhalte)."""
    if depth > 3:
        return "…"
    if isinstance(v, dict):
        return {k: shape(x, depth + 1) for k, x in v.items() if k != "csrf"}
    if isinstance(v, list):
        return [shape(v[0], depth + 1)] if v else []
    return type(v).__name__


def flat(s, prefix=""):
    """{"a": {"b": "int"}} -> {"a.b": "int"} zum Vergleichen."""
    if isinstance(s, dict):
        r = {}
        for k, v in s.items():
            r.update(flat(v, f"{prefix}{k}."))
        return r or {prefix.rstrip("."): "{}"}
    if isinstance(s, list):
        return flat(s[0], prefix + "[].") if s else {prefix + "[]": "leer"}
    return {prefix.rstrip("."): s}


def diff(label, old, new):
    added, gone = sorted(set(new) - set(old)), sorted(set(old) - set(new))
    out(f"\n=== {label}: {len(new)} (neu {len(added)}, weg {len(gone)})")
    for x in added:
        out(f"  🆕 {x}")
    for x in gone:
        out(f"  ❌ weg: {x}")
    if not added and not gone:
        out("  unverändert")


async def main():
    from scraper.gtcha_scraper import GTCHAScraper
    prev = BASELINE
    try:
        with open(INVENTORY, encoding="utf-8") as f:
            prev = json.load(f)
        out(f"Vergleich mit Stand vom {prev.get('at', '?')}")
    except (OSError, ValueError):
        out("Vergleich mit dem Bericht vom Umbau (erster Lauf)")
    db = sqlite3.connect(os.path.join(DATA, "gtcha_bot.db"))
    active = [r[0] for r in db.execute("SELECT pack_id FROM banners WHERE is_active = 1 ORDER BY pack_id DESC")]
    no_pool = [r[0] for r in db.execute("SELECT pack_id FROM banners WHERE is_active = 1 AND (card_pool IS NULL "
                                        "OR card_pool NOT LIKE '%\"cards\": [{%') ORDER BY pack_id DESC")]
    scripts, calls = {}, {}

    async with GTCHAScraper() as s:
        ctx = s._context

        async def on_response(r):
            url = r.url
            if not url.startswith(BASE):
                return
            try:
                if url.split("?")[0].endswith(".js") and url not in scripts:
                    scripts[url] = await r.text()
                elif "/api/" in url:
                    key = f"{r.request.method} {norm(url.replace(BASE, ''))}"
                    try:
                        body = shape(await r.json())
                    except Exception:
                        body = f"kein JSON (HTTP {r.status})"
                    calls.setdefault(key, {"status": r.status, "shape": body})
            except Exception:
                pass
        ctx.on("response", on_response)
        page = await ctx.new_page()

        async def visit(path):
            try:
                await page.goto(BASE + path, wait_until="domcontentloaded", timeout=60000)
                try:
                    await page.wait_for_load_state("networkidle", timeout=15000)
                except Exception:
                    pass
                await page.wait_for_timeout(1500)
                text = await page.evaluate("document.body ? document.body.innerText.length : 0")
                return f"{text} Zeichen"
            except Exception as e:
                return f"Fehler: {type(e).__name__}"

        out("\n=== Besuchte Seiten")
        out(f"  / : {await visit('/')}")
        for pid in ([active[0]] if active else []) + no_pool[:2]:
            out(f"  /pack-detail?packId={pid}{' (ohne Kartenliste)' if pid in no_pool else ''}: "
                f"{await visit(f'/pack-detail?packId={pid}')}")
        code = "\n".join(scripts.values())
        routes = sorted(set(re.findall(r"path:\s*[\"'`](/[A-Za-z0-9_\-/]*)[\"'`]", code)))
        if len(routes) < 10:   # Router anders verpackt: alle Zeichenketten, die wie eine Unterseite aussehen
            routes = sorted(set(re.findall(r"[\"'`](/[a-z][a-z0-9\-]{2,40})[\"'`]", code)) - {"/api"})
        for path in [r for r in routes if r != "/" and ":" not in r and not SKIP.search(r)][:45]:
            out(f"  {path}: {await visit(path)}")
        await page.close()

    code = "\n".join(scripts.values())
    out(f"\n=== Code: {len(scripts)} Skripte, {sum(map(len, scripts.values())) // 1024} KB")
    api = sorted({m for m in re.findall(r"[\"'`](/(?:api/)?user/[A-Za-z0-9_\-/]+)", code)}
                 | {m for m in re.findall(r"[\"'`](/api/[A-Za-z0-9_\-/]+)", code)})
    diff("Unterseiten (Routen) im Code", prev.get("routes", []), routes)
    diff("Schnittstellen-Adressen im Code", prev.get("api", []), api)
    diff("Abfragen, die die Seiten selbst laden", prev.get("calls", []), sorted(calls))

    out("\n=== Aufbau der Antworten (Feldnamen, Typen)")
    old_shapes = prev.get("shapes", {})
    for key in sorted(calls):
        c = calls[key]
        now = flat(c["shape"]) if isinstance(c["shape"], (dict, list)) else {"": c["shape"]}
        before = old_shapes.get(key)
        if before is None:
            out(f"  {key} -> HTTP {c['status']}" + ("" if "shapes" not in prev else "  (neu)"))
            out(f"     {json.dumps(c['shape'], ensure_ascii=False)[:1500]}")
            continue
        added = sorted(set(now) - set(before))
        gone = sorted(set(before) - set(now))
        changed = sorted(k for k in set(now) & set(before) if now[k] != before[k])
        status = "unverändert" if not (added or gone or changed) else "GEÄNDERT"
        out(f"  {key} -> HTTP {c['status']} · {status}")
        for k in added:
            out(f"     🆕 {k}: {now[k]}")
        for k in gone:
            out(f"     ❌ weg: {k}")
        for k in changed:
            out(f"     ✏️ {k}: {before[k]} -> {now[k]}")

    with open(INVENTORY, "w", encoding="utf-8") as f:
        json.dump({"at": f"{datetime.now():%d.%m.%Y %H:%M}", "routes": routes, "api": api, "calls": sorted(calls),
                   "shapes": {k: (flat(c["shape"]) if isinstance(c["shape"], (dict, list)) else {"": c["shape"]})
                              for k, c in calls.items()}}, f, ensure_ascii=False, indent=1)
    out(f"\nStand gespeichert ({INVENTORY}) - der nächste Lauf vergleicht damit.")


async def post(path):
    import aiohttp
    token, channel = os.getenv("DISCORD_TOKEN", ""), os.getenv("ADMIN_CHANNEL_ID", "")
    if not token or not channel.isdigit():
        print("Discord: DISCORD_TOKEN oder ADMIN_CHANNEL_ID fehlt - Datei liegt in data/api_check.txt")
        return
    form = aiohttp.FormData()
    form.add_field("payload_json", json.dumps({"content": "🔎 Schnittstellen-Prüfung (api_check)"}))
    with open(path, "rb") as f:
        form.add_field("files[0]", f.read(), filename=os.path.basename(path), content_type="text/plain")
    async with aiohttp.ClientSession() as session:
        async with session.post(f"https://discord.com/api/v10/channels/{channel}/messages", data=form,
                                headers={"Authorization": f"Bot {token}"}) as r:
            print("Discord: gesendet" if r.status < 300 else f"Discord: Fehler {r.status} {await r.text()}")


out(f"Schnittstellen-Prüfung {datetime.now():%d.%m.%Y %H:%M}")
try:
    asyncio.run(main())
except Exception as e:
    out(f"❌ Abbruch: {type(e).__name__}: {e}")
with open(OUT, "w", encoding="utf-8") as f:
    f.write("\n".join(lines) + "\n")
asyncio.run(post(OUT))
