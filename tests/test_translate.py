"""Japanische Namen auf Deutsch: Wörterbuch, DeepL-Zwischenspeicher, Pools und Titel in der App."""

import asyncio

import pytest

from database.db import Database
from utils import translate
from utils.card_pool import summarize_cards


@pytest.fixture(autouse=True)
def clean_cache():
    translate.set_cache({})
    yield
    translate.set_cache({})


def test_dictionary_translates_store_jewel_names():
    # so liefert die Seite die Namen (halbbreite Katakana)
    assert translate.to_german("ｴﾒﾗﾙﾄﾞ/ﾙｰｽ/ダイヤバンク") == "Smaragd / loser Stein / Dia Bank"
    assert translate.to_german("ﾊﾞｲｵﾚｯﾄｻﾌｧｲﾔ/ﾙｰｽ/ダイヤバンク") == "Violetter Saphir / loser Stein / Dia Bank"
    assert translate.to_german("ﾌｧｲｱｵﾊﾟｰﾙ/ﾙｰｽ/ダイヤバンク") == "Feueropal / loser Stein / Dia Bank"
    assert translate.to_german("ｻﾝｺﾞ/ﾙｰｽ/ダイヤバンク") == "Koralle / loser Stein / Dia Bank"
    assert translate.to_german("宝石ガチャ BtoB") == "Edelstein-Gacha BtoB"
    assert translate.to_german("Pikachu ex SAR") == "Pikachu ex SAR"          # nichts Japanisches: unverändert
    assert translate.to_german(None) is None


def test_unknown_parts_use_cache_and_stay_original_without_it():
    assert translate.missing(["ルフィ/ﾙｰｽ", "Lugia"]) == ["ルフィ"]
    assert translate.to_german("ルフィ/ﾙｰｽ") == "ルフィ / loser Stein"               # ohne Übersetzung: Original bleibt
    translate.set_cache({"ルフィ": "Ruffy"})
    assert translate.to_german("ルフィ/ﾙｰｽ") == "Ruffy / loser Stein"
    assert translate.missing(["ルフィ/ﾙｰｽ"]) == []


def test_translate_pool_keeps_original_and_is_stable():
    pool = summarize_cards([{"id": 1, "name": "ｴﾒﾗﾙﾄﾞ/ﾙｰｽ/ダイヤバンク", "buy_point": 171000, "duplication": 1},
                            {"id": 2, "name": "2,000coins", "buy_point": 2000, "duplication": 191}])
    pool, changed = translate.translate_pool(pool)
    assert changed and pool["cards"][0]["name"] == "Smaragd / loser Stein / Dia Bank"
    assert pool["cards"][0]["name_ja"] == "ｴﾒﾗﾙﾄﾞ/ﾙｰｽ/ダイヤバンク" and "name_ja" not in pool["cards"][1]
    assert pool["top"][0]["name"] == "Smaragd / loser Stein / Dia Bank"
    assert translate.translate_pool(pool) == (pool, False)                    # zweiter Lauf ändert nichts


def test_bot_job_translates_pools_with_deepl_once(tmp_path, monkeypatch):
    monkeypatch.setenv("DISCORD_TOKEN", "x")
    monkeypatch.setenv("GUILD_ID", "1")
    monkeypatch.setenv("DEEPL_API_KEY", "abc:fx")
    calls = []

    async def fake_deepl(texts, key):
        calls.append(list(texts))
        return {"ルフィ": "Ruffy"}

    monkeypatch.setattr(translate, "deepl", fake_deepl)

    async def run():
        from bot.translate import TranslateMixin
        db = Database(str(tmp_path / "b.db"))
        await db.init()
        await db.upsert_store_pack(24126, "宝石ガチャ BtoB", 5000, 200, 200, None, None, None, "x")
        pool = summarize_cards([{"id": 1, "name": "ルフィ/ﾙｰｽ", "buy_point": 9000, "duplication": 1},
                                {"id": 2, "name": "ｱｺﾔ/ﾙｰｽ", "buy_point": 900, "duplication": 9}])
        await db.save_card_pool(24126, pool)

        class Bot(TranslateMixin):
            def __init__(self):
                self.db = db

        bot = Bot()
        await bot._translate_names()
        await bot._translate_names()
        saved = await db.get_card_pool(24126)
        assert [c["name"] for c in saved["cards"]] == ["Ruffy / loser Stein", "Akoya-Perle / loser Stein"]
        assert calls == [["ルフィ"]]                                          # nur Unbekanntes, nur einmal
        assert await db.get_translations() == {"ルフィ": "Ruffy"}

        # App: Titel übersetzt, Kartennamen deutsch
        from webapp.view import BannerView
        translate.set_cache({})
        view = BannerView(db)
        row = await db.get_banner(24126)
        data = await view.summary(row, with_pool=True)
        assert data["headline"] == data["title"] == "Edelstein-Gacha BtoB"
        assert row["title"] == "宝石ガチャ BtoB"                                # in der DB bleibt das Original
        assert data["cards_brief"]["1"][0] == "Ruffy / loser Stein"

    asyncio.run(run())
