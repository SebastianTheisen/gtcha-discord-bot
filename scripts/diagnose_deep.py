"""Tiefenanalyse: Browser-Engines, Roh-API-Antworten und JS-Logik der Pack-Anzeige.

Vorher einmalig im Container (optional, für Firefox/WebKit):
    docker exec gtcha-discord-bot playwright install --with-deps firefox webkit
Aufruf:
    docker exec -i gtcha-discord-bot python - 24125 < scripts/diagnose_deep.py
"""

import asyncio
import json
import os
import re
import sys

from playwright.async_api import async_playwright

BASE = "https://gtchaxonline.com"
PROXY = os.getenv("SCRAPER_PROXY") or "socks5://127.0.0.1:40000"
SHOW_APIS = ("user/language", "user/detail", "user/country/check", "user/point", "pack/detail/")
JS_TERMS = ("pack_count", "stock_count", "total_pack_count", "total_sendcount", "country", "lang")


def pack_fields(obj):
    if isinstance(obj, dict):
        return {k: v for k, v in obj.items()
                if any(t in k for t in ("pack", "stock", "count", "send", "kangen", "limit", "country", "lang", "market", "rule", "method"))}
    return obj


async def run(pw, engine, proxy, pid, full):
    label = f"{engine}{' + WARP' if proxy else ''}"
    print(f"\n######## {label} ########", flush=True)
    try:
        browser = await getattr(pw, engine).launch(
            headless=True, args=["--no-sandbox"] if engine == "chromium" else None)
    except Exception as e:
        print(f"  nicht verfügbar: {str(e).splitlines()[0][:150]}")
        return
    ctx = await browser.new_context(locale="de-DE", proxy={"server": proxy} if proxy else None)
    page = await ctx.new_page()
    scripts, seen = [], set()

    async def on_response(resp):
        url = resp.url
        if full and resp.request.resource_type == "script" and "gtchaxonline" in url:
            scripts.append(resp)
        if "/api/" not in url:
            return
        key = url.split("?")[0]
        try:
            data = await resp.json()
        except Exception:
            return
        if "pack/list" in url:
            for it in data.get("list", []):
                if str(it.get("id")) == str(pid):
                    print(f"  pack/list {pid}: {json.dumps(pack_fields(it), ensure_ascii=False)}")
            return
        if full and key not in seen and any(s in url for s in SHOW_APIS):
            seen.add(key)
            payload = {k: v for k, v in data.items() if k != "csrf"}
            if "detail" in payload and isinstance(payload["detail"], dict) and "pack/detail" in url:
                payload["detail"] = pack_fields(payload["detail"])
            print(f"  {key.split('/api/')[-1]}: {json.dumps(payload, ensure_ascii=False)[:700]}")

    page.on("response", on_response)
    try:
        await page.goto(f"{BASE}/pack-detail?packId={pid}", wait_until="domcontentloaded", timeout=90000)
        await asyncio.sleep(6)
        body = " ".join((await page.inner_text("body")).split())
        dom = sorted({m for m in re.findall(r"\d[\d,]*\s*/\s*\d[\d,]*", body) if "/10" not in m})
        print(f"  DOM Detailseite: {', '.join(dom) or '-'}")
        if full:
            ls = await page.evaluate("() => JSON.stringify(Object.fromEntries(Object.entries(localStorage)))")
            print(f"  localStorage: {ls[:400]}")
            cookies = await ctx.cookies()
            cookie_str = ", ".join(c["name"] + "=" + c["value"][:20] for c in cookies)
            print(f"  Cookies: {cookie_str}")
        await page.goto(BASE, wait_until="domcontentloaded", timeout=90000)
        await asyncio.sleep(5)
    except Exception as e:
        print(f"  FEHLER: {str(e).splitlines()[0][:200]}")

    if full:
        print(f"  --- JS-Analyse ({len(scripts)} Skripte)")
        for resp in scripts:
            try:
                code = await resp.text()
            except Exception:
                continue
            name = resp.url.split("?")[0].split("/")[-1]
            hits = 0
            for term in JS_TERMS[:4]:
                for m in re.finditer(term, code):
                    snippet = " ".join(code[max(0, m.start() - 120):m.end() + 120].split())
                    print(f"  [{name}] …{snippet}…")
                    hits += 1
                    if hits >= 8:
                        break
                if hits >= 8:
                    break
    await browser.close()


async def main():
    pid = int(sys.argv[1]) if len(sys.argv) > 1 else 24125
    async with async_playwright() as pw:
        await run(pw, "chromium", None, pid, full=True)
        await run(pw, "firefox", None, pid, full=False)
        await run(pw, "webkit", None, pid, full=False)
        await run(pw, "webkit", PROXY, pid, full=False)


asyncio.run(main())
