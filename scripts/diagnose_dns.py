"""Prüft, ob hinter gtchaxonline.com mehrere Server mit unterschiedlichen Pack-Zahlen stehen.

Aufruf auf dem VPS (Host):
    python3 scripts/diagnose_dns.py 24125
"""

import json
import socket
import subprocess
import sys
import tempfile

HOST = "gtchaxonline.com"
UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
DOH = {
    "Cloudflare": "https://cloudflare-dns.com/dns-query?name={}&type={}",
    "Google": "https://dns.google/resolve?name={}&type={}",
}


def curl(args):
    return subprocess.run(["curl", "-s", "--max-time", "20"] + args, capture_output=True, text=True).stdout


def resolve():
    found = {}
    try:
        for info in socket.getaddrinfo(HOST, 443, proto=socket.IPPROTO_TCP):
            found.setdefault(info[4][0], set()).add("VPS-System")
    except Exception as e:
        print(f"System-DNS Fehler: {e}")
    for name, url in DOH.items():
        for rtype in ("A", "AAAA", "CNAME"):
            try:
                data = json.loads(curl(["-H", "Accept: application/dns-json", url.format(HOST, rtype)]))
            except Exception:
                continue
            for ans in data.get("Answer", []):
                if ans.get("type") in (1, 28):
                    found.setdefault(ans["data"], set()).add(name)
                elif ans.get("type") == 5:
                    print(f"CNAME ({name}): {ans['data']}")
    return found


def pack_count(ip, pid):
    target = f"[{ip}]" if ":" in ip else ip
    resolve_arg = ["--resolve", f"{HOST}:443:{target}"]
    with tempfile.NamedTemporaryFile() as jar:
        base = resolve_arg + ["-c", jar.name, "-b", jar.name, "-A", UA, "-H", "Accept-Language: de-DE,de;q=0.9"]
        headers = curl(base + ["-D", "-", "-o", "/dev/null", f"https://{HOST}/"])
        body = curl(base + ["-H", "Accept: application/json", "-H", "X-Requested-With: XMLHttpRequest",
                            f"https://{HOST}/api/user/pack/detail/{pid}"])
    server = next((l.split(":", 1)[1].strip() for l in headers.splitlines()
                   if l.lower().startswith(("server:", "x-served-by:", "x-backend"))), "?")
    try:
        detail = json.loads(body).get("detail", {})
        return f"pack_count={detail.get('pack_count')} / {detail.get('total_pack_count')}  (server: {server})"
    except Exception:
        return f"keine JSON-Antwort: {' '.join(body.split())[:120]!r}  (server: {server})"


def main():
    pid = sys.argv[1] if len(sys.argv) > 1 else "24125"
    ips = resolve()
    print(f"\n{len(ips)} Server-IP(s) für {HOST}:")
    for ip, sources in ips.items():
        print(f"  {ip:<40} gefunden über: {', '.join(sorted(sources))}")
    print(f"\nBanner {pid} je Server:")
    for ip in ips:
        for attempt in (1, 2):
            print(f"  {ip:<40} #{attempt}: {pack_count(ip, pid)}", flush=True)


if __name__ == "__main__":
    main()
