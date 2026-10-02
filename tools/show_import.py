"""Zeigt die zuletzt per Lesezeichen übertragene eigene GTCHA-Seite in Kurzform.

Aufruf auf dem VPS:
    docker exec -i gtcha-app python - < tools/show_import.py          # letzter Import
    docker exec -i gtcha-app python - 3 < tools/show_import.py        # drittletzter
"""

import json
import re
import sqlite3
import sys
from collections import Counter

nth = int(sys.argv[1]) if len(sys.argv) > 1 else 1
db = sqlite3.connect("/app/data/webapp.db")
row = db.execute("SELECT created_at, url, data FROM user_imports ORDER BY id DESC LIMIT 1 OFFSET ?", (nth - 1,)).fetchone()
if not row:
    sys.exit("Noch nichts übertragen.")
created, url, data = row
page = json.loads(data)
print(f"Import vom {created[:19]} · {url}\nTitel: {page.get('title')}\n")
print("--- Text (Anfang) ---")
print("\n".join(line for line in (page.get("text") or "").splitlines() if line.strip())[:2500])
print("\n--- Links (Muster, Anzahl, Beispiel) ---")
links = [(t, h) for t, h in page.get("links") or [] if h]
pattern = lambda h: re.sub(r"\d+", "#", h.split("?")[0]) + ("?" + re.sub(r"=\d+", "=#", h.split("?")[1]) if "?" in h else "")
counts = Counter(pattern(h) for _, h in links)
for pat, n in counts.most_common(25):
    example = next((t, h) for t, h in links if pattern(h) == pat)
    print(f"{n:4}× {pat}   z.B. „{example[0][:30]}“ -> {example[1][:80]}")
print("\n--- Bilder (Muster, Anzahl) ---")
imgs = Counter(pattern(src or "") for _, src in page.get("images") or [])
for pat, n in imgs.most_common(10):
    print(f"{n:4}× {pat[:100]}")
