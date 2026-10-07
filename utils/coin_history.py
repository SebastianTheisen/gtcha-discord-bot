"""Coin-Banner Schub für Schub: Umwandlungen der Seite (alle 30 Minuten, um voll und um halb) gegen die Packs,
die in genau diesem Zeitfenster gezogen wurden.

Geht ein Schub glatt auf (5 Packs, 7.500 Coins = 5 × 1.500), waren es nur die günstigsten Coins. Bleibt ein Rest,
steckt ein teurerer Coin in genau diesem Schub. Alle Schübe zusammen: jeder teurere Coin kann nur einmal gezogen
werden - wie beim Versand klären spätere Schübe frühere ❓ auf. Schübe, die zu nichts passen (z. B. ein Zug genau
an der Grenze), werden mit dem nächsten zusammengelegt, notfalls übergangen.
"""

from datetime import datetime, timedelta, timezone
from typing import Dict, List, Optional, Set, Tuple

from utils.card_pool import _coin_cards, _summarize_options, _value_classes, medal_units

RUN_MINUTES = 30   # die Seite zählt Umwandlungen um voll und um halb


def _ts(iso: str) -> Optional[datetime]:
    try:
        return datetime.fromisoformat(iso)
    except (TypeError, ValueError):
        return None


def run_start(t: datetime) -> datetime:
    """Lauf der Seite, zu dem eine erkannte Änderung gehört (auf volle/halbe Stunde abgerundet)."""
    return t.replace(minute=t.minute - t.minute % RUN_MINUTES, second=0, microsecond=0)


def coin_intervals(conversions: List[Tuple[str, int, int]], moves: List[Tuple[str, int, int]],
                   total: int, current: int) -> List[Dict]:
    """Schübe: [{"drawn": gezogene Packs im Fenster, "coins": umgewandelt im Fenster, "t": Lauf (Unix)}].

    conversions: (Zeit, alt, neu) aus convert_history; moves: (Zeit, alt, neu) aus pack_history, beide sortiert.
    Der erste Schub enthält alles seit Banner-Start (vor der Aufzeichnung). Packs nach dem letzten Lauf zählen
    noch nicht (deren Coins kommen erst im nächsten Lauf)."""
    runs: List[Tuple[datetime, int, int]] = []   # (Lauf, Coins vorher, Coins nachher) - gleiche Läufe zusammen
    for at, old, new in conversions:
        t = _ts(at)
        if t is None or old is None or new is None:
            continue
        start = run_start(t)
        if runs and runs[-1][0] == start:
            runs[-1] = (start, runs[-1][1], new)
        else:
            runs.append((start, old, new))
    if not runs:
        return []
    parsed = [(t, old, new) for t, old, new in ((_ts(a), o, n) for a, o, n in moves) if t is not None]

    def packs_at(t: datetime) -> int:
        before = [new for at, _, new in parsed if at <= t]
        if before:
            return before[-1]
        return parsed[0][1] if parsed else current

    out, prev_packs, prev_coins = [], total, 0
    for start, _, new in runs:
        packs = packs_at(start)
        out.append({"drawn": max(0, prev_packs - packs), "coins": new - prev_coins,
                    "t": int(start.replace(tzinfo=timezone.utc).timestamp())})
        prev_packs, prev_coins = packs, new
    return out


def match_coin_history(pool: Dict, intervals: List[Dict], hits_possible: int = 0,
                       required: Set[str] = frozenset()) -> Optional[Dict]:
    """Teurere Coins aus den Schüben. hits_possible: Versand-Hits, die im Banner stecken (gemischte Banner) - so
    viele gezogene Packs eines Fensters können Versand-Hits statt Coins gewesen sein (höchstens die Packs des
    Fensters). Ergebnis wie match_shipped_hits plus "used"/"skipped" (Schübe), None ohne Coins/Schübe."""
    coins = _coin_cards(pool)
    if not coins or not intervals:
        return None
    base = min(int(c["value"]) for c in coins)
    coin_ids = {str(c.get("id")) for c in coins}
    hits = [u for u in medal_units(pool) if not u["shipping_only"] and u["value"] > base
            and str(u["key"]).split("#")[0] in coin_ids]
    if not hits:
        return {"certain": [], "groups": [], "maybe": [], "used": len(intervals), "skipped": 0}
    classes = _value_classes(hits, 0)
    sizes = [len(c["keys"]) for c in classes]
    extras = [c["min"] - base for c in classes]

    def options(drawn: int, value: int) -> Set[tuple]:
        lo = max(0, drawn - min(drawn, hits_possible))
        found: Set[tuple] = set()

        def walk(i, taken, extra, count):
            if count > drawn or extra > value or len(found) > 5000:
                return
            if i == len(classes):
                rest = value - extra
                if rest % base == 0 and max(lo, count) <= rest // base <= drawn:
                    found.add(tuple(taken))
                return
            for m in range(0, sizes[i] + 1):
                walk(i + 1, taken + [m], extra + m * extras[i], count + m)

        walk(0, [], 0, 0)
        return found

    states = {tuple([0] * len(classes))}
    used = skipped = 0
    pending = None   # Schub, der allein nicht aufging - wird mit dem nächsten zusammengelegt
    for iv in intervals:
        drawn, value = iv["drawn"], iv["coins"]
        if pending:
            drawn, value = drawn + pending["drawn"], value + pending["coins"]
        opts = options(drawn, value)
        combined = {tuple(a + b for a, b in zip(s, o)) for s in states for o in opts}
        combined = {s for s in combined if all(x <= n for x, n in zip(s, sizes))}
        if combined:
            states, pending = combined, None
            used += 1
        elif pending is None:
            pending = {"drawn": drawn, "coins": value}   # z. B. Zug genau an der Grenze: mit dem nächsten Schub
        else:
            pending = None
            skipped += 1   # passt auch zusammen nicht - übergehen statt falsch zuordnen
    if pending:
        skipped += 1
    need = [sum(1 for k in required if k in c["keys"]) for c in classes]
    with_required = [s for s in states if all(x >= r for x, r in zip(s, need))]
    result = _summarize_options(classes, with_required or list(states), hits)
    result.update(used=used, skipped=skipped)
    return result
