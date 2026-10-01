"""Kartenpool eines Banners: Zusammenfassung der card_list-API und geschätzte Rückgabe pro Zug.

card_list zeigt immer den Startbestand (duplication = Exemplare je Karte, Summe = Gesamt-Packs).
Welche Karten gezogen wurden, ist nur für T1-T3 (= Platz 1-3 nach Coin-Wert) über die
Medaillen bekannt. Alle übrigen Züge werden als Durchschnittszüge aus dem Rest-Pool gerechnet.
"""

from typing import Dict, List, Optional

HIT_ACTION_TYPE = 2
TOP_COUNT = 5
TIERS = ("T1", "T2", "T3")


def summarize_cards(cards: List[Dict]) -> Optional[Dict]:
    """Verdichtet die rohen card_list-Einträge auf das, was der Bot speichert."""
    parsed = []
    for c in cards:
        try:
            parsed.append({
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
    return {
        "total_count": total_count,
        "total_value": sum(c["value"] * c["copies"] for c in parsed),
        "hits_total": sum(c["copies"] for c in parsed if c["hit"]),
        "top": [{k: c[k] for k in ("name", "value", "image", "hit")} for c in parsed[:TOP_COUNT]],
    }


def estimate(pool: Dict, remaining: Optional[int], total_packs: Optional[int],
             claimed: Dict[str, bool], price: Optional[int]) -> Optional[Dict]:
    """Geschätzte Ø-Rückgabe pro Zug und Hit-Chance für die verbleibenden Packs.

    claimed: Medaillen-Status {'T1': bool, 'T2': bool, 'T3': bool}.
    """
    n_pool = pool.get("total_count") or 0
    if n_pool <= 0:
        return None
    total = total_packs or n_pool
    remaining = n_pool if remaining is None else max(0, min(remaining, n_pool))
    if remaining <= 0:
        return None

    tier_cards = pool["top"][:len(TIERS)]
    claimed_cards = [c for c, tier in zip(tier_cards, TIERS) if claimed.get(tier)]
    open_tier_cards = [c for c, tier in zip(tier_cards, TIERS) if not claimed.get(tier)]

    # Unbekannte Züge stammen aus dem Pool ohne T1-T3: gezogene T1-T3 sind per Medaille bekannt,
    # nicht vergebene gelten als noch drin.
    rest_count = n_pool - len(tier_cards)
    rest_value = pool["total_value"] - sum(c["value"] for c in tier_cards)
    rest_hits = pool.get("hits_total", 0) - sum(1 for c in tier_cards if c["hit"])
    pulled = max(0, min(total, n_pool) - remaining)
    unknown_pulls = max(0, min(pulled - len(claimed_cards), rest_count))
    rest_left_share = (rest_count - unknown_pulls) / rest_count if rest_count > 0 else 0.0

    value_left = sum(c["value"] for c in open_tier_cards) + rest_value * rest_left_share
    hits_left = sum(1 for c in open_tier_cards if c["hit"]) + rest_hits * rest_left_share

    ev = value_left / remaining
    return {
        "ev": ev,
        "ev_pct": ev / price * 100 if price else None,
        "estimated": pulled > 0,
        "hits_total": pool.get("hits_total", 0),
        "hits_left": hits_left,
        "hit_chance_pct": min(100.0, hits_left / remaining * 100),
        "open_tiers": [tier for tier in TIERS if not claimed.get(tier)][:len(tier_cards)],
    }


def decided_value(item: Dict) -> Optional[int]:
    """Coin-Wert aller gezogenen Karten, die umgewandelt oder verschickt wurden (aus pack/list)."""
    try:
        return int(float(item.get("total_kangen") or 0)) + int(float(item.get("total_sendprice") or 0))
    except (TypeError, ValueError):
        return None


def detect_tier_pulls(pool: Dict, jump: int, detected: List[str]) -> List[str]:
    """Erkennt T1-T3 an einem Anstieg des entschiedenen Werts zwischen zwei Scrapes.

    Die Seite zählt den Coin-Wert einer Karte erst, wenn der Gewinner sie umwandelt oder
    verschicken lässt. Ein Anstieg um mindestens den Wert einer offenen T1-T3 enthält mit hoher
    Wahrscheinlichkeit diese Karte; es wird jeweils die größte passende Karte genommen.
    Simuliert mit der Kartenliste von 24114: T1 praktisch immer richtig, T2/T3 können bei großen
    Sammel-Umwandlungen verwechselt werden.
    """
    tiers = list(zip(TIERS, (c["value"] for c in pool.get("top", [])[:len(TIERS)])))
    found: List[str] = []
    remaining = jump
    while True:
        fitting = [(t, v) for t, v in tiers if t not in detected and t not in found and 0 < v <= remaining]
        if not fitting:
            return found
        tier, value = max(fitting, key=lambda x: x[1])
        found.append(tier)
        remaining -= value


def fmt_coins(value: float) -> str:
    return f"{round(value):,}".replace(",", ".")


def fmt_pct(value: float, digits: int = 1) -> str:
    return f"{value:.{digits}f}".replace(".", ",")
