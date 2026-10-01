"""Kartenpool eines Banners: Zusammenfassung der card_list-API, Hit-Erkennung und Ø-Rückgabe.

card_list zeigt immer den Startbestand (duplication = Exemplare je Karte, Summe = Gesamt-Packs).
Hits sind die Karten mit action_type 2 ("Versand nur"): sie können nicht in Coins umgewandelt,
nur verschickt werden. pack/list zählt verschickte Karten (total_sendcount) und ihren Coin-Wert
(total_sendprice); daraus lässt sich exakt nachrechnen, welche Hits verschickt wurden.
Banner ohne Versand-Hits verfolgen ersatzweise die drei wertvollsten Karten (T1-T3).
"""

from itertools import combinations
from typing import Dict, List, Optional, Set

POOL_VERSION = 2
HIT_ACTION_TYPE = 2       # "Versand nur" (Flugzeug-Symbol auf der Seite)
MAX_LISTED = 10           # Discord erlaubt 10 Embeds pro Nachricht
TIERS = ("T1", "T2", "T3")
MAX_SHIPMENT_CARDS = 10   # größere Versand-Sprünge werden nicht exakt zerlegt
MAX_SHIPMENT_VALUE = 5_000_000


def summarize_cards(cards: List[Dict]) -> Optional[Dict]:
    """Verdichtet die rohen card_list-Einträge auf das, was der Bot speichert."""
    parsed = []
    for c in cards:
        try:
            parsed.append({
                "id": str(c.get("id")),
                "name": str(c.get("name") or "?").strip(),
                "value": int(c.get("buy_point") or 0),
                "copies": int(c.get("duplication") or 0),
                "hit": int(c.get("action_type") or 0) == HIT_ACTION_TYPE,
                "image": c.get("image_url") or None,
            })
        except (TypeError, ValueError):
            continue
    total_count = sum(c["copies"] for c in parsed)
    if total_count <= 0:
        return None
    parsed.sort(key=lambda c: -c["value"])
    normal_values: Dict[str, int] = {}
    for c in parsed:
        if not c["hit"] and c["copies"] > 0:
            normal_values[str(c["value"])] = normal_values.get(str(c["value"]), 0) + c["copies"]
    return {
        "version": POOL_VERSION,
        "total_count": total_count,
        "total_value": sum(c["value"] * c["copies"] for c in parsed),
        "hits_total": sum(c["copies"] for c in parsed if c["hit"]),
        "hits": [{k: c[k] for k in ("id", "name", "value", "image", "copies")} for c in parsed if c["hit"]],
        "top": [{k: c[k] for k in ("id", "name", "value", "image", "hit")} for c in parsed[:5]],
        "normal_values": normal_values,
    }


def tracked_units(pool: Dict) -> List[Dict]:
    """Karten, die einzeln verfolgt werden: alle Versand-Hits, sonst die drei wertvollsten Karten.

    Jedes Exemplar ist eine Einheit mit eigenem Schlüssel (Karten-ID, bei Mehrfach-Exemplaren
    mit Nummer), absteigend nach Wert. Einheit 1-3 entspricht T1-T3.
    """
    if pool.get("hits"):
        cards, shipping_only = pool["hits"], True
    else:
        cards, shipping_only = pool.get("top", [])[:len(TIERS)], False
    units = []
    for c in cards:
        copies = int(c.get("copies") or 1)
        for n in range(copies):
            key = c.get("id") or c["name"]
            units.append({**c, "key": f"{key}#{n + 1}" if copies > 1 else str(key),
                          "shipping_only": shipping_only})
    return units


def tier_keys(pool: Dict) -> Dict[str, str]:
    """Zuordnung T1-T3 -> Schlüssel der drei wertvollsten verfolgten Einheiten."""
    return {tier: unit["key"] for tier, unit in zip(TIERS, tracked_units(pool))}


def estimate(pool: Dict, remaining: Optional[int], total_packs: Optional[int],
             pulled_keys: Set[str], price: Optional[int]) -> Optional[Dict]:
    """Geschätzte Ø-Rückgabe pro Zug und Hit-Chance für die verbleibenden Packs.

    Verfolgte Einheiten gelten als noch drin, solange sie nicht als gezogen bekannt sind
    (Versand erkannt oder Medaille). Alle übrigen Züge zählen als Durchschnittszüge aus dem Rest.
    """
    n_pool = pool.get("total_count") or 0
    if n_pool <= 0:
        return None
    total = total_packs or n_pool
    remaining = n_pool if remaining is None else max(0, min(remaining, n_pool))
    if remaining <= 0:
        return None

    units = tracked_units(pool)
    open_units = [u for u in units if u["key"] not in pulled_keys]
    known_pulled = len(units) - len(open_units)

    rest_count = n_pool - len(units)
    rest_value = pool["total_value"] - sum(u["value"] for u in units)
    rest_hits = pool.get("hits_total", 0) - sum(1 for u in units if u.get("hit", u["shipping_only"]))
    pulled = max(0, min(total, n_pool) - remaining)
    unknown_pulls = max(0, min(pulled - known_pulled, rest_count))
    rest_left_share = (rest_count - unknown_pulls) / rest_count if rest_count > 0 else 0.0

    value_left = sum(u["value"] for u in open_units) + rest_value * rest_left_share
    hits_left = (sum(1 for u in open_units if u.get("hit", u["shipping_only"]))
                 + max(0, rest_hits) * rest_left_share)
    ev = value_left / remaining
    keys = tier_keys(pool)
    return {
        "ev": ev,
        "ev_pct": ev / price * 100 if price else None,
        "estimated": pulled > 0,
        "hits_total": pool.get("hits_total", 0),
        "hits_open": sum(1 for u in open_units if u.get("hit", u["shipping_only"])),
        "hits_left": hits_left,
        "hit_chance_pct": min(100.0, hits_left / remaining * 100),
        "open_tiers": [t for t in TIERS if t in keys and keys[t] not in pulled_keys],
    }


def shipment_values(item: Dict) -> Optional[tuple]:
    """(Anzahl, Coin-Wert) aller verschickten Karten eines Banners aus pack/list."""
    try:
        return int(float(item.get("total_sendcount") or 0)), int(float(item.get("total_sendprice") or 0))
    except (TypeError, ValueError):
        return None


def _normal_sums(pool: Dict, max_cards: int, max_value: int) -> List[int]:
    """Bitmasken: sums[j] hat Bit s gesetzt, wenn j Nicht-Hit-Karten zusammen genau s Coins ergeben."""
    mask = (1 << (max_value + 1)) - 1
    sums = [1] + [0] * max_cards
    for value_str, copies in pool.get("normal_values", {}).items():
        value = int(value_str)
        if value <= 0 or value > max_value:
            continue
        for _ in range(min(copies, max_cards)):
            for j in range(max_cards, 0, -1):
                sums[j] |= (sums[j - 1] << value) & mask
    return sums


def match_shipped_hits(pool: Dict, count: int, value: int, pulled_keys: Set[str]) -> List[str]:
    """Welche Versand-Hits sind sicher in einer Sendung aus `count` Karten im Wert `value` enthalten?

    Probiert alle Kombinationen offener Hits; der Rest muss sich aus genau so vielen normalen
    Karten exakt ergeben. Nur Hits, die in jeder möglichen Kombination vorkommen, gelten als
    sicher verschickt. Nicht zerlegbare oder zu große Sendungen liefern nichts.
    """
    if count <= 0 or value <= 0 or count > MAX_SHIPMENT_CARDS or value > MAX_SHIPMENT_VALUE:
        return []
    open_hits = [u for u in tracked_units(pool) if u["shipping_only"] and u["key"] not in pulled_keys]
    if not open_hits:
        return []
    sums = _normal_sums(pool, count, value)
    # Wertgleiche Hits sind austauschbar: es zählt nur, welche Werte wie oft verschickt wurden
    possible = set()
    for size in range(0, min(count, len(open_hits)) + 1):
        for combo in combinations(open_hits, size):
            rest = value - sum(u["value"] for u in combo)
            if rest >= 0 and (sums[count - size] >> rest) & 1:
                possible.add(tuple(sorted(u["value"] for u in combo)))
    if not possible:
        return []
    certain_values = None
    for values in possible:
        counted = {v: values.count(v) for v in set(values)}
        certain_values = counted if certain_values is None else {
            v: min(n, counted.get(v, 0)) for v, n in certain_values.items() if counted.get(v, 0)}
    shipped = []
    for unit in open_hits:
        if certain_values.get(unit["value"], 0) > 0:
            shipped.append(unit["key"])
            certain_values[unit["value"]] -= 1
    return shipped


def decided_value(item: Dict) -> Optional[int]:
    """Coin-Wert aller gezogenen Karten, die umgewandelt oder verschickt wurden (aus pack/list)."""
    try:
        return int(float(item.get("total_kangen") or 0)) + int(float(item.get("total_sendprice") or 0))
    except (TypeError, ValueError):
        return None


def detect_jump_pulls(pool: Dict, jump: int, pulled_keys: Set[str]) -> List[str]:
    """Rückfall für Banner ohne Versand-Hits: T1-T3 an einem Sprung des entschiedenen Werts.

    Ein Anstieg um mindestens den Wert einer offenen T1-T3 enthält mit hoher Wahrscheinlichkeit
    diese Karte; es wird jeweils die größte passende genommen. Simuliert mit 24114: T1 praktisch
    immer richtig, T2/T3 können bei großen Sammel-Umwandlungen verwechselt werden.
    """
    units = [u for u in tracked_units(pool)[:len(TIERS)] if not u["shipping_only"]]
    found: List[str] = []
    remaining = jump
    while True:
        fitting = [u for u in units if u["key"] not in pulled_keys and u["key"] not in found
                   and 0 < u["value"] <= remaining]
        if not fitting:
            return found
        unit = max(fitting, key=lambda u: u["value"])
        found.append(unit["key"])
        remaining -= unit["value"]


def fmt_coins(value: float) -> str:
    return f"{round(value):,}".replace(",", ".")


def fmt_pct(value: float, digits: int = 1) -> str:
    return f"{value:.{digits}f}".replace(".", ",")
