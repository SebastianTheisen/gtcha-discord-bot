"""Wahrscheinlichkeiten für ❓-Gruppen: welcher Hit steckt wohl im Versand?

Rechnerisch passen bei einer ❓-Gruppe mehrere Hits (gleicher Wert). Abgehakt wird davon nie etwas - aber aus
dem zeitlichen Ablauf lässt sich schätzen, welcher es wahrscheinlich war:

  - Zeit vom Zug bis zum Versand: lernt der Bot aus Hits mit Medaille (Zugzeit ≈ Meldezeit), deren Versand
    sicher erkannt wurde. Ohne Beobachtungen gilt eine breite Startannahme (PRIOR).
  - Hit mit Medaille: Zugzeit bekannt -> Dichte der Verzögerung bis zu diesem Versand.
  - Hit ohne Medaille: Zugzeit unbekannt - jeder vorher verkaufte Pack kann ihn enthalten haben
    (Gewicht = verkaufte Packs / alle Packs, mal Dichte der Verzögerung ab diesem Verkauf).

Alle Zeiten sind Unix-Sekunden.
"""

import math
from typing import Dict, Iterable, List, Optional, Sequence, Set, Tuple

from utils.card_pool import explain_batch

# Verzögerung Zug -> Versand in Stunden: Grenzen der Klassen und schwache Startannahme (Pseudo-Zählungen)
BUCKETS = [0, 1, 6, 24, 72, 168, 336, 720]
PRIOR = [1, 1, 2, 2, 2, 1, 1]
MIN_SHARE = 0.03   # unter 3 % nicht anzeigen


def bucket(hours: float) -> Optional[int]:
    if hours < 0:
        return None
    for i in range(len(BUCKETS) - 1):
        if hours < BUCKETS[i + 1]:
            return i
    return len(BUCKETS) - 2


def counts_from(observations: Iterable[float]) -> List[int]:
    """Gelernte Klassen-Zählungen aus Verzögerungen (Stunden), ohne PRIOR."""
    counts = [0] * (len(BUCKETS) - 1)
    for h in observations:
        b = bucket(h)
        if b is not None:
            counts[b] += 1
    return counts


def density(hours: float, learned: Optional[Sequence[int]] = None) -> float:
    """Wahrscheinlichkeitsdichte (pro Stunde) für eine Verzögerung - PRIOR plus gelernte Zählungen."""
    b = bucket(hours)
    if b is None:
        return 0.0
    counts = [p + (learned[i] if learned and i < len(learned) else 0) for i, p in enumerate(PRIOR)]
    return counts[b] / sum(counts) / (BUCKETS[b + 1] - BUCKETS[b])


def weights(keys: Sequence[str], ship_t: float, medal_t: Dict[str, float], moves: Sequence[Tuple[float, int]],
            total_packs: int, learned: Optional[Sequence[int]] = None) -> Dict[str, float]:
    """Unnormiertes Gewicht je Kandidat: P(gezogen) × Dichte(Verzögerung bis zu diesem Versand)."""
    before = [(t, n) for t, n in moves if t <= ship_t and n > 0]
    sold = sum(n for _, n in before)
    total = max(total_packs or 0, sold, 1)
    unknown = sum(n / total * density((ship_t - t) / 3600, learned) for t, n in before)
    out = {}
    for k in keys:
        t = medal_t.get(k)
        if t is not None and t <= ship_t:
            out[k] = density((ship_t - t) / 3600, learned)
        elif t is not None:
            # erst nach dem Versand gemeldet: sicher vorher gezogen, Zeitpunkt unbekannt
            out[k] = unknown / (sold / total) if sold else 0.0
        else:
            out[k] = unknown
    return out


def group_odds(keys: Sequence[str], pulled: int, ship_t: Optional[float], medal_t: Dict[str, float],
               moves: Sequence[Tuple[float, int]], total_packs: int,
               learned: Optional[Sequence[int]] = None) -> Dict[str, float]:
    """Je Kandidat die Wahrscheinlichkeit (0-1), in diesem Versand zu stecken. Leer, wenn nicht schätzbar."""
    if not ship_t or not keys or pulled <= 0:
        return {}
    w = weights(keys, ship_t, medal_t, moves, total_packs, learned)
    s = sum(w.values())
    if s <= 0 or not math.isfinite(s):
        return {}
    return {k: min(1.0, pulled * v / s) for k, v in w.items()}


def batch_results(pool: Dict, shipments: List[Dict], price: Optional[int] = None) -> List[Dict]:
    """Jeder Versandschub (älteste zuerst, mit "t", "cards", "coins") mit explain_batch-Ergebnis; ein sicher
    erkannter Hit zählt in späteren Schüben nicht noch einmal."""
    sent: Set[str] = set()
    out = []
    for s in sorted(shipments, key=lambda s: s.get("t") or 0):
        res = explain_batch(pool, s["cards"], s["coins"], sent, price=price)
        sent |= set(res["certain"])
        out.append({**s, "res": res})
    return out


def observations(pool: Dict, shipments: List[Dict], medal_t: Dict[str, float],
                 price: Optional[int] = None) -> List[float]:
    """Gelernte Verzögerungen (Stunden): Hit mit Medaille, dessen Versand sicher erkannt wurde."""
    found = []
    for s in batch_results(pool, shipments, price):
        for k in s["res"]["certain"]:
            if k in medal_t and s.get("t") and s["t"] >= medal_t[k]:
                found.append((s["t"] - medal_t[k]) / 3600)
    return found


def fmt_odds(odds: Dict[str, float], names: Dict[str, str]) -> str:
    """'Name A ~80 % / Name B ~20 %' (wahrscheinlichster zuerst)."""
    parts = [(p, names.get(k, k)) for k, p in odds.items() if p >= MIN_SHARE]
    merged: Dict[str, float] = {}
    for p, n in parts:
        merged[n] = merged.get(n, 0) + p
    return " / ".join(f"{n} ~{round(min(p, 1) * 100)} %" for n, p in sorted(merged.items(), key=lambda x: -x[1]))
