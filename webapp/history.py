"""Eigener GTCHA-Verlauf aus den per Lesezeichen übertragenen Seiten.

Liest den Text der eigenen Verlaufsseiten (keine Schnittstellen der Seite):
  buy-point-history  Münzen: Öffnungen (-1.000 …) und Umwandlungen (+660 …) mit Uhrzeit (JST)
  pending-detail     angeforderte, noch nicht verschickte Karten
  shipped-detail     verschickte Karten (mit Sendungsnummer)
  change-member      "Ausgaben in diesem Monat 8.000円", Coin-Stand
Züge werden einem Banner nur zugeordnet, wenn es eindeutig ist (Preis + Pack-Bewegung in derselben
Minute + Wert der umgewandelten Karte im Kartenpool).
"""

import re
from bisect import bisect_left
from collections import defaultdict
from datetime import datetime, timedelta
from typing import Dict, List

DATE_TIME = re.compile(r"^(\d{4})/(\d{2})/(\d{2}) (\d{2}):(\d{2})$")
DATE = re.compile(r"(\d{2,4})/(\d{2})/(\d{2})")
NUMBER = re.compile(r"^-?[\d.]+$")
JST_OFFSET = timedelta(hours=9)
MOVE_WINDOW = timedelta(minutes=7)   # Scrape alle 5 Minuten + Laufzeit
OPEN_NOTES = ("öffnen", "open")
CONVERT_NOTES = ("umwandeln", "convert")
BUY_NOTES = ("erhalten", "kauf", "purchase", "charge")
HEADER = re.compile(r"^(Liste der versendeten Artikel|Anfragedatum|Sendungsnummer|Börsenhistorie)", re.I)


def _unique(pages: List[Dict]) -> List[Dict]:
    """Doppelt eingelesene Seiten (Blättern hat nicht gegriffen) nur einmal zählen."""
    seen, out = set(), []
    for page in pages:
        key = page.get("text") or ""
        if key not in seen:
            seen.add(key)
            out.append(page)
    return out


def _int(text: str) -> int:
    return int(text.replace(".", "").replace(",", ""))


def _lines(page: Dict) -> List[str]:
    return [line.strip() for line in (page.get("text") or "").splitlines() if line.strip()]


def parse_coins(pages: List[Dict]) -> List[Dict]:
    """Münzen-Verlauf: [{t (JST, naiv), amount, yen, note, kind}] in Seitenreihenfolge (neueste zuerst)."""
    events = []
    for page in _unique(pages):
        lines = _lines(page)
        i = 0
        while i < len(lines):
            m = DATE_TIME.match(lines[i])
            if m and i + 1 < len(lines) and NUMBER.match(lines[i + 1]):
                t = datetime(*map(int, m.groups()))
                amount = _int(lines[i + 1])
                yen = lines[i + 2] if i + 2 < len(lines) and lines[i + 2].startswith(("¥", "￥")) else ""
                note = lines[i + 3] if yen and i + 3 < len(lines) else (lines[i + 2] if i + 2 < len(lines) else "")
                low = note.lower()
                kind = ("open" if any(n in low for n in OPEN_NOTES) else
                        "convert" if any(n in low for n in CONVERT_NOTES) else
                        "buy" if any(n in low for n in BUY_NOTES) and amount > 0 else "other")
                events.append({"t": t, "amount": amount, "yen": _int(re.sub(r"[^\d.]", "", yen) or "0"),
                               "note": note, "kind": kind})
                i += 4 if yen else 3
                continue
            i += 1
    return events


def parse_cards(pages: List[Dict]) -> List[Dict]:
    """Karten aus pending/shipped: [{date, name, rarity, number, tracking, image}]."""
    """Jeder Artikel ist ein durch Leerzeilen getrennter Block (meist Name, Rarität, Nummer);
    Kopfzeilen (Datum, Sendungsnummer) gelten für die folgenden Blöcke. Ein Bild pro Artikel."""
    cards = []
    for page in _unique(pages):
        images = [src for src in page.get("images") or [] if "/card/" in (src or "")]
        items, block, date, tracking, started = [], None, None, None, False

        def close():
            if block:
                items.append({"date": date, "tracking": tracking, "lines": list(block)})

        for raw in (page.get("text") or "").split("\n"):
            line = raw.strip()
            if line.startswith("Xero Place"):
                break
            header = HEADER.match(line)
            if header:
                started = True
                close()
                block = None
                if line.lower().startswith("sendungsnummer"):
                    tracking = line.split(":", 1)[-1].strip()
                elif DATE.search(line):
                    y, mo, d = DATE.search(line).groups()
                    date = f"{int(y) + 2000 if len(y) == 2 else int(y)}-{mo}-{d}"
                continue
            if not started:
                continue
            if not line:
                close()
                block = []
            elif block is not None:
                block.append(line)
        close()
        page_cards = []
        for item in items:
            lines = item.pop("lines")
            if len(lines) == 3:
                card = {**item, "name": lines[0], "rarity": lines[1], "number": lines[2]}
            else:
                card = {**item, "name": " ".join(lines), "rarity": "", "number": ""}
            page_cards.append(card)
        if len(images) == len(page_cards):
            for card, src in zip(page_cards, images):
                m = re.search(r"/card/(\d+)", src)
                card["image"], card["card_id"] = src, (m.group(1) if m else None)
        cards += page_cards
    return cards


def parse_member(pages: List[Dict]) -> Dict:
    """Coin-Stand und Ausgaben dieses Monats (Yen)."""
    info = {"coins": None, "spent_month_yen": None}
    for page in pages:
        lines = _lines(page)
        for j, line in enumerate(lines):
            if info["coins"] is None and NUMBER.match(line):
                info["coins"] = _int(line)
            if "ausgaben" in line.lower() and j + 1 < len(lines):
                m = re.search(r"([\d.,]+)\s*[円¥￥]", lines[j + 1] + " " + line)
                if m:
                    info["spent_month_yen"] = _int(m.group(1))
    return info


def attribute_opens(events: List[Dict], banners: Dict[int, Dict], moves: Dict[int, List[datetime]],
                    window: timedelta = MOVE_WINDOW) -> None:
    """Ordnet Öffnungen einem Banner zu (setzt e["banner"], e["pulls"]) - nur wenn eindeutig.

    banners: pack_id -> {price, values (Kartenwerte)}; moves: pack_id -> Zeitpunkte (UTC) mit weniger Packs.
    Zeiten der Seite sind JST und minutengenau; der Bot sieht die Pack-Bewegung erst beim nächsten Scrape.
    Kandidat = Preis teilt den Betrag, Pack-Bewegung kurz danach und - bei einem einzelnen Zug - der Wert
    der direkt folgenden Umwandlung kommt im Kartenpool vor. Bleiben mehrere Kandidaten, entscheidet
    eine eindeutig zugeordnete Öffnung direkt davor/danach (gleiche Sitzung) - sonst "nicht zugeordnet".
    Umwandlungen direkt nach einer zugeordneten Öffnung zählen zum selben Banner.
    """
    chrono = list(reversed(events))   # Seite: neueste zuerst
    sorted_moves = {pid: sorted(times) for pid, times in moves.items() if times}
    opens = []
    for pos, e in enumerate(chrono):
        if e["kind"] != "open" or e["amount"] >= 0:
            continue
        cost = -e["amount"]
        utc = e["t"] - JST_OFFSET
        value = None
        for nxt in chrono[pos + 1:pos + 3]:
            if nxt["kind"] == "open":
                break
            if nxt["kind"] == "convert" and nxt["t"] - e["t"] <= timedelta(minutes=2):
                value = nxt["amount"]
                break
        candidates = []
        lo, hi = utc - timedelta(seconds=60), utc + window
        for pid, b in banners.items():
            price = b.get("price") or 0
            if not price or cost % price:
                continue
            times = sorted_moves.get(pid)
            if not times:
                continue
            i = bisect_left(times, lo)    # erste Bewegung ab lo - liegt sie vor hi, passt es
            if i == len(times) or times[i] > hi:
                continue
            if value is not None and cost == price and b.get("values") and value not in b["values"]:
                continue
            candidates.append(pid)
        e["candidates"] = candidates
        if len(candidates) == 1:
            e["banner"] = candidates[0]
        opens.append(e)
    # gleiche Sitzung: Nachbar-Öffnung (≤ 5 Min) eindeutig und unter den Kandidaten
    for i, e in enumerate(opens):
        if e.get("banner") or len(e["candidates"]) < 2:
            continue
        near = {n["banner"] for n in (opens[i - 1] if i else None, opens[i + 1] if i + 1 < len(opens) else None)
                if n and n.get("banner") and abs(n["t"] - e["t"]) <= timedelta(minutes=5)}
        if len(near) == 1 and next(iter(near)) in e["candidates"]:
            e["banner"], e["guessed"] = next(iter(near)), True
    current = None
    for e in chrono:
        if e["kind"] == "open":
            current = e if e.get("banner") else None
            if current:
                e["pulls"] = -e["amount"] // banners[e["banner"]]["price"]
        elif e["kind"] == "convert" and current and e["t"] - current["t"] <= timedelta(minutes=2):
            e["banner"] = current["banner"]
        elif e["kind"] != "convert":
            current = None


def summarize(events: List[Dict]) -> Dict:
    """Ausgaben/Rückgabe/Bilanz gesamt, pro Tag und pro Banner (Coins)."""
    total = {"spent": 0, "returned": 0, "opens": 0, "bought": 0, "bought_yen": 0}
    days = defaultdict(lambda: {"spent": 0, "returned": 0, "opens": 0})
    per_banner = defaultdict(lambda: {"spent": 0, "pulls": 0, "returned": 0})
    for e in events:
        day = days[e["t"].strftime("%Y-%m-%d")]
        if e["kind"] == "open" and e["amount"] < 0:
            for bucket in (total, day):
                bucket["spent"] += -e["amount"]
                bucket["opens"] += 1
            if e.get("banner"):
                per_banner[e["banner"]]["spent"] += -e["amount"]
                per_banner[e["banner"]]["pulls"] += e.get("pulls", 1)
        elif e["kind"] == "buy":
            total["bought"] += e["amount"]
            total["bought_yen"] += e["yen"]
        elif e["kind"] == "convert" and e["amount"] > 0:
            total["returned"] += e["amount"]
            day["returned"] += e["amount"]
            if e.get("banner"):
                per_banner[e["banner"]]["returned"] += e["amount"]
    total["balance"] = total["returned"] - total["spent"]
    unassigned = sum(1 for e in events if e["kind"] == "open" and e["amount"] < 0 and not e.get("banner"))
    times = [e["t"] for e in events]
    return {"total": total, "unassigned_opens": unassigned,
            "since": min(times).strftime("%Y-%m-%d %H:%M") if times else None,
            "days": [{"day": d, **v, "balance": v["returned"] - v["spent"]} for d, v in sorted(days.items(), reverse=True)],
            "banners": [{"banner": b, **v, "balance": v["returned"] - v["spent"]}
                        for b, v in sorted(per_banner.items(), key=lambda x: -x[1]["spent"])]}


TIME_FMT = "%Y-%m-%d %H:%M"
COIN_KEY = ("t", "amount", "note")
CARD_KEY = ("date", "tracking", "name", "number")


def merge_newest_first(old: List[Dict], new: List[Dict], key: tuple) -> tuple:
    """Neue Einträge vor die gespeicherten setzen (beide neueste zuerst).

    Das Lesezeichen hört auf zu blättern, sobald eine Seite den zuletzt bekannten Eintrag enthält - die
    neuen Daten enden also mit einem Anfang der alten Liste. Den längsten solchen Überlapp abschneiden.
    Rückgabe: (zusammengeführt, Überlapp gefunden). Ohne Überlapp fehlt evtl. etwas dazwischen.
    """
    if not old:
        return list(new), True
    sig = lambda e: tuple(e.get(k) for k in key)
    new_s, old_s = [sig(e) for e in new], [sig(e) for e in old]
    for k in range(min(len(new_s), len(old_s)), 0, -1):
        if new_s[-k:] == old_s[:k]:
            return list(new[:-k]) + list(old), True
    return list(new) + list(old), not new


def ingest(stored: Dict[str, Dict], entries: List[Dict]) -> Dict[str, Dict]:
    """Ein "Alles übertragen" in den gespeicherten Verlauf übernehmen.

    entries: [{path, pages, partial}] - partial = nur die neuesten Seiten (Lesezeichen hat am bekannten
    Eintrag aufgehört), sonst vollständig (ersetzt das Gespeicherte). Rückgabe: geänderte Bereiche
    {coins|shipped|pending|member: {items/info, gap}}.
    """
    changed = {}
    for entry in entries:
        path, pages, partial = entry.get("path"), entry.get("pages") or [], bool(entry.get("partial"))
        if path == "buy-point-history":
            area, key = "coins", COIN_KEY
            new = [{**e, "t": e["t"].strftime(TIME_FMT)} for e in parse_coins(pages)]
        elif path == "shipped-detail":
            area, key, new = "shipped", CARD_KEY, parse_cards(pages)
        elif path == "pending-detail":
            changed["pending"] = {"items": parse_cards(pages), "gap": False}
            continue
        elif path == "change-member":
            info = {k: v for k, v in parse_member(pages).items() if v is not None}
            changed["member"] = {"info": {**((stored.get("member") or {}).get("info") or {}), **info}}
            continue
        else:
            continue
        old = (stored.get(area) or {}).get("items") or []
        if partial:
            items, ok = merge_newest_first(old, new, key)
            gap = (stored.get(area) or {}).get("gap", False) or not ok
        else:
            items, gap = new, False
        changed[area] = {"items": items, "gap": gap}
    return changed


def build_history(areas: Dict[str, Dict], banners: Dict[int, Dict], moves: Dict[int, List[datetime]]) -> Dict:
    """Auswertung eines vollständigen "Alles übertragen"-Laufs (path -> {pages})."""
    stored = ingest({}, [{"path": p, "pages": a.get("pages") or []} for p, a in areas.items()])
    return build_from_stored(stored, banners, moves)


def stored_events(stored: Dict[str, Dict]) -> List[Dict]:
    return [{**e, "t": datetime.strptime(e["t"], TIME_FMT)} for e in (stored.get("coins") or {}).get("items") or []]


def build_from_stored(stored: Dict[str, Dict], banners: Dict[int, Dict], moves: Dict[int, List[datetime]]) -> Dict:
    """Auswertung aus dem gespeicherten (zusammengeführten) Verlauf."""
    events = stored_events(stored)
    attribute_opens(events, banners, moves)
    summary = summarize(events)
    for row in summary["banners"]:
        b = banners.get(row["banner"]) or {}
        row.update(title=b.get("title"), image=b.get("image"), price=b.get("price"))
    return {
        "member": (stored.get("member") or {}).get("info") or {"coins": None, "spent_month_yen": None},
        "summary": summary,
        "pending": (stored.get("pending") or {}).get("items") or [],
        "shipped": (stored.get("shipped") or {}).get("items") or [],
        "gap": any((stored.get(a) or {}).get("gap") for a in ("coins", "shipped")),
        "events": [{k: v for k, v in {**e, "t": e["t"].strftime(TIME_FMT)}.items() if k != "candidates"}
                   for e in events[:300]],
    }


def plan_claims(cards: List[Dict], banners: Dict[int, Dict], targets: Dict[int, Dict], user_id: str,
                done: set) -> List[Dict]:
    """Automatische Medaillen für angeforderte Karten: nur wenn die Karte in genau einem Banner vorkommt,
    das beim Ziehen schon lief (Anfragedatum ≥ erster Tag des Banners), dieses Banner noch aktiv ist,
    die Karte dort meldbar ist (ab Packpreis) und noch ein Platz dieser Karte frei ist.
    Hat die Person dort schon eine Medaille auf dieser Karte, wird nichts gemeldet.

    targets: pack_id -> {units (claimable_units), medals {tier: user_id}}; done: schon erledigte Schlüssel.
    Rückgabe: [{key, pack_id, tier, card}]
    """
    planned, used = [], defaultdict(set)
    for n, card in enumerate(cards):
        cid, date = card.get("card_id"), card.get("date")
        if not cid or not date:
            continue
        key = f"{cid}@{date}#{sum(1 for c in cards[:n] if c.get('card_id') == cid and c.get('date') == date)}"
        if key in done:
            continue
        seen = [pid for pid, b in banners.items() if cid in b.get("card_ids", ()) and (b.get("created") or "") <= date]
        if len(seen) != 1 or seen[0] not in targets:
            continue
        pid = seen[0]
        units = [u for u in targets[pid]["units"] if str(u["key"]).split("#")[0] == cid]
        medals = targets[pid]["medals"]
        if not units or any(str(medals.get(u["tier"])) == str(user_id) for u in units):
            continue
        free = [u for u in units if u["tier"] not in medals and u["tier"] not in used[pid]]
        if free:
            used[pid].add(free[0]["tier"])
            planned.append({"key": key, "pack_id": pid, "tier": free[0]["tier"], "card": card.get("name")})
    return planned
