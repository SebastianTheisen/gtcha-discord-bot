"""Testet Header-, Sprach- und Proxy-Varianten gegen pack/detail, um den Pool-Auslöser zu finden.

Aufruf auf dem VPS (Host):
    python3 scripts/diagnose_variants.py 24125 [DEINE_HANDY_IP ...]
"""

import json
import subprocess
import sys
import tempfile

HOST = "gtchaxonline.com"
WARP = "127.0.0.1:40000"
UA = "Mozilla/5.0 (iPhone; CPU iPhone OS 18_0 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/18.0 Mobile/15E148 Safari/604.1"
IP_HEADERS = ("X-Forwarded-For", "X-Real-IP", "Client-IP", "True-Client-IP", "X-Client-IP", "Forwarded")


def curl(args):
    return subprocess.run(["curl", "-s", "--max-time", "20"] + args, capture_output=True, text=True).stdout


def pack_count(pid, headers=(), warp=False, lang="de-DE,de;q=0.9"):
    with tempfile.NamedTemporaryFile() as jar:
        base = ["-c", jar.name, "-b", jar.name, "-A", UA, "-H", f"Accept-Language: {lang}"]
        for h in headers:
            base += ["-H", h]
        if warp:
            base += ["--socks5-hostname", WARP]
        curl(base + ["-o", "/dev/null", f"https://{HOST}/"])
        body = curl(base + ["-H", "Accept: application/json", f"https://{HOST}/api/user/pack/detail/{pid}"])
        point = curl(base + ["-H", "Accept: application/json", f"https://{HOST}/api/user/point"])
    try:
        count = json.loads(body)["detail"]["pack_count"]
    except Exception:
        count = f"? ({' '.join(body.split())[:60]})"
    try:
        p = json.loads(point)
        extra = f"lang={p.get('language')} country_id={p.get('country_id')}"
    except Exception:
        extra = ""
    return f"{count:<8} {extra}"


def ip_headers(ip):
    return [f"{h}: {ip}" if h != "Forwarded" else f"Forwarded: for={ip}" for h in IP_HEADERS]


def main():
    pid = sys.argv[1] if len(sys.argv) > 1 else "24125"
    phone_ips = sys.argv[2:]
    variants = [
        ("Direkt (Contabo-IP)", {}),
        ("WARP", {"warp": True}),
        ("Sprache ja", {"lang": "ja-JP,ja;q=0.9"}),
        ("Sprache en", {"lang": "en-US,en;q=0.9"}),
        ("IP-Header DE Telekom", {"headers": ip_headers("217.237.150.100")}),
        ("IP-Header DE Vodafone", {"headers": ip_headers("188.97.1.1")}),
        ("IP-Header JP", {"headers": ip_headers("126.0.0.1")}),
        ("IP-Header US", {"headers": ip_headers("8.8.8.8")}),
    ]
    for ip in phone_ips:
        variants.append((f"IP-Header Handy {ip}", {"headers": ip_headers(ip)}))
        variants.append((f"Nur X-Forwarded-For {ip}", {"headers": [f"X-Forwarded-For: {ip}"]}))
    print(f"Banner {pid} - pack_count je Variante:")
    for label, kw in variants:
        print(f"  {label:<40} {pack_count(pid, **kw)}", flush=True)


if __name__ == "__main__":
    main()
