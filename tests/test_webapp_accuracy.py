import asyncio

from webapp.accuracy import AccuracyStore, evaluate


def test_evaluate_weights_prediction_by_sold_packs():
    # (banner, t, ev %, übrig, raus, Preis, Titel): 100 Packs zu 1.000 Coins verkauft
    snaps = [(1, 0, 120.0, 200, 50_000, 1000, "A"), (1, 10, 100.0, 140, 120_000, 1000, "A"),
             (1, 20, 90.0, 100, 150_000, 1000, "A")]
    r = evaluate({1: snaps, 2: [(2, 0, 100.0, 50, 0, 1000, "B"), (2, 5, 100.0, 40, 9000, 1000, "B")]}, lag=0)
    assert r["count"] == 1                               # Banner 2: zu wenig verkauft
    item = r["items"][0]
    assert item["predicted"] == round((120 * 60 + 100 * 40) / 100, 1) == 112.0
    assert item["realized"] == 100.0 and item["diff"] == 12.0
    assert r["mean_abs"] == 12.0 and r["bias"] == 12.0


def test_record_only_when_time_passed_or_value_changed(tmp_path):
    import time
    t0 = int(time.time())

    async def run():
        store = AccuracyStore(str(tmp_path / "w.db"))
        await store.init()
        b = {"id": 5, "ev_pct": 101.0, "remaining": 80, "price": 1000, "converted": 1000, "ship_value": 500, "title": "X"}
        assert await store.record([b], now=t0) == 1
        assert await store.record([{**b, "ev_pct": 102.0}], now=t0 + 100) == 0      # kaum Änderung, zu früh
        assert await store.record([{**b, "ev_pct": 106.0}], now=t0 + 200) == 1      # +5 %-Punkte
        assert await store.record([{**b, "ev_pct": 106.5}], now=t0 + 200 + 1800) == 1   # 30 Minuten später
        assert await store.record([{**b, "ev_pct": None}], now=t0 + 9000) == 0
        assert [p["ev"] for p in await store.history(5)] == [101.0, 106.0, 106.5]
        store2 = AccuracyStore(store.db_path)                                    # Neustart: letzter Stand bekannt
        await store2.init()
        assert await store2.record([{**b, "ev_pct": 106.5}], now=t0 + 2100) == 0

    asyncio.run(run())


def test_evaluate_measures_result_one_day_later():
    # Karten werden erst später verschickt/umgewandelt: das Ergebnis zählt, was 24 Std. später raus ist
    h = 3600
    snaps = [(1, 0, 100.0, 200, 0, 1000, "A"), (1, 10 * h, 100.0, 100, 60_000, 1000, "A"),
             (1, 24 * h, None, 100, 90_000, 1000, "A"), (1, 34 * h, None, 100, 100_000, 1000, "A")]
    item = evaluate({1: snaps})["items"][0]
    assert item["sold"] == 100 and item["predicted"] == 100.0
    assert item["realized"] == 100.0           # ohne Versatz wären es nur 60 %
    assert evaluate({1: snaps}, lag=0)["items"][0]["realized"] == 100.0   # Gesamtzeitraum: Archiv mit dabei
    assert evaluate({1: snaps[:2]})["count"] == 0                          # noch keine 24 Std. Abstand
