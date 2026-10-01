"""Untersucht die ganze Seite nach Daten zu gezogenen Karten oder Pack-Positionen.

Nur lesend und ohne Login: besucht Seiten, schneidet API-Antworten mit, durchsucht den
JavaScript-Code und ruft gefundene lesende Adressen ab. Adressen, die nach Kaufen, Ziehen,
Bezahlen oder Tauschen klingen, werden nie aufgerufen.

Aufruf auf dem VPS (im laufenden Container, nutzt dessen Tor-Proxy):
    docker exec -i gtcha-discord-bot python - 24114 < scripts/diagnose_site.py
Der vollständige Bericht landet in ~/gtcha-discord-bot/data/site_report.txt
"""

import asyncio
import json
import os
import re
import sys

from playwright.async_api import async_playwright

BASE = "https://gtchaxonline.com"
PROXY = os.getenv("SCRAPER_PROXY")
REPORT = "/app/data/site_report.txt"

RISKY = ("buy", "purchase", "draw", "lottery", "gacha_exec", "exec", "pay", "charge", "order",
         "exchange", "sell", "convert", "ship", "delete", "update", "edit", "regist", "login",
         "logout", "signup", "password", "withdraw", "cancel", "send", "post", "add", "remove",
         "coupon", "point_add", "checkout", "stripe", "paypal")
KEYWORDS = ("remain", "rank", "winner", "win_", "history", "result", "drawn", "position", "slot",
            "seat", "index_no", "last_one", "lastone", "kuji", "box", "sold", "stock", "opened")
FETCH_JS = """async (url) => {
    const r = await fetch(url, {headers: {Accept: 'application/json'}});
    const t = await r.text();
    try { return {status: r.status, json: JSON.parse(t)}; } catch (e) { return {status: r.status, text: t.slice(0, 200)}; }
}"""

lines = []


def out(text=""):
    print(text, flush=True)
    lines.append(text)


def short(obj, limit=400):
    return json.dumps(obj, ensure_ascii=False)[:limit]


def keys_of(data):
    if isinstance(data, dict):
        return {k: (keys_of(v) if isinstance(v, (dict, list)) else type(v).__name__) for k, v in data.items()
                if k != "csrf"}
    if isinstance(data, list) and data:
        return [keys_of(data[0])]
    return type(data).__name__


def is_risky(path):
    low = path.lower()
    return any(w in low for w in RISKY)


async def main():
    pid = int(sys.argv[1]) if len(sys.argv) > 1 else 24114
    out(f"Banner {pid} | Proxy: {PROXY or 'keiner'}")
    async with async_playwright() as pw:
        browser = await pw.chromium.launch(
            headless=True, args=["--no-sandbox", "--disable-dev-shm-usage", "--blink-settings=imagesEnabled=false"])
        ctx = await browser.new_context(locale="de-DE", proxy={"server": PROXY} if PROXY else None)
        page = await ctx.new_page()

        api_seen, scripts = {}, {}

        async def on_response(resp):
            url = resp.url
            if resp.request.resource_type == "script" and "gtchaxonline" in url:
                try:
                    scripts[url.split("?")[0]] = await resp.text()
                except Exception:
                    pass
            if "/api/" in url:
                key = f"{resp.request.method} {url.split('?')[0]}"
                if key in api_seen:
                    return
                try:
                    data = await resp.json()
                except Exception:
                    data = None
                api_seen[key] = {"post": (resp.request.post_data or "")[:200], "keys": keys_of(data),
                                 "sample": short(data, 600)}

        page.on("response", on_response)

        # 1. Startseite und Detailseite
        for url in (BASE, f"{BASE}/pack-detail?packId={pid}"):
            out(f"\n### Lade {url}")
            await page.goto(url, wait_until="domcontentloaded", timeout=120000)
            await asyncio.sleep(8)
            for _ in range(4):
                await page.mouse.wheel(0, 5000)
                await asyncio.sleep(1)

        # 2. JavaScript durchsuchen
        code = "\n".join(scripts.values())
        out(f"\n=== {len(scripts)} Skripte, {len(code) // 1024} KB Code")
        paths = set(re.findall(r"""["'`](/?(?:api/)?(?:user|pack|card|gacha|rank|history|winner|result|news|notice|top|item)[A-Za-z0-9_\-/]*)["'`$]""", code))
        paths |= {m for m in re.findall(r"""["'`](/?api/[A-Za-z0-9_\-/]+)""", code)}
        routes = sorted(set(re.findall(r"""path:\s*["'`](/[A-Za-z0-9_\-/:]*)["'`]""", code)))
        out(f"\n=== Routen (Unterseiten) im Code ({len(routes)}):")
        for r in routes:
            out(f"  {r}")
        out(f"\n=== Adress-Bruchstücke im Code ({len(paths)}):")
        for p in sorted(paths):
            out(f"  {p}{'   [nicht abgerufen: riskant]' if is_risky(p) else ''}")

        out("\n=== Stichwörter im Code (je max. 3 Fundstellen):")
        for kw in KEYWORDS:
            hits = [m.start() for m in re.finditer(kw, code)][:3]
            for pos in hits:
                out(f"  [{kw}] …{' '.join(code[max(0, pos - 100):pos + 120].split())}…")

        # 3. Unterseiten besuchen (ohne Platzhalter und ohne riskante Wörter)
        visit = [r for r in routes if ":" not in r and not is_risky(r) and r not in ("/", "/pack-detail")][:20]
        out(f"\n=== Besuche {len(visit)} Unterseiten")
        for r in visit:
            try:
                await page.goto(f"{BASE}{r}", wait_until="domcontentloaded", timeout=60000)
                await asyncio.sleep(4)
                text = " ".join((await page.inner_text("body")).split())[:150]
                out(f"  {r}: {text}")
            except Exception as e:
                out(f"  {r}: Fehler {str(e).splitlines()[0][:100]}")

        # 4. Alle mitgeschnittenen API-Aufrufe
        out(f"\n=== Mitgeschnittene API-Aufrufe ({len(api_seen)}):")
        for key, info in sorted(api_seen.items()):
            out(f"  {key}" + (f" | POST: {info['post']}" if info['post'] else ""))
            out(f"     Felder: {short(info['keys'], 500)}")

        # 5. Gefundene lesende Adressen abrufen
        await page.goto(f"{BASE}/api/user/point", wait_until="domcontentloaded", timeout=60000)
        out("\n=== Probeabruf lesender Adressen:")
        probed = set()
        for p in sorted(paths):
            if is_risky(p) or "api" not in p and "user/" not in p:
                continue
            base_path = "/" + p.lstrip("/")
            if not base_path.startswith("/api/"):
                base_path = "/api/" + base_path.lstrip("/")
            for path in (base_path.rstrip("/"), f"{base_path.rstrip('/')}/{pid}", f"{base_path.rstrip('/')}/{pid}/1"):
                if path in probed or is_risky(path):
                    continue
                probed.add(path)
                res = await page.evaluate(FETCH_JS, path)
                body = res.get("json")
                if body is None or (isinstance(body, dict) and body.get("message") == "Not found"):
                    continue
                out(f"  {path} -> {res.get('status')} | Felder: {short(keys_of(body), 300)}")
                out(f"     {short(body, 300)}")

        # 6. Vollständige Felder von Detail und Kartenliste
        for path in (f"/api/user/pack/detail/{pid}", f"/api/user/pack/card_list/{pid}/1"):
            res = await page.evaluate(FETCH_JS, path)
            out(f"\n=== Vollständige Antwort {path}:")
            out(f"  {short(res.get('json'), 2500)}")

        await browser.close()

    try:
        with open(REPORT, "w") as f:
            f.write("\n".join(lines))
        print(f"\nBericht gespeichert: {REPORT} (auf dem VPS: ~/gtcha-discord-bot/data/site_report.txt)")
    except Exception as e:
        print(f"Bericht nicht gespeichert: {e}")


asyncio.run(main())
