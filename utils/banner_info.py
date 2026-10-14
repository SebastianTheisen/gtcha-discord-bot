"""Reine Hilfsfunktionen rund um Banner: Zahlen lesen, Hit-Chance, Kaufbedingungen."""

import json
import re
from datetime import datetime, timedelta, timezone
from typing import Dict, Optional

from utils.card_pool import card_value, fmt_coins


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
        "cards_hidden": str(item.get("card_hide_flag") or "0") not in ("0", "", "None"),   # Kartenliste versteckt
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


def shipping_stats(item: dict) -> dict:
    """Verschickte Karten eines Banners aus pack/list."""
    return {
        "cards": to_int(item.get("total_sendcount")),
        "coins": to_int(item.get("total_sendprice")),
        "players": to_int(item.get("total_sendpeople")),
    }


def format_shipping(raw: Optional[str]) -> Optional[str]:
    """'5 Karten · 772.255 Coins · 1 Spieler' (Kartenwert; None, solange keine Daten da sind)."""
    if not raw:
        return None
    st = json.loads(raw)
    if not st.get("cards"):
        return "Noch nichts verschickt"
    cards = "1 Karte" if st["cards"] == 1 else f"{fmt_coins(st['cards'])} Karten"
    players = "1 Spieler" if st.get("players") == 1 else f"{fmt_coins(st.get('players') or 0)} Spieler"
    return f"{cards} · {fmt_coins(card_value(st['coins']))} Coins · {players}"


JST = timezone(timedelta(hours=9))
CATEGORY_BY_CARD_TYPE = {"2": "Pokémon", "5": "One piece", "9": "Dragon Ball", "999996": "MIX"}
# Namen weiterer Kategorien, wie die Seite sie in ihren Reitern zeigt (card_type -> Name, z. B. "Yu-Gi-Oh!");
# setzt der Scrape aus der Kategorienliste der Seite (set_card_type_names)
CARD_TYPE_NAMES: Dict[str, str] = {}


# Die Kategorienliste der Seite ist japanisch; Namen wie in den deutschen Reitern der Seite
CATEGORY_NAME_DE = {
    "遊戯王": "Yu-Gi-Oh!", "ウルトラマン": "Ultraman", "ヴァイスシュヴァルツ": "Weiß Schwarz", "フィギュア": "Figuren",
    "ゲーム": "Spiele", "ホビー": "Hobby", "予備": "Reserve", "海外向け": "International", "BtoB": "BtoB",
    "ポケモン": "Pokémon", "ワンピース": "One piece", "ドラゴンボール": "Dragon Ball",
    "デュエル・マスターズ": "Duel Masters", "デジモン": "Digimon", "ヴァンガード": "Vanguard", "マジック": "Magic",
    "ロルカナ": "Lorcana", "ユニオンアリーナ": "Union Arena", "バトルスピリッツ": "Battle Spirits",
}


def category_name_de(name: str) -> str:
    name = str(name or "").strip()
    return CATEGORY_NAME_DE.get(name, name)


def set_card_type_names(names: Dict) -> None:
    CARD_TYPE_NAMES.clear()
    CARD_TYPE_NAMES.update({str(k): category_name_de(v) for k, v in (names or {}).items() if str(v or "").strip()})


def category_for(item: dict) -> Optional[str]:
    """Kategorie eines Banners aus pack/list. Bekannte haben feste Namen (wie die Discord-Foren); neue Kategorien
    der Seite (z. B. Yu-Gi-Oh!) bekommen den Namen aus ihren Reitern - so erscheinen sie automatisch in der App."""
    if str(item.get("is_bonus")) == "1":
        return "Bonus"
    card_type = str(item.get("card_type") or "").strip()
    if not card_type or card_type == "0":
        return None
    return CATEGORY_BY_CARD_TYPE.get(card_type) or CARD_TYPE_NAMES.get(card_type) or f"Kategorie {card_type}"


def versioned_image(url: Optional[str], starts_at: Optional[int], cards: Optional[str] = None) -> Optional[str]:
    """Bannerbild mit Kennung des Banners: GTCHA vergibt Pack-IDs neu bzw. bearbeitet Banner, das Bild liegt dann
    unter derselben Adresse (/pack/24260/1.webp) - ohne Kennung zeigten Zwischenspeicher (VPS, Browser) noch das
    alte Bild. Kennung = Verkaufsstart, in den Apps zusätzlich die Karten (card_key). Die Seite ignoriert den Zusatz."""
    if not url or not (starts_at or cards):
        return url
    key = "-".join(str(x) for x in (int(starts_at) if starts_at else None, cards) if x)
    return f"{url}{'&' if '?' in url else '?'}s={key}"


def card_key(pool_json) -> Optional[str]:
    """Kurzer Fingerabdruck der Karten eines Banners (IDs, nicht Werte): ändert sich, wenn GTCHA die Karten tauscht."""
    import hashlib
    try:
        pool = json.loads(pool_json) if isinstance(pool_json, str) else (pool_json or {})
    except ValueError:
        return None
    ids = sorted(str(c.get("id") or c.get("name")) for part in ("hits", "cards", "top") for c in pool.get(part) or [])
    return hashlib.sha1(",".join(ids).encode()).hexdigest()[:8] if ids else None


def jst_timestamp(text: Optional[str]) -> Optional[int]:
    """'2026-10-01 03:00:00' oder '2026/11/01 00:00' (Zeit der Seite, JST) -> Unix-Zeit."""
    if not text:
        return None
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y/%m/%d %H:%M:%S", "%Y-%m-%d %H:%M", "%Y/%m/%d %H:%M"):
        try:
            return int(datetime.strptime(str(text).strip(), fmt).replace(tzinfo=JST).timestamp())
        except ValueError:
            continue
    return None


def sale_end_timestamp(text: Optional[str]) -> Optional[int]:
    """Verkaufsende aus dem Text der Seite (Zeit in JST) -> Unix-Zeit.

    Die Seite liefert verschiedene Formate, z.B. 'Erhältlich bis 31/10/2026 23:59 JST' oder
    '2026/10/31 23:59 まで販売'. Ohne Uhrzeit gilt 23:59.
    """
    if not text:
        return None
    s = str(text)
    m = re.search(r"(\d{4})[/.-](\d{1,2})[/.-](\d{1,2})(?:\D+(\d{1,2}):(\d{2}))?", s)
    if m:
        y, mo, d, h, mi = m.groups()
    else:
        m = re.search(r"(\d{1,2})[/.-](\d{1,2})[/.-](\d{4})(?:\D+(\d{1,2}):(\d{2}))?", s)
        if not m:
            return None
        d, mo, y, h, mi = m.groups()
    try:
        when = datetime(int(y), int(mo), int(d), int(h or 23), int(mi or 59), tzinfo=JST)
    except ValueError:
        return None
    return int(when.timestamp())


def is_upcoming(item: dict) -> bool:
    return bool(item.get("is_before"))


def berlin_time(ts: float) -> datetime:
    """Unix-Zeit -> deutsche Zeit (MEZ/MESZ, Umstellung am letzten Sonntag im März/Oktober um 01:00 UTC)."""
    utc = datetime.fromtimestamp(ts, timezone.utc)

    def last_sunday(month: int) -> datetime:
        day = datetime(utc.year, month, 31, 1, tzinfo=timezone.utc)
        return day - timedelta(days=(day.weekday() + 1) % 7)

    summer = last_sunday(3) <= utc < last_sunday(10)
    return utc.astimezone(timezone(timedelta(hours=2 if summer else 1)))


def berlin_to_ts(local: datetime) -> int:
    """Deutsche Uhrzeit (ohne Zeitzone) -> Unix-Zeit (Gegenstück zu berlin_time)."""
    for hours in (2, 1):
        ts = int(local.replace(tzinfo=timezone(timedelta(hours=hours))).timestamp())
        if berlin_time(ts).replace(tzinfo=None) == local.replace(second=0, microsecond=0):
            return ts
    return int(local.replace(tzinfo=timezone(timedelta(hours=1))).timestamp())


STATUS_ICONS = {"running": "🎯", "endspurt": "⚡ Endspurt", "hits_out": "🔴 Hits raus"}


def thread_title(pack_id: int, price, total, entries, status: str, starts_at: Optional[int] = None) -> str:
    """Kompakter Thread-Titel: Status · Preis · Packs · Limit · ID (max. 100 Zeichen)."""
    if status == "upcoming" and starts_at:
        head = f"🕒 ab {berlin_time(starts_at):%d.%m. %H:%M}"
    else:
        head = STATUS_ICONS.get(status, "🎯")
    parts = [head, f"{fmt_coins(to_int(price))} Coins", f"{fmt_coins(to_int(total))} Packs"]
    if to_int(entries):
        parts.append(f"{to_int(entries)}/Tag")
    parts.append(f"ID {pack_id}")
    return " · ".join(parts)[:100]


def parse_thread_title(name: str) -> dict:
    """ID, Preis, Limit und Packs aus einem Thread-Titel (neues und altes Format)."""
    def number(pattern):
        m = re.search(pattern, name)
        return int(m.group(1).replace(".", "")) if m else None

    return {
        "pack_id": number(r"\bID:?\s*(\d+)"),
        "price": number(r"([\d.]+) Coins") or number(r"Kosten:\s*(\d+)"),
        "entries": number(r"(\d+)/Tag") or number(r"Anzahl(?: Pulls)?:\s*(\d+)"),
        "total": number(r"([\d.]+) Packs") or number(r"Gesamt:\s*(\d+)"),
    }
