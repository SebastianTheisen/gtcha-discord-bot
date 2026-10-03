"""Japanische Karten- und Pack-Namen auf Deutsch.

Zuerst ein eigenes Wörterbuch (Schmuck, Steine, Begriffe der Seite) - exakt und ohne Netz. Was es nicht kennt,
übersetzt MyMemory (kostenlos, ohne Anmeldung, ca. 5.000 Zeichen am Tag) einmal - oder DeepL, wenn in der .env
ein DEEPL_API_KEY steht. Das Ergebnis liegt in der Tabelle translations und gilt danach für immer. Bei Fehlern
oder erschöpftem Tageskontingent bleibt der Originaltext stehen und wird beim nächsten Lauf erneut versucht.

Namen werden an "/" in Teile zerlegt (z. B. "ｴﾒﾗﾙﾄﾞ/ﾙｰｽ/ダイヤバンク") - jeder Teil wird für sich übersetzt,
so landen wiederkehrende Teile nur einmal bei DeepL.
"""

import logging
import re
import unicodedata
from typing import Dict, Iterable, List, Optional, Tuple

logger = logging.getLogger(__name__)

JAPANESE = re.compile(r"[぀-ヿㇰ-ㇿ㐀-䶿一-鿿ｦ-ﾟ]")
DEEPL_BATCH = 50
MYMEMORY_URL = "https://api.mymemory.translated.net/get"
MYMEMORY_PER_RUN = 40      # je Lauf (alle 5 Minuten) - schont das Tageskontingent

# Wörter nach NFKC (halbbreite Katakana wie ｴﾒﾗﾙﾄﾞ werden dabei zu エメラルド). Längere Wörter zuerst ersetzt.
WORDS = {
    "ダイヤバンク": "Dia Bank", "ルース": "loser Stein", "鑑別書": "Gutachten", "鑑定書": "Zertifikat",
    "宝石ガチャ": "Edelstein-Gacha", "宝石": "Edelstein", "ガチャ": "Gacha", "コイン": "Coins", "オリパ": "Orip",
    "エメラルド": "Smaragd", "ルビー": "Rubin", "ダイヤモンド": "Diamant", "ダイヤ": "Diamant",
    "サファイヤ": "Saphir", "サファイア": "Saphir", "バイオレット": "Violetter ", "ピンク": "Rosa ",
    "イエロー": "Gelber ", "ブルー": "Blauer ", "パパラチア": "Padparadscha-", "スター": "Stern-",
    "サンゴ": "Koralle", "珊瑚": "Koralle", "ファイアオパール": "Feueropal", "ブラックオパール": "Schwarzer Opal",
    "オパール": "Opal", "アコヤ": "Akoya-Perle", "南洋": "Südsee-", "真珠": "Perle", "パール": "Perle",
    "アクアマリン": "Aquamarin", "パライバトルマリン": "Paraiba-Turmalin", "トルマリン": "Turmalin",
    "アメジスト": "Amethyst", "ガーネット": "Granat", "トパーズ": "Topas", "ペリドット": "Peridot",
    "スピネル": "Spinell", "タンザナイト": "Tansanit", "アレキサンドライト": "Alexandrit", "翡翠": "Jade",
    "ヒスイ": "Jade", "ジェイド": "Jade", "ムーンストーン": "Mondstein", "クリソベリル": "Chrysoberyll",
    "キャッツアイ": "Katzenauge", "ターコイズ": "Türkis", "ラピスラズリ": "Lapislazuli", "モルガナイト": "Morganit",
    "シトリン": "Citrin", "クォーツ": "Quarz", "水晶": "Bergkristall", "ルチル": "Rutil",
    "ネックレス": "Halskette", "ペンダント": "Anhänger", "リング": "Ring", "指輪": "Ring", "ピアス": "Ohrstecker",
    "イヤリング": "Ohrringe", "ブレスレット": "Armband", "ブローチ": "Brosche", "ルース石": "loser Stein",
    "ゴールド": "Gold", "プラチナ": "Platin", "シルバー": "Silber", "カラット": " ct",
}
_WORDS = sorted(WORDS.items(), key=lambda kv: -len(kv[0]))

_cache: Dict[str, str] = {}


def normalize(text: str) -> str:
    return unicodedata.normalize("NFKC", text or "").strip()


def has_japanese(text: Optional[str]) -> bool:
    return bool(text and JAPANESE.search(text))


def segments(text: str) -> List[str]:
    return [s.strip() for s in normalize(text).split("/")]


def local(segment: str) -> Optional[str]:
    """Teil nur mit dem Wörterbuch übersetzen; None, wenn danach noch Japanisch übrig ist."""
    out = segment
    for ja, de in _WORDS:
        out = out.replace(ja, de)
    out = re.sub(r"\s{2,}", " ", out).strip()
    return None if has_japanese(out) else out


def set_cache(translations: Dict[str, str]) -> None:
    _cache.clear()
    _cache.update(translations)


def to_german(text: Optional[str]) -> Optional[str]:
    """Deutscher Name (Wörterbuch, sonst gespeicherte DeepL-Übersetzung, sonst Original des Teils)."""
    if not has_japanese(text):
        return text
    parts = []
    for seg in segments(text):
        if not has_japanese(seg):
            parts.append(seg)
        else:
            parts.append(local(seg) or _cache.get(seg) or seg)
    return " / ".join(p for p in parts if p)


def missing(texts: Iterable[Optional[str]]) -> List[str]:
    """Teile, die weder das Wörterbuch noch der Zwischenspeicher kennen (die müssen zu DeepL)."""
    out = []
    for text in texts:
        if not has_japanese(text):
            continue
        for seg in segments(text):
            if has_japanese(seg) and local(seg) is None and seg not in _cache and seg not in out:
                out.append(seg)
    return out


def translate_pool(pool: Optional[Dict]) -> Tuple[Optional[Dict], bool]:
    """Kartennamen im Pool auf Deutsch (Original bleibt als name_ja). (Pool, geändert?)"""
    if not pool:
        return pool, False
    changed = False
    for key in ("cards", "hits", "top"):
        for card in pool.get(key) or []:
            original = card.get("name_ja") or card.get("name")
            german = to_german(original)
            if german and german != card.get("name"):
                card["name_ja"], card["name"] = original, german
                changed = True
    low = pool.get("min")
    if isinstance(low, dict) and has_japanese(low.get("name")):
        german = to_german(low["name"])
        if german != low["name"]:
            low["name"], changed = german, True
    return pool, changed


def pool_texts(pool: Optional[Dict]) -> List[str]:
    if not pool:
        return []
    return [c.get("name_ja") or c.get("name") for key in ("cards", "hits", "top") for c in pool.get(key) or []]


async def deepl(texts: List[str], key: str) -> Dict[str, str]:
    """Übersetzt mit DeepL (Japanisch -> Deutsch). Gibt nur erfolgreich übersetzte Texte zurück."""
    import aiohttp
    if not texts or not key:
        return {}
    host = "api-free.deepl.com" if key.endswith(":fx") else "api.deepl.com"
    result: Dict[str, str] = {}
    async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=30)) as session:
        for i in range(0, len(texts), DEEPL_BATCH):
            batch = texts[i:i + DEEPL_BATCH]
            try:
                async with session.post(f"https://{host}/v2/translate",
                                        headers={"Authorization": f"DeepL-Auth-Key {key}"},
                                        json={"text": batch, "source_lang": "JA", "target_lang": "DE"}) as resp:
                    if resp.status != 200:
                        logger.warning(f"[ÜBERSETZUNG] DeepL antwortet {resp.status}: {(await resp.text())[:200]}")
                        break
                    data = await resp.json()
            except Exception as e:
                logger.warning(f"[ÜBERSETZUNG] DeepL nicht erreichbar: {type(e).__name__}: {e}")
                break
            for src, tr in zip(batch, data.get("translations") or []):
                text = (tr.get("text") or "").strip()
                if text:
                    result[src] = text
    return result


async def mymemory(texts: List[str], limit: int = MYMEMORY_PER_RUN) -> Dict[str, str]:
    """Übersetzt mit MyMemory (Japanisch -> Deutsch), ohne Key. Gibt nur brauchbare Übersetzungen zurück und hört
    beim ersten Fehler (z. B. Tageskontingent erschöpft) auf - der Rest kommt beim nächsten Lauf dran."""
    import aiohttp
    result: Dict[str, str] = {}
    if not texts:
        return result
    async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=20)) as session:
        for text in texts[:limit]:
            try:
                async with session.get(MYMEMORY_URL, params={"q": text, "langpair": "ja|de"}) as resp:
                    data = await resp.json(content_type=None)
            except Exception as e:
                logger.warning(f"[ÜBERSETZUNG] MyMemory nicht erreichbar: {type(e).__name__}: {e}")
                break
            status = str(data.get("responseStatus") or resp.status)
            german = ((data.get("responseData") or {}).get("translatedText") or "").strip()
            if status != "200" or "MYMEMORY WARNING" in german.upper():
                logger.info(f"[ÜBERSETZUNG] MyMemory: {status} {german[:120]} - später erneut")
                break
            # unbrauchbar: leer, unverändert oder noch japanisch -> nicht speichern (Original bleibt)
            if german and german != text and not has_japanese(german):
                result[text] = german
    return result
