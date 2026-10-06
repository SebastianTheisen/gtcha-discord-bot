"""Treffsicherheit der Ø Rückgabe: Vorhersage gegenüber dem, was tatsächlich aus dem Banner kam.

Die App speichert regelmäßig je Banner die vorhergesagte Ø Rückgabe (%), die übrigen Packs und den Wert,
der laut Seite schon raus ist (umgewandelt + verschickt als Kartenwert). Daraus:
  Vorhersage = Ø Rückgabe, gewichtet mit den in jedem Abschnitt verkauften Packs
  Ergebnis   = Zuwachs "raus" / (verkaufte Packs × Preis)
Gezogene Karten zählt die Seite erst, wenn sie verschickt oder umgewandelt werden. Deshalb wird das Ergebnis
mit LAG_SECONDS Versatz gemessen: für die Packs, die bis vor 24 Stunden verkauft wurden, zählt, was jeweils
24 Stunden später raus war. Beendete Banner (Archiv) werden dafür noch weiter mitgeschrieben.
"""

import time
from typing import Dict, List, Optional

import aiosqlite

SNAPSHOT_SECONDS = 30 * 60   # spätestens alle 30 Minuten ein Stand je Banner
SNAPSHOT_EV_STEP = 3.0       # oder sobald sich die Ø Rückgabe um 3 Prozentpunkte ändert
KEEP_DAYS = 90
MIN_SOLD = 30                # erst ab so vielen verkauften Packs vergleichen
REPORT_LIMIT = 30
LAG_SECONDS = 24 * 3600      # Ergebnis 24 Stunden später messen (Karten werden erst später verschickt/umgewandelt)

SCHEMA = """
CREATE TABLE IF NOT EXISTS ev_history (
    banner_id INTEGER, t INTEGER, ev_pct REAL, remaining INTEGER, out_value INTEGER, price INTEGER, title TEXT);
CREATE INDEX IF NOT EXISTS ev_history_banner ON ev_history (banner_id, t);
"""


class AccuracyStore:
    def __init__(self, db_path: str):
        self.db_path = db_path
        self._last: Dict[int, tuple] = {}

    async def init(self):
        async with aiosqlite.connect(self.db_path) as db:
            await db.executescript(SCHEMA)
            await db.execute("DELETE FROM ev_history WHERE t < ?", (int(time.time()) - KEEP_DAYS * 86400,))
            cur = await db.execute("SELECT banner_id, max(t), ev_pct FROM ev_history GROUP BY banner_id")
            self._last = {b: (t, ev) for b, t, ev in await cur.fetchall()}
            await db.commit()

    async def record(self, banners: List[Dict], now: Optional[float] = None) -> int:
        """Neuen Stand je Banner speichern, wenn genug Zeit vergangen ist oder sich die % deutlich ändern."""
        now = int(now or time.time())
        rows = []
        for b in banners:
            ev, remaining = b.get("ev_pct"), b.get("remaining")
            # beendete Banner: ohne Vorhersage, nur noch "raus" mitschreiben (für den Versatz)
            if (ev is None and not b.get("archived")) or not b.get("price") or remaining is None:
                continue
            last = self._last.get(b["id"])
            changed = ev is not None and last and last[1] is not None and abs(ev - last[1]) >= SNAPSHOT_EV_STEP
            if last and now - last[0] < SNAPSHOT_SECONDS and not changed:
                continue
            out = None
            if b.get("converted") is not None:
                out = int(b["converted"]) + int(b.get("ship_value") or 0)
            rows.append((b["id"], now, ev, remaining, out, b["price"], b.get("title")))
            self._last[b["id"]] = (now, ev)
        if rows:
            async with aiosqlite.connect(self.db_path) as db:
                await db.executemany("INSERT INTO ev_history VALUES (?, ?, ?, ?, ?, ?, ?)", rows)
                await db.commit()
        return len(rows)

    async def history(self, banner_id: int) -> List[Dict]:
        async with aiosqlite.connect(self.db_path) as db:
            cur = await db.execute("SELECT t, ev_pct FROM ev_history WHERE banner_id = ? ORDER BY t", (banner_id,))
            return [{"t": t, "ev": ev} for t, ev in await cur.fetchall()]

    async def report(self) -> Dict:
        async with aiosqlite.connect(self.db_path) as db:
            cur = await db.execute("SELECT banner_id, t, ev_pct, remaining, out_value, price, title FROM ev_history "
                                   "WHERE out_value IS NOT NULL ORDER BY banner_id, t")
            rows = await cur.fetchall()
        by_banner: Dict[int, list] = {}
        for r in rows:
            by_banner.setdefault(r[0], []).append(r)
        return evaluate(by_banner)


def evaluate(by_banner: Dict[int, list], lag: int = LAG_SECONDS) -> Dict:
    """Je Banner Vorhersage gegen Ergebnis; dazu Ø Abweichung und Richtung über alle Banner.

    Verglichen werden die Packs, die bis `lag` vor dem letzten Stand verkauft wurden; als Ergebnis zählt der
    Zuwachs "raus" bis `lag` nach dem letzten dieser Verkäufe (Karten werden erst nach dem Ziehen verschickt
    oder umgewandelt)."""
    items = []
    for bid, snaps in by_banner.items():
        snaps = clean_snapshots(snaps)
        if len(snaps) < 2:
            continue
        last_t = snaps[-1][1]
        window = [s for s in snaps if s[1] <= last_t - lag]
        if len(window) < 2:
            continue
        first, end = window[0], window[-1]
        sold = first[3] - end[3]
        price = end[5]
        if sold < MIN_SOLD or not price:
            continue

        def out_at(t):
            return next((s[4] for s in snaps if s[1] >= t), snaps[-1][4])

        weighted = sum((a[2] or 0) * max(0, a[3] - b[3]) for a, b in zip(window, window[1:]))
        predicted = weighted / sold
        # Anfang: Stand beim ersten Eintrag; Ende: was `lag` nach dem letzten verglichenen Verkauf raus war
        realized = (out_at(end[1] + lag) - first[4]) / (sold * price) * 100
        items.append({"id": bid, "title": snaps[-1][6], "sold": sold, "price": price, "predicted": round(predicted, 1),
                      "realized": round(realized, 1), "diff": round(predicted - realized, 1), "t": end[1]})
    items.sort(key=lambda x: -x["t"])
    items = items[:REPORT_LIMIT]
    n = len(items)
    # gewichtet nach eingesetzten Coins (Packs × Preis): kleine Banner (ein Hit = riesiger Ausschlag) und große
    # Billig-Banner (zehntausende Packs zu wenigen Coins) verzerren den Gesamtwert nicht
    spent = sum(i["sold"] * i["price"] for i in items)
    w_pred = round(sum(i["predicted"] * i["sold"] * i["price"] for i in items) / spent, 1) if spent else None
    w_real = round(sum(i["realized"] * i["sold"] * i["price"] for i in items) / spent, 1) if spent else None
    sold_all = sum(i["sold"] for i in items)
    return {"items": items, "count": n,
            "mean_abs": round(sum(abs(i["diff"]) for i in items) / n, 1) if n else None,
            "bias": round(sum(i["diff"] for i in items) / n, 1) if n else None,
            "weighted_predicted": w_pred, "weighted_realized": w_real, "sold": sold_all, "spent": spent}


def clean_snapshots(snaps: list) -> list:
    """Kurze Ausreißer der Seite entfernen (z. B. 134 -> 432 -> 134 Packs): Packs können nur sinken - ein Stand
    über dem vorigen fliegt raus."""
    out = []
    for s in snaps:
        if out and s[3] > out[-1][3]:
            continue
        out.append(s)
    return out
