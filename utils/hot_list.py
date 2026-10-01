"""Hot-Banner-Rangliste: ziehbare Banner nach Ø Rückgabe in % vom Preis (reine Hilfsfunktionen)."""

import json
from typing import Dict, List, Optional

from utils.banner_info import RANK_NAMES, RANK_ORDER
from utils.card_pool import fmt_coins, fmt_pct

HOT_TOP = 10
ALERT_PCT = 100     # stille Meldung, wenn ein Banner neu über diesen Wert steigt
ALERT_RESET_PCT = 97  # erst darunter wieder scharf (kein Hin und Her um 100 %)


def min_rank(conditions: Optional[str]) -> Optional[str]:
    """Niedrigster erlaubter Mitgliedsrang, None = alle Ränge."""
    if not conditions:
        return None
    ranks = [r for r in json.loads(conditions).get("ranks", []) if r in RANK_ORDER]
    if not ranks or "all" in json.loads(conditions).get("ranks", []):
        return None
    lowest = min(ranks, key=RANK_ORDER.index)
    return None if RANK_ORDER.index(lowest) == 0 else lowest


def needs_password(conditions: Optional[str]) -> bool:
    return bool(conditions) and bool(json.loads(conditions).get("password"))


def rank_entries(entries: List[Dict], top: int = HOT_TOP) -> List[Dict]:
    """Beste Ø Rückgabe zuerst; bei Gleichstand der Banner mit weniger Rest-Packs."""
    return sorted(entries, key=lambda e: (-e["pct"], e["remaining"]))[:top]


def hot_line(rank: int, e: Dict, link: str) -> str:
    """'1. 🎯 [ID 24149](link) · 1.000 Coins · Ø **226,5 %** zurück · 1/3 Hits offen · 61 Packs übrig'"""
    icon = "⚡" if e.get("endspurt") else "🎯"
    parts = [f"{rank}. {icon} [ID {e['pack_id']}]({link})", f"{fmt_coins(e['price'])} Coins",
             f"Ø **{fmt_pct(e['pct'])} %** zurück"]
    if e.get("tracked_hits"):
        parts.append(f"{e['hits_open']}/{e['hits_total']} Hits offen")
    else:
        parts.append(f"Top 3: {e['hits_open']} offen")
    if e.get("unsure"):
        parts[-1] += " ❓"
    parts.append(f"{fmt_coins(e['remaining'])} Packs übrig")
    if e.get("cost_to_hit"):
        parts.append(f"Ø {fmt_coins(e['cost_to_hit'])} bis Hit")
    if e.get("rank"):
        parts.append(f"ab {RANK_NAMES.get(e['rank'], e['rank'])}")
    return " · ".join(parts)


def new_alerts(entries: List[Dict], alerted: set) -> tuple:
    """(Banner, die neu über ALERT_PCT liegen, neue Menge gemeldeter Banner)."""
    by_id = {e["pack_id"]: e for e in entries}
    fresh = [e for e in entries if e["pct"] > ALERT_PCT and e["pack_id"] not in alerted]
    keep = {pid for pid in alerted if pid in by_id and by_id[pid]["pct"] >= ALERT_RESET_PCT}
    return fresh, keep | {e["pack_id"] for e in fresh}
