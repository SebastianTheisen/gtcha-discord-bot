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
EMBEDS_PER_MESSAGE = 10   # Discord erlaubt 10 Embeds pro Nachricht
MAX_LISTED = 50           # Obergrenze für die Hit-Liste (5 Nachrichten)
TIERS = ("T1", "T2", "T3")
MAX_MEDALS = 50           # Medaille Tn = Platz n der Hit-Liste
MAX_SHIPMENT_CARDS = 10   # größere Versand-Sprünge werden nicht exakt zerlegt
MAX_SHIPMENT_VALUE = 5_000_000
VALUE_TOLERANCE = 0.01     # nur Rundung (Werte werden ohne Steuer gezählt, siehe TAX_FACTOR)
# total_sendprice zählt Kartenwerte ohne 10 % japanische Steuer: gezählt = Kartenwert / 1,1
# (bestätigt: 165.000 -> 150.000, 18.260 -> 16.600, 660 -> 600)
TAX_FACTOR = 1.1


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
        "min": _min_card(parsed),
        # komplette Kartenliste (für die Web-App), wertvollste zuerst
        "cards": [{k: c[k] for k in ("id", "name", "value", "copies", "image", "hit")} for c in parsed if c["copies"] > 0],
    }


def _min_card(parsed: List[Dict]) -> Optional[Dict]:
    cards = [c for c in parsed if c["copies"] > 0]
    if not cards:
        return None
    low = min(c["value"] for c in cards)
    same = [c for c in cards if c["value"] == low]
    return {"value": low, "copies": sum(c["copies"] for c in same),
            "name": same[0]["name"] if len(same) == 1 else None}


def pool_minimum(pool: Dict) -> Optional[Dict]:
    """Niedrigster Kartenwert im Pool = was ein Zug mindestens zurückbringt.

    Ältere gespeicherte Pools haben noch kein "min"; dann aus den Kartenwerten ohne Namen.
    """
    if pool.get("min"):
        return pool["min"]
    values = {int(v): n for v, n in (pool.get("normal_values") or {}).items()}
    for hit in pool.get("hits") or []:
        values[hit["value"]] = values.get(hit["value"], 0) + int(hit.get("copies") or 1)
    if not values:
        return None
    low = min(values)
    return {"value": low, "copies": values[low], "name": None}


def _units(pool: Dict) -> List[Dict]:
    """Alle Exemplare (Versand-Hits und normale Karten) ohne Platz, Hits zuerst."""
    units = []
    for c in pool.get("hits") or []:
        copies = int(c.get("copies") or 1)
        for n in range(copies):
            key = c.get("id") or c["name"]
            units.append({**c, "key": f"{key}#{n + 1}" if copies > 1 else str(key), "shipping_only": True})
    if pool.get("hits"):
        normal = [c for c in pool.get("cards") or [] if not c.get("hit")]
    else:
        normal = pool.get("cards") or pool.get("top", [])
    # Normale Karten sind nach Wert sortiert; für T1-T50 reichen die 50 teuersten Exemplare
    # (große Pools haben über 100.000 Exemplare - alle zu erzeugen machte den Server langsam)
    normal_units = 0
    for c in normal:
        key = str(c.get("id") or c["name"])
        card = {k: v for k, v in c.items() if k != "copies"}
        for n in range(int(c.get("copies") or 1)):
            if normal_units >= MAX_MEDALS:
                return units
            units.append({**card, "key": key if n == 0 else f"{key}#{n + 1}", "shipping_only": False})
            normal_units += 1
    return units


def medal_units(pool: Dict) -> List[Dict]:
    """Alle Einheiten, die per Medaille (T1-T50) gemeldet werden können; "tier" = Medaille.

    Streng nach Wert sortiert (wie die Kartenliste der Seite), Versand-Hits und normale Karten
    gemischt; bei gleichem Wert Versand-Hits zuerst. Jedes Exemplar ist eine Einheit; bei normalen
    Karten behält das erste Exemplar die Karten-ID als Schlüssel, damit gespeicherte Züge passen.
    """
    units = sorted(_units(pool), key=lambda u: (-u["value"], not u["shipping_only"]))[:MAX_MEDALS]
    return [{**u, "tier": f"T{i}"} for i, u in enumerate(units, 1)]


def medal_units_hits_first(pool: Dict) -> List[Dict]:
    """Frühere Reihenfolge (Versand-Hits zuerst, dann normale Karten) - nur für die Umstellung."""
    return [{**u, "tier": f"T{i}"} for i, u in enumerate(_units(pool)[:MAX_MEDALS], 1)]


def tracked_units(pool: Dict) -> List[Dict]:
    """Karten, die einzeln verfolgt werden: alle Versand-Hits, sonst die drei wertvollsten Karten
    (mit Exemplaren: gibt es die teuerste Karte 3x, sind das T1, T2 und T3). Einheit 1-3 = T1-T3."""
    if pool.get("hits"):
        # alle Versand-Hits (unabhängig von der T50-Grenze), mit ihrem Medaillen-Platz
        tiers = {u["key"]: u["tier"] for u in medal_units(pool)}
        return [{**u, "tier": tiers.get(u["key"])} for u in _units(pool) if u["shipping_only"]]
    return medal_units(pool)[:len(TIERS)]


def claimable_units(pool: Dict, price: Optional[int]) -> List[Dict]:
    """Was man als gezogen melden kann: alle Einheiten ab Packpreis (Versand-Hits und normale
    Karten), Platz = "tier". Ohne Versand-Hits und ohne Packpreis (Gratis-Banner) nur T1-T3."""
    units = medal_units(pool)
    if not pool.get("hits") and not price:
        return units[:len(TIERS)]
    return [u for u in units if not price or u["value"] >= price]


def tier_keys(pool: Dict) -> Dict[str, str]:
    """Zuordnung T1-T50 -> Schlüssel (Platz in der Hit-Liste, nach Wert)."""
    return {unit["tier"]: unit["key"] for unit in medal_units(pool)}


def is_relevant_hit(unit: Dict, price: Optional[int]) -> bool:
    """Zählt als Hit: Versand-/Top-Karte, die mindestens so viel wert ist wie ein Pack.

    Wie die Gewinner-Vorschau der Seite, die Karten unter dem Packpreis nicht zeigt.
    """
    is_hit = unit.get("hit", unit.get("shipping_only", True))
    return bool(is_hit) and (not price or unit["value"] >= price)


def relevant_units(pool: Dict, price: Optional[int]) -> List[Dict]:
    return [u for u in tracked_units(pool) if is_relevant_hit(u, price)]


def resolve_pulled(detected: List[str], unsure: List[Dict], claimed: Set[str]) -> tuple:
    """Gezogene Karten aus sicheren Erkennungen, Medaillen und offenen ❓-Gruppen.

    Eine Gruppe "n von diesen Karten gezogen" bleibt offen (❓), solange eine ihrer Karten weder per
    Medaille noch sicher erkannt ist: eine Medaille sagt nicht, welcher Versand zu ihr gehört (24152:
    T3 gemeldet, der Versand T2/T3 war aber T2). Für die Rechnung zählen Stellvertreter aus der Gruppe.
    Gibt (alle gezogenen inkl. Stellvertreter, sicher erkannte, offene Gruppen) zurück.
    """
    groups = [g for g in unsure if g.get("pulled", 0) > 0]
    # Ältere Stände haben Stellvertreter (erste Gruppen-Karten) direkt gespeichert: herausrechnen
    legacy = {k for g in groups for k in g["keys"][:g["pulled"]]}
    sure = set(detected) - legacy
    base = sure | set(claimed)
    stand_ins: Set[str] = set()
    open_groups = []
    for g in sorted(groups, key=lambda g: len(g["keys"])):
        free = [k for k in g["keys"] if k not in base and k not in stand_ins]
        if not free:
            continue   # alle Kandidaten stehen schon als gezogen fest - nichts mehr offen
        stand_ins |= set(free[:g["pulled"]])
        open_groups.append(g)
    return base | stand_ins, sure, open_groups


def out_of_banner_value(pool: Dict, converted: Optional[int], shipped_counted: Optional[int],
                        held_keys: Set[str] = frozenset(), shipped_keys: Set[str] = frozenset()) -> Optional[int]:
    """Kartenwert, der schon aus dem Banner raus ist (None = keine Zahlen der Seite).

    umgewandelt (total_kangen) zählt die Seite mit vollem Kartenwert (bestätigt an 24060: 55.575 Züge,
    Verhältnis 1,001), verschickt (total_sendprice) ohne 10 % Steuer -> ×1,1.
    Per Medaille gemeldete Hits (held_keys) zählen nur zusätzlich, wenn sie nicht schon im
    verschickten Wert stecken können: Hit für Hit (teuerster zuerst) wird geprüft, ob er in den
    Versandwert passt, der noch keinem Hit sicher zugeordnet ist (24149: alle 3 Medaillen-Hits waren
    verschickt und wurden sonst doppelt gezählt). Passt er nicht hinein, liegt er noch beim Spieler.
    """
    if converted is None:
        return None
    # Nur Versand-Hits können beim Spieler liegen bleiben; normale Karten werden umgewandelt und
    # stecken dann schon in "umgewandelt" - sie zählen nicht extra
    units = {u["key"]: u["value"] for u in tracked_units(pool) if u["shipping_only"]}
    shipped = card_value(shipped_counted)
    room = max(0, shipped - sum(units.get(k, 0) for k in shipped_keys))
    extra = 0
    for value in sorted((units.get(k, 0) for k in set(held_keys) - set(shipped_keys)), reverse=True):
        if value <= room:
            room -= value          # kann im Versand stecken
        else:
            extra += value         # noch nicht verschickt
    return converted + shipped + extra


def estimate(pool: Dict, remaining: Optional[int], total_packs: Optional[int],
             pulled_keys: Set[str], price: Optional[int], out_value: Optional[int] = None) -> Optional[Dict]:
    """Ø-Rückgabe pro Zug und Hit-Chance für die verbleibenden Packs.

    Mit out_value (Kartenwert, der laut Seite schon umgewandelt/verschickt ist, siehe
    out_of_banner_value) wird die Ø-Rückgabe aus echten Zahlen berechnet: (Poolwert - raus) / Restpacks.
    Ohne diese Zahlen Schätzung: verfolgte Einheiten gelten als noch drin, solange sie nicht als
    gezogen bekannt sind; alle übrigen Züge zählen als Durchschnittszüge aus dem Rest.
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
    pulled = max(0, min(total, n_pool) - remaining)
    unknown_pulls = max(0, min(pulled - known_pulled, rest_count))
    rest_left_share = (rest_count - unknown_pulls) / rest_count if rest_count > 0 else 0.0

    value_left = sum(u["value"] for u in open_units) + rest_value * rest_left_share
    relevant = [u for u in units if is_relevant_hit(u, price)]
    relevant_open = [u for u in open_units if is_relevant_hit(u, price)]
    if pool.get("hits"):
        hits_total, hits_left = len(relevant), float(len(relevant_open))
    else:
        hits_total = pool.get("hits_total", 0)
        rest_hits = hits_total - sum(1 for u in units if u.get("hit", u["shipping_only"]))
        hits_left = (sum(1 for u in open_units if u.get("hit", u["shipping_only"]))
                     + max(0, rest_hits) * rest_left_share)
    # Echte Zahlen der Seite, wenn plausibel (mehr raus als im Pool deutet auf einen neuen Pool hin)
    data_based = out_value is not None and out_value <= pool["total_value"] * 1.02
    if data_based:
        value_left = max(0, pool["total_value"] - out_value)
    ev = value_left / remaining
    keys = tier_keys(pool)
    hits_open_now = len(relevant_open) if pool.get("hits") else hits_left
    return {
        # Erwartete Züge bis zum ersten Hit (ohne Zurücklegen): (N + 1) / (h + 1)
        "cost_to_hit": price * (remaining + 1) / (hits_open_now + 1) if price and hits_open_now >= 1 else None,
        "ev": ev,
        "ev_pct": ev / price * 100 if price else None,
        "estimated": pulled > 0,
        "data_based": data_based,
        "hits_total": hits_total,
        "hits_open": len(relevant_open) if pool.get("hits") else sum(
            1 for u in open_units if u.get("hit", u["shipping_only"])),
        "hits_left": hits_left,
        "hit_chance_pct": min(100.0, hits_left / remaining * 100),
        "open_tiers": [t for t in TIERS if t in keys and keys[t] not in pulled_keys],
        "tracked_hits": bool(pool.get("hits")),
        "open_units": relevant_open if pool.get("hits") else open_units,
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


def _value_classes(units: List[Dict], tol: float) -> List[Dict]:
    """Fasst Hits mit fast gleichem Wert (innerhalb 2 x Toleranz) zu Klassen zusammen."""
    classes: List[Dict] = []
    for unit in sorted(units, key=lambda u: -u["value"]):
        if classes and unit["value"] >= classes[-1]["max"] * (1 - 2 * tol):
            cls = classes[-1]
            cls["keys"].append(unit["key"])
            cls["min"] = unit["value"]
        else:
            classes.append({"keys": [unit["key"]], "max": unit["value"], "min": unit["value"]})
    return classes


def _any_bit(mask: int, lo: int, hi: int) -> bool:
    lo = max(lo, 0)
    if hi < lo:
        return False
    return (mask >> lo) & ((1 << (hi - lo + 1)) - 1) != 0


def _batch_options(pool: Dict, classes: List[Dict], count: int, value: int, tol: float) -> Optional[List[tuple]]:
    """Alle Aufteilungen eines Schubs (count Karten, Kartenwert `value`) als Hits je Klasse.

    Rest muss aus genau so vielen normalen Karten bestehen. None = zu viele Möglichkeiten.
    """
    upper = int(value / (1 - tol)) + 2
    sums = _normal_sums(pool, count, upper)
    possible: List[tuple] = []

    def walk(i: int, taken: List[int], lo: float, hi: float, n: int):
        if lo > upper or len(possible) > 50000:
            return
        if i == len(classes):
            # Jede Karte darf um ±tol abweichen: hits_lo + N·(1-tol) <= value <= hits_hi + N·(1+tol)
            k = count - n
            if _any_bit(sums[k], int((value - hi) / (1 + tol)), int((value - lo) / (1 - tol)) + 1):
                possible.append(tuple(taken))
            return
        cls = classes[i]
        for m in range(0, min(len(cls["keys"]), count - n) + 1):
            walk(i + 1, taken + [m], lo + m * cls["min"] * (1 - tol), hi + m * cls["max"] * (1 + tol), n + m)

    walk(0, [], 0.0, 0.0, 0)
    return None if len(possible) > 50000 else possible


# Normale Karten unter diesem Vielfachen des Packpreises werden fast immer umgewandelt, nicht verschickt
SHIP_NORMAL_MIN_FACTOR = 3


def _valuable_pool(pool: Dict, price: Optional[int]) -> Optional[Dict]:
    """Pool, in dem nur normale Karten ab SHIP_NORMAL_MIN_FACTOR × Packpreis vorkommen (None ohne Preis)."""
    if not price:
        return None
    limit = SHIP_NORMAL_MIN_FACTOR * price
    return {**pool, "normal_values": {v: n for v, n in (pool.get("normal_values") or {}).items() if int(v) >= limit}}


def _ship_options(pool: Dict, classes: List[Dict], count: int, value: int, tol: float,
                  price: Optional[int] = None, hits_needed: bool = False) -> Optional[List[tuple]]:
    """Mögliche Aufteilungen eines Versand-Schubs, in dieser Rangfolge (die erste, die aufgeht, gilt):
      1. mit wertvollen normalen Karten (ab 3× Packpreis) - als Karten, sonst als Versand-Aufträge
         (dort zuerst allein aus Versand-Hits, siehe order_options)
      2. mit allen Karten - billige Karten werden fast immer umgewandelt, aber nicht immer
    hits_needed: als Versand-Aufträge nur zählen, wenn dabei ein Hit ins Spiel kommt (Einzelansicht).
    None = zu viele Möglichkeiten."""
    def options(p):
        found = _batch_options(p, classes, count, value, tol) if count > 0 else []
        if found is None:
            return None
        if not found and classes and value <= ORDER_MAX_VALUE:
            orders = _order_class_options(p, classes, count, value)
            found = orders if not hits_needed or any(any(t) for t in orders) else []
        return found
    valuable = _valuable_pool(pool, price)
    if valuable is not None:
        found = options(valuable)
        if found:
            return found
    return options(pool)


def _summarize_options(classes: List[Dict], possible: List[tuple], units: List[Dict]) -> Dict:
    """Was in jeder möglichen Aufteilung gilt: sichere Hits und ❓-Gruppen."""
    result = {"certain": [], "groups": [], "maybe": []}
    unit_by_key = {u["key"]: u for u in units}

    def identical(keys):
        # Mehrere Exemplare derselben Karte: egal welches verschickt wurde
        return len({(unit_by_key[k]["name"], unit_by_key[k]["value"]) for k in keys}) == 1

    certain_per_class = [min(t[i] for t in possible) for i in range(len(classes))]
    for cls, n in zip(classes, certain_per_class):
        if n <= 0:
            continue
        if len(cls["keys"]) <= n:
            result["certain"] += cls["keys"]
        elif identical(cls["keys"]):
            result["certain"] += cls["keys"][:n]
        else:
            result["groups"].append({"value": cls["min"], "value_max": cls["max"], "keys": cls["keys"], "pulled": n})

    # Mindestens so viele Hits stecken in jeder möglichen Aufteilung, auch wenn die Klasse offen ist
    extra = min(sum(t) for t in possible) - sum(certain_per_class)
    if extra > 0:
        involved = [cls for i, cls in enumerate(classes) if any(t[i] > certain_per_class[i] for t in possible)]
        result["groups"].append({
            "value": min(c["min"] for c in involved), "value_max": max(c["max"] for c in involved),
            "keys": [k for c in involved for k in c["keys"]], "pulled": extra,
        })
    return result


ORDER_MAX_NORMALS = 60      # so viele normale Karten kann ein Versand-Schub höchstens enthalten
ORDER_MAX_VALUE = 3_000_000   # darüber nur nach dem Karten-Modell (Rechnung ~1,5 s je 1,5 Mio., einmal je Banner)
_ORDER_CACHE: Dict[tuple, List[tuple]] = {}
_SUMS_CACHE: Dict[tuple, tuple] = {}   # Summen-Tabelle der normalen Karten je Pool (groß - nur die letzten wenigen)


def _pool_sums(pool: Dict, value: int) -> List[int]:
    """_normal_sums für Werte bis `value`, je Pool einmal berechnet (für größere Werte neu, mit Reserve)."""
    key = tuple(sorted(pool.get("normal_values", {}).items()))
    cached = _SUMS_CACHE.get(key)
    if cached and cached[0] >= value:
        return cached[1]
    limit = int(value * 1.25) + 1000
    sums = _normal_sums(pool, ORDER_MAX_NORMALS, limit)
    if len(_SUMS_CACHE) >= 4:   # je Banner: Pool mit wertvollen und mit allen Karten
        _SUMS_CACHE.pop(next(iter(_SUMS_CACHE)))
    _SUMS_CACHE[key] = (limit, sums)
    return sums
ORDER_MAX_HITS = 4          # so viele Versand-Hits höchstens in einem Schub (mehr ist praktisch nie)


def order_options(pool: Dict, orders: int, value: int, tol: Optional[int] = None,
                  max_normals: int = ORDER_MAX_NORMALS, sums: Optional[List[int]] = None,
                  prefer_hits: bool = True) -> List[frozenset]:
    """Modell "Aufträge": total_sendcount zählt Versand-Aufträge, nicht Karten.

    Ein Auftrag enthält mindestens eine, aber beliebig viele Karten; orders = 0 heißt, Karten kamen zu einem
    bestehenden Auftrag dazu. Dafür muss der Wert (fast) exakt aufgehen - die Seite zählt ganze Coins.
    value = Kartenwert (Versandsumme × 1,1). Gibt alle möglichen Mengen verschickter Versand-Hits zurück
    (leere Menge = nur normale Karten möglich); leere Liste = gar nicht erklärbar.
    """
    hits = [u for u in tracked_units(pool) if u.get("shipping_only")]
    tol = tol if tol is not None else max(2, round(value * 0.001))
    if sums is None:
        sums = (_pool_sums(pool, value + tol) if max_normals == ORDER_MAX_NORMALS
                else _normal_sums(pool, max_normals, value + tol))
    found, hits_only = [], []
    for r in range(0, min(len(hits), ORDER_MAX_HITS) + 1):
        for combo in combinations(hits, r):
            rest = value - sum(u["value"] for u in combo)
            if rest < -tol:
                continue
            keys = frozenset(u["key"] for u in combo)
            if r and r >= orders and abs(rest) <= tol:
                hits_only.append(keys)
            need = max(orders - r, 0)       # mindestens eine Karte je Auftrag
            if r == 0:
                need = max(need, 1)            # ganz ohne Hit braucht der Wert mindestens eine normale Karte
            for j in range(need, len(sums)):
                if _any_bit(sums[j], rest - tol, rest + tol):
                    found.append(keys)
                    break
    # Normale Karten wandelt praktisch jeder um, verschickt werden die großen Hits: geht der Schub allein mit
    # Versand-Hits auf, zählen nur diese Erklärungen; normale Karten nur, wenn es ohne sie nicht geht
    if hits_only and prefer_hits:
        return list(dict.fromkeys(hits_only))
    return found


def _order_class_options(pool: Dict, classes: List[Dict], orders: int, value: int) -> List[tuple]:
    """order_options als Anzahl Hits je Wert-Klasse (wie _batch_options); Ergebnis zwischengespeichert,
    weil alle Schübe eines Banners bei jedem neuen Schub erneut ausgewertet werden."""
    fingerprint = (tuple(sorted(pool.get("normal_values", {}).items())),
                   tuple((tuple(c["keys"]), c["min"], c["max"]) for c in classes))
    key = (fingerprint, orders, value)
    if key not in _ORDER_CACHE:
        if len(_ORDER_CACHE) > 5000:
            _ORDER_CACHE.clear()
        found = order_options(pool, orders, value)
        _ORDER_CACHE[key] = sorted({tuple(sum(1 for k in combo if k in c["keys"]) for c in classes)
                                    for combo in found})
    return _ORDER_CACHE[key]


def match_shipped_hits(pool: Dict, count: int, value: int, pulled_keys: Set[str],
                       tol: float = VALUE_TOLERANCE) -> Dict:
    """Welche Versand-Hits stecken in einer einzelnen Sendung aus `count` Karten im Wert `value`?

    `value` ist der Betrag aus total_sendprice, also ohne Steuer; er wird mit TAX_FACTOR auf
    Kartenwerte umgerechnet. Hits plus genau so viele normale Karten müssen ihn auf ±tol treffen.
    Hits mit fast gleichem Wert bilden Klassen und sind nicht unterscheidbar. Ergebnis:
      certain: Schlüssel von Hits, die sicher verschickt wurden
      groups:  Hits, von denen sicher `pulled` Stück verschickt wurden, aber unklar welche
               (value/value_max: Wertspanne der Gruppe)
      maybe:   Einzelsendung, die zu Hits passt, aber auch zu einer normalen Karte (nicht sicher)
    Nicht zerlegbare oder zu große Sendungen liefern nichts.
    """
    result = {"certain": [], "groups": [], "maybe": []}
    value = round(value * TAX_FACTOR)
    if count <= 0 or value <= 0 or count > MAX_SHIPMENT_CARDS or value > MAX_SHIPMENT_VALUE:
        return result
    open_hits = [u for u in tracked_units(pool) if u["shipping_only"] and u["key"] not in pulled_keys]
    if not open_hits:
        return result
    classes = _value_classes(open_hits, tol)
    possible = _batch_options(pool, classes, count, value, tol)
    if not possible:
        return result
    result = _summarize_options(classes, possible, open_hits)
    if count == 1 and not result["certain"] and not result["groups"]:
        for i, cls in enumerate(classes):
            if any(t[i] for t in possible):
                result["maybe"].append({"value": cls["min"], "value_max": cls["max"],
                                        "keys": cls["keys"], "pulled": 0})
    return result


def explain_batch(pool: Dict, count: int, value: int, pulled_keys: Set[str],
                  tol: float = VALUE_TOLERANCE, required: Set[str] = frozenset(),
                  price: Optional[int] = None) -> Dict:
    """Was steckt in einem einzelnen Versandschub? Für die Anzeige pro Schub.

    kind: "hits"    - mindestens ein Versand-Hit ist sicher oder als ❓-Gruppe drin
          "maybe"   - passt zu Hits, aber auch zu nur normalen Karten
          "normal"  - nur normale Karten möglich
          "unclear" - keine Kombination passt (z.B. Hit schon vorher gezählt)
          "too_big" - zu viele Karten/zu viel Wert zum Zerlegen
    """
    gross = round(value * TAX_FACTOR)
    base = {"kind": "unclear", "value": gross, "certain": [], "groups": [], "maybe": []}
    if count <= 0 or gross <= 0:
        return {**base, "kind": "normal"}
    if count > MAX_SHIPMENT_CARDS or gross > MAX_SHIPMENT_VALUE:
        return {**base, "kind": "too_big"}
    open_hits = [u for u in tracked_units(pool) if u["shipping_only"] and u["key"] not in pulled_keys]
    classes = _value_classes(open_hits, tol) if open_hits else []
    # wie match_shipment_history; als Versand-Aufträge nur, wenn dabei ein Hit ins Spiel kommt
    # (aus vielen normalen Karten lässt sich fast jeder Betrag bilden, das erklärt nichts)
    possible = _ship_options(pool, classes, count, gross, tol, price, hits_needed=True)
    if possible is None:
        return {**base, "kind": "too_big"}
    if not possible:
        return base
    # Hits, deren Medaillen-Frist dieser Schub ist (batch_deadlines), müssen drin sein - wenn das aufgeht
    need = [sum(1 for k in required if k in cls["keys"]) for cls in classes]
    forced = [k for k in required if any(k in cls["keys"] for cls in classes)]
    if forced:
        kept = [t for t in possible if all(x >= n for x, n in zip(t, need))]
        if kept:
            possible = kept
        else:
            forced = []
    if all(not any(t) for t in possible):
        return {**base, "kind": "normal"}
    match = _summarize_options(classes, possible, open_hits)
    if forced:
        match["certain"] = list(dict.fromkeys(match["certain"] + sorted(forced)))
        groups = []
        for g in match["groups"]:
            pulled = g["pulled"] - sum(1 for k in g["keys"] if k in forced)
            rest = [k for k in g["keys"] if k not in forced and k not in match["certain"]]
            if pulled > 0 and rest:
                groups.append({**g, "keys": rest, "pulled": min(pulled, len(rest))})
        match["groups"] = groups
    if match["certain"] or match["groups"]:
        return {**base, "kind": "hits", **{k: match[k] for k in ("certain", "groups")}}
    maybe = [{"value": cls["min"], "value_max": cls["max"], "keys": cls["keys"], "pulled": 0}
             for i, cls in enumerate(classes) if any(t[i] for t in possible)]
    return {**base, "kind": "maybe", "maybe": maybe}


def batch_deadlines(batches: List[List], medal_t: Dict[str, float]) -> Dict[str, int]:
    """Frist je Hit mit Medaille: Index des ersten Schubs, der nach dem Setzen der Medaille aufgezeichnet wurde.

    Regel der Gruppe: wer eine Medaille setzt, hat die Karte schon zum Versand angefordert - die Anforderung
    erhöht sofort den Versand-Zähler der Seite. Die Karte steckt also spätestens in diesem Schub (oder in einem
    früheren, wenn die Medaille erst später gesetzt wurde). Schübe ohne Zeit (vor der ersten Aufzeichnung)
    zählen als früher. Ohne Schub nach der Medaille gibt es (noch) keine Frist."""
    out = {}
    for key, t in medal_t.items():
        if t is None:
            continue
        for i, b in enumerate(batches):
            if len(b) > 2 and b[2] is not None and b[2] >= t:
                out[key] = i
                break
    return out


def match_shipment_history(pool: Dict, batches: List[List], tol: float = VALUE_TOLERANCE,
                           deadlines: Optional[Dict[str, int]] = None, price: Optional[int] = None) -> Dict:
    """Wertet alle Versand-Schübe eines Banners gemeinsam aus.

    batches: [[Anzahl Karten, Betrag aus total_sendprice, Zeit (optional)], ...] je Schub. Jeder Versand-Hit
    kann insgesamt nur einmal verschickt werden; deshalb schließen spätere Schübe Möglichkeiten aus
    früheren aus. Schübe, die zu groß oder gar nicht erklärbar sind, werden übergangen (das macht
    das Ergebnis nur vorsichtiger, nie falsch).
    deadlines (batch_deadlines): Hit mit Medaille -> Schub, bis zu dem er verschickt sein muss. Nur
    Aufteilungen, die alle Fristen einhalten, bleiben übrig; diese Hits gelten dann als sicher verschickt.
    Passt eine Frist zu keiner Aufteilung (Medaille irrtümlich, Karte doch umgewandelt, Frist-Schub
    übergangen), wird sie ignoriert und unter "ignored_deadlines" gemeldet.
    price (Packpreis): billige normale Karten nur, wenn es ohne sie nicht aufgeht (siehe _ship_options).
    Ergebnis wie match_shipped_hits, plus "used_batches".
    """
    result = {"certain": [], "groups": [], "maybe": [], "used_batches": 0, "ignored_deadlines": []}
    hits = [u for u in tracked_units(pool) if u["shipping_only"]]
    if not hits:
        return result
    classes = _value_classes(hits, tol)
    sizes = [len(c["keys"]) for c in classes]
    class_of = {k: i for i, c in enumerate(classes) for k in c["keys"]}
    due = {k: j for k, j in (deadlines or {}).items() if k in class_of}
    enforced: Set[str] = set()   # Fristen, die gelten (Frist-Schub ausgewertet, mit den Schüben vereinbar)
    states = {tuple([0] * len(classes))}
    for i, b in enumerate(batches):
        count, net = b[0], b[1]
        now_due = [k for k, j in due.items() if j == i]
        value = round(net * TAX_FACTOR)
        options = None
        if not (count < 0 or value <= 0 or count > MAX_SHIPMENT_CARDS or value > MAX_SHIPMENT_VALUE):
            # Geht der Schub als Karten nicht auf: der Zähler zählt manchmal Versand-Aufträge (mehrere Karten
            # je Auftrag, Karten nachträglich zu einem Auftrag) - dann muss der Wert fast exakt aufgehen
            options = _ship_options(pool, classes, count, value, tol, price)
        if not options:
            result["ignored_deadlines"] += now_due   # Frist-Schub nicht auswertbar: Frist nicht prüfbar
            continue
        combined = set()
        for st in states:
            for opt in options:
                cand = tuple(x + y for x, y in zip(st, opt))
                if all(c <= sz for c, sz in zip(cand, sizes)):
                    combined.add(cand)
            if len(combined) > 50000:
                return result
        if not combined:
            result["ignored_deadlines"] += now_due
            continue  # widerspricht den anderen Schüben: übergehen statt falsch zuordnen
        if now_due:
            def need(keys):
                req = [0] * len(classes)
                for k in keys:
                    req[class_of[k]] += 1
                return req
            req = need(enforced | set(now_due))
            kept = {st for st in combined if all(x >= r for x, r in zip(st, req))}
            if kept:
                combined = kept
                enforced |= set(now_due)
            else:
                result["ignored_deadlines"] += now_due
        states = combined
        result["used_batches"] += 1
    if not result["used_batches"]:
        return result
    summary = _summarize_options(classes, list(states), hits)
    if enforced:
        # Hits mit eingehaltener Frist sind sicher verschickt - aus ihren ❓-Gruppen herausnehmen
        summary["certain"] = list(dict.fromkeys(summary["certain"] + sorted(enforced)))
        groups = []
        for g in summary["groups"]:
            pulled = g["pulled"] - sum(1 for k in g["keys"] if k in enforced)
            rest = [k for k in g["keys"] if k not in enforced and k not in summary["certain"]]
            if pulled > 0 and rest:
                groups.append({**g, "keys": rest, "pulled": min(pulled, len(rest))})
        summary["groups"] = groups
    result.update(summary)
    return result


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


def card_value_changes(old_pool: Optional[Dict], new_pool: Optional[Dict]) -> List[Dict]:
    """Karten, deren Wert sich zwischen zwei geladenen Pools geändert hat (nur mit vollständiger Kartenliste)."""
    if not old_pool or not new_pool or not old_pool.get("cards") or not new_pool.get("cards"):
        return []
    before = {str(c["id"]): c["value"] for c in old_pool["cards"]}
    return [{"id": str(c["id"]), "name": c["name"], "old": before[str(c["id"])], "new": c["value"]}
            for c in new_pool["cards"] if str(c["id"]) in before and before[str(c["id"])] != c["value"]]


def card_value(counted: Optional[int]) -> int:
    """Gezählter Versandwert der Seite (ohne Steuer) -> Kartenwert, wie er an den Karten steht."""
    return round((counted or 0) * TAX_FACTOR)


def fmt_coins(value: float) -> str:
    return f"{round(value):,}".replace(",", ".")


def fmt_pct(value: float, digits: int = 1) -> str:
    return f"{value:.{digits}f}".replace(".", ",")
