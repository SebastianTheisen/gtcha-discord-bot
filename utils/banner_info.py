"""Reine Hilfsfunktionen rund um Banner: Zahlen lesen, Hit-Chance, Kaufbedingungen."""

import json
from typing import Optional

from utils.card_pool import fmt_coins


def to_int(value) -> int:
    """Zahl aus der API (int, float oder String) lesen; ungültig/leer = 0."""
    try:
        return int(float(value or 0))
    except (TypeError, ValueError):
        return 0


def chance_at_least_one(packs: int, hits: int, pulls: int) -> float:
    """Wahrscheinlichkeit in %, bei `pulls` Zügen aus `packs` Packs mindestens einen der `hits` zu ziehen."""
    if hits <= 0 or packs <= 0:
        return 0.0
    if pulls > packs - hits:
        return 100.0
    none = 1.0
    for i in range(pulls):
        none *= (packs - hits - i) / (packs - i)
    return (1 - none) * 100


RANK_ORDER = ("white", "bronze", "silver", "gold", "rainbow", "black")
RANK_NAMES = {"white": "Weiß", "bronze": "Bronze", "silver": "Silber", "gold": "Gold",
              "rainbow": "Rainbow", "black": "Black"}


def banner_conditions(item: dict) -> dict:
    """Kaufbedingungen eines Banners aus pack/list."""
    badges = item.get("badges") or []
    return {
        "ranks": badges if isinstance(badges, list) else [],
        "min_charge": to_int(item.get("min_charge_amount")),
        "password": bool(item.get("password_flag")),
    }


def format_conditions(raw: Optional[str]) -> Optional[str]:
    """Kaufbedingungen als Text für den Startbeitrag (None, solange keine Daten da sind)."""
    if not raw:
        return None
    cond = json.loads(raw)
    lines = []
    ranks = [r for r in cond.get("ranks", []) if r in RANK_ORDER]
    if "all" in cond.get("ranks", []) or not ranks or min(RANK_ORDER.index(r) for r in ranks) == 0:
        lines.append("Alle Mitgliedsränge")
    else:
        lowest = min(ranks, key=RANK_ORDER.index)
        lines.append(f"Ab Mitgliedsrang **{RANK_NAMES[lowest]}**")
    if cond.get("min_charge"):
        lines.append(f"Mindest-Aufladung: {fmt_coins(cond['min_charge'])} Coins im Monat")
    if cond.get("password"):
        lines.append("🔒 Nur mit Passwort")
    return "\n".join(lines)
