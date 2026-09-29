"""Vergleicht /api/user/pack/list direkt vs. über WARP, um Pack-Abweichungen zu finden.

Aufruf auf dem VPS (Host, nicht im Container):
    python3 scripts/diagnose_packs.py [PACK_ID ...]
"""

import json
import subprocess
import sys
import time

URL = "https://gtchaxonline.com/api/user/pack/list"
WARP = "127.0.0.1:40000"
UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
FAKE_GEO = ["X-Forwarded-For: 217.237.150.100", "X-Real-IP: 217.237.150.100",
            "CF-Connecting-IP: 217.237.150.100", "X-Country: DE"]
SHOW_HEADERS = ("server", "cf-cache-status", "age", "cf-ray", "x-cache", "cache-control", "date", "via", "x-served-by")
PACK_FIELDS = ["pack_count", "pack_remaining", "remaining_count", "remaining", "stock", "packs"]

MODES = {
    "DIREKT": ([], False),
    "DIREKT+GEO-HEADER": (FAKE_GEO, False),
    "WARP": ([], True),
}


def fetch(extra_headers, use_warp, bust):
    url = f"{URL}?_={int(time.time() * 1000)}" if bust else URL
    cmd = ["curl", "-s", "--max-time", "25", "-D", "-", url,
           "-H", "Accept: application/json", "-H", "Accept-Language: de-DE,de;q=0.9",
           "-H", f"User-Agent: {UA}", "-H", "Cache-Control: no-cache"]
    for h in extra_headers:
        cmd += ["-H", h]
    if use_warp:
        cmd += ["--socks5-hostname", WARP]
    out = subprocess.run(cmd, capture_output=True, text=True).stdout
    head, _, body = out.partition("\r\n\r\n")
    while body.startswith("HTTP/"):
        head, _, body = body.partition("\r\n\r\n")
    headers = {}
    for line in head.splitlines()[1:]:
        k, _, v = line.partition(":")
        headers[k.strip().lower()] = v.strip()
    status = head.splitlines()[0] if head else "KEINE ANTWORT"
    try:
        data = json.loads(body)
    except Exception:
        data = None
    return status, headers, data, body


def packs(item):
    for f in PACK_FIELDS:
        if item.get(f) is not None:
            return item[f]
    return None


def main():
    wanted = [int(a) for a in sys.argv[1:]]
    results = {}
    first_item_shown = False

    for name, (hdrs, warp) in MODES.items():
        for bust in (True, False):
            for i in range(2):
                label = f"{name} {'cache-bust' if bust else 'ohne-bust'} #{i + 1}"
                status, headers, data, body = fetch(hdrs, warp, bust)
                print(f"\n=== {label}: {status}")
                if not isinstance(data, dict):
                    print(f"    content-type: {headers.get('content-type', '?')}")
                    print(f"    Body: {' '.join(body.split())[:400]}")
                    continue
                for h in SHOW_HEADERS:
                    if h in headers:
                        print(f"    {h}: {headers[h]}")
                extra = {k: v for k, v in data.items() if k != "list"}
                if extra:
                    print(f"    Weitere Felder: {json.dumps(extra, ensure_ascii=False)[:300]}")
                items = {int(it["id"]): it for it in data.get("list", []) if it.get("id")}
                print(f"    {len(items)} Banner")
                if items and not first_item_shown:
                    sample = next(iter(items.values()))
                    print(f"    Beispiel-Eintrag: {json.dumps(sample, ensure_ascii=False)[:800]}")
                    first_item_shown = True
                for pid in wanted:
                    it = items.get(pid)
                    print(f"    {pid}: {packs(it) if it else 'nicht vorhanden'}")
                results[label] = {pid: packs(it) for pid, it in items.items()}
                time.sleep(1)

    base_label = next(iter(results), None)
    if not base_label:
        return
    print(f"\n=== ABWEICHUNGEN gegenüber '{base_label}'")
    base = results[base_label]
    for label, vals in results.items():
        if label == base_label:
            continue
        diffs = [(pid, base[pid], vals[pid]) for pid in base if pid in vals and base[pid] != vals[pid]]
        print(f"  {label}: {len(diffs)} Banner abweichend")
        for pid, a, b in diffs[:8]:
            print(f"      {pid}: {a} vs {b}")


if __name__ == "__main__":
    main()
