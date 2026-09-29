"""Testet Header-, Sprach- und Proxy-Varianten gegen pack/detail, um den Pool-Auslöser zu finden.

Aufruf auf dem VPS (Host):
    python3 scripts/diagnose_variants.py 24125 [HANDY_IP ...]
"""

import json
import socket
import subprocess
import sys
import tempfile

HOST = "gtchaxonline.com"
WARP = "127.0.0.1:40000"
TOR = "127.0.0.1:9050"
UA = "Mozilla/5.0 (iPhone; CPU iPhone OS 18_0 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/18.0 Mobile/15E148 Safari/604.1"
IP_HEADERS = ("X-Forwarded-For", "X-Real-IP", "Client-IP", "True-Client-IP", "X-Client-IP", "Forwarded")


def curl(args, timeout=60):
    r = subprocess.run(["curl", "-sS", "--max-time", str(timeout)] + args, capture_output=True, text=True)
    return r.stdout or r.stderr.strip()


def port_open(addr):
    host, port = addr.split(":")
    try:
        with socket.create_connection((host, int(port)), timeout=2):
            return True
    except OSError:
        return False


def egress_ip(proxy):
    args = ["--socks5-hostname", proxy] if proxy else []
    trace = curl(args + ["https://www.cloudflare.com/cdn-cgi/trace"])
    return next((l[3:] for l in trace.splitlines() if l.startswith("ip=")), "?")


def pack_count(pid, headers=(), warp=False, lang="de-DE,de;q=0.9", proxy=None, circuit=None):
    if warp:
        proxy = WARP
    ip = ""
    with tempfile.NamedTemporaryFile() as jar:
        base = ["-c", jar.name, "-b", jar.name, "-A", UA, "-H", f"Accept-Language: {lang}"]
        for h in headers:
            base += ["-H", h]
        if proxy:
            base += ["--socks5-hostname", proxy]
        if circuit:
            base += ["--proxy-user", f"{circuit}:x"]
            trace = curl(base + ["https://www.cloudflare.com/cdn-cgi/trace"])
            ip = next((l[3:] for l in trace.splitlines() if l.startswith("ip=")), "?")
        curl(base + ["-o", "/dev/null", f"https://{HOST}/"])
        body = ""
        for _ in range(2):
            body = curl(base + ["-H", "Accept: application/json", f"https://{HOST}/api/user/pack/detail/{pid}"])
            if body.startswith("{"):
                break
        point = curl(base + ["-H", "Accept: application/json", f"https://{HOST}/api/user/point"])
    try:
        count = json.loads(body)["detail"]["pack_count"]
    except Exception:
        count = f"? ({' '.join(body.split())[:90]})"
    try:
        p = json.loads(point)
        extra = f"lang={p.get('language')} country_id={p.get('country_id')}"
    except Exception:
        extra = ""
    return f"{count:<8} {extra}" + (f"  (Tor-IP {ip})" if ip else "")


def ip_headers(ip):
    return [f"{h}: {ip}" if h != "Forwarded" else f"Forwarded: for={ip}" for h in IP_HEADERS]


def main():
    args = [a for a in sys.argv[1:] if a != "--nur-tor"]
    only_tor = "--nur-tor" in sys.argv
    pid = args[0] if args else "24125"
    phone_ips = args[1:]
    variants = []
    if not only_tor:
        variants = [
            ("Direkt (Contabo-IP)", {}),
            ("WARP", {"warp": True}),
            ("Sprache ja", {"lang": "ja-JP,ja;q=0.9"}),
            ("IP-Header DE Telekom", {"headers": ip_headers("217.237.150.100")}),
        ]
        for ip in phone_ips:
            variants.append((f"IP-Header Handy {ip}", {"headers": ip_headers(ip)}))
            variants.append((f"Nur X-Forwarded-For {ip}", {"headers": [f"X-Forwarded-For: {ip}"]}))
        print(f"Ausgangs-IP direkt: {egress_ip(None)} | WARP: {egress_ip(WARP)}")
    print(f"Banner {pid} - pack_count je Variante:")
    for label, kw in variants:
        print(f"  {label:<40} {pack_count(pid, **kw)}", flush=True)

    if not port_open(TOR):
        print(f"Tor nicht erreichbar ({TOR}) - Tor-Test übersprungen")
        return
    print("Tor-Verbindungscheck (max. 45 s)...", flush=True)
    check = curl(["--socks5-hostname", TOR, "https://check.torproject.org/api/ip"], timeout=45)
    print(f"  {' '.join(check.split())[:150]}", flush=True)
    if '"IsTor":true' not in check.replace(" ", ""):
        print("  Tor baut keine Verbindung auf - Tor-Test übersprungen")
        return
    for i in (1, 2, 3):
        print(f"  Tor #{i} läuft...", flush=True)
        print(f"  {'Tor #' + str(i):<40} {pack_count(pid, proxy=TOR, circuit=f'diag{i}')}", flush=True)


if __name__ == "__main__":
    main()
