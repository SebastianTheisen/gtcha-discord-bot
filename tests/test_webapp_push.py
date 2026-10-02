from webapp.push import build_events, clean_prefs, recipients


def full(pid, remaining=100, out=(), ship=0, pct=50.0, status="running"):
    return {"id": pid, "title": f"Pack {pid}", "price": 1000, "remaining": remaining, "total": 500,
            "ev_pct": pct, "status": status, "ship_cards": ship, "ship_value": ship * 1000,
            "out": [{"name": n, "value": 5000} for n in out]}


def hot(*banners):
    return [{**b, "pct": b["ev_pct"]} for b in banners]


def test_first_run_only_remembers():
    b = full(1, pct=150)
    messages, state = build_events([b], hot(b), {})
    assert messages == []
    assert state["known"] == [1] and state["alerted"] == [1] and "1" in state["banners"]


def test_banner_events_for_every_banner():
    _, state = build_events([full(1), full(2)], [], {})
    now = [full(1, 90, out=["Lugia"], ship=2, pct=120, status="endspurt"), full(2, 50), full(3)]
    messages, state = build_events(now, [], state)
    by_banner = {}
    for m in messages:
        by_banner.setdefault(m[3], []).append(m[0])
    assert sorted(by_banner[1]) == ["ev", "hit", "low", "packs", "ship"]
    assert by_banner[2] == ["packs"] and by_banner[3] == ["new"]   # neuer Banner: nur "neu", sonst erst merken
    messages, _ = build_events([full(2, 40), full(3)], [], state)
    assert ("end", 1) in {(m[0], m[3]) for m in messages}


def test_prefs_defaults_and_watch_cleanup():
    prefs = clean_prefs({"value": False, "watch": {"24149": ["hit", "packs", "bogus"], "x": ["hit"]}})
    assert prefs["watch"] == {"24149": ["hit", "packs"]}
    assert prefs["value"] is False and prefs["new"] is True and prefs["packs"] is False


def test_recipients_general_watch_and_dedupe():
    messages = [("hit", "Hit 1", "", 1, True), ("hit", "Hit 2", "", 2, True),
                ("packs", "📉 Banner 1: 100 → 90 Packs", "", 1, True),
                ("packs", "📉 Banner 2: 50 → 49 Packs", "", 2, True),
                ("packs", "📉 Banner 3: 20 → 18 Packs", "", 3, True),
                ("ev", "über 100", "", 1, True), ("value", "lohnt 1", "", 1, False),
                ("ship", "Versand 2", "", 2, True), ("new", "neu 4", "", 4, False)]
    # nur beobachten: Banner 1 mit Hit + Packs + über 100 %; allgemein Hit an, Packs/Versand aus, value an
    prefs = clean_prefs({"hit": True, "value": True, "packs": False, "ship": False, "new": False,
                         "watch": {"1": ["hit", "packs", "ev"]}})
    got = [m[1] for m in recipients(messages, prefs)]
    assert got == ["Hit 1", "Hit 2", "📉 Banner 1: 100 → 90 Packs", "über 100"]   # value 1 nicht doppelt
    # allgemein Pack-Bewegung an: übrige Banner zusammengefasst in einem Push
    prefs = clean_prefs({"packs": True, "hit": False, "value": False, "new": False, "watch": {"1": ["packs"]}})
    got = recipients(messages, prefs)
    summary = [m for m in got if m[1].startswith("📉 Pack-Bewegung")]
    assert len(summary) == 1 and summary[0][2] == "2: 50 → 49 · 3: 20 → 18"
    assert [m[1] for m in got if m[3] == 1] == ["📉 Banner 1: 100 → 90 Packs"]


def test_push_inbox_per_device_and_mark_read(tmp_path, monkeypatch):
    import asyncio
    import sys
    import types

    from webapp.push import PushService

    sent = []
    fake = types.ModuleType("pywebpush")
    fake.WebPushException = type("WebPushException", (Exception,), {})
    fake.webpush = lambda **kw: sent.append(kw["data"])
    monkeypatch.setitem(sys.modules, "pywebpush", fake)

    async def run():
        push = PushService(str(tmp_path), "x")
        await push.init()
        for i in range(3):
            await push._push("A", {"endpoint": "A"}, f"Titel {i}", "Text", 24114 if i else None, "hit")
        await push._push("B", {"endpoint": "B"}, "Anderes Gerät", "", None, "new")
        box = await push.inbox("A", limit=2)
        assert [n["title"] for n in box["items"]] == ["Titel 2", "Titel 1"] and box["more"] and box["unread"] == 3
        older = await push.inbox("A", limit=2, before=box["items"][-1]["id"])
        assert [n["title"] for n in older["items"]] == ["Titel 0"] and not older["more"]
        await push.mark_read("A", [box["items"][0]["id"]])
        await push.mark_read("A", [ (await push.inbox("B"))["items"][0]["id"] ])   # fremde ID: keine Wirkung
        assert (await push.inbox("A"))["unread"] == 2 and (await push.inbox("B"))["unread"] == 1
        await push.mark_read("A")
        assert (await push.inbox("A"))["unread"] == 0 and (await push.inbox("B"))["unread"] == 1

    asyncio.run(run())
    payloads = [__import__("json").loads(d) for d in sent]
    assert payloads[1]["url"].startswith("/#/banner/24114?n=") and payloads[0]["url"].startswith("/#/inbox?n=")
    assert [p["unread"] for p in payloads[:3]] == [1, 2, 3]


def test_wish_events_new_banner_and_pulled():
    from webapp.push import wish_events

    def banner(bid, ids, out=()):
        return {"id": bid, "title": f"B{bid}", "price": 1000,
                "cards_brief": {i: [f"Karte {i}", 5000, None, 1] for i in ids}, "out_ids": list(out)}

    # nach dem Update: nur merken
    msgs, cards, outc = wish_events([banner(1, ["7"])], {"known": [1]}, first=False)
    assert msgs == [] and cards == {"1": ["7"]}
    state = {"cards": cards, "outc": outc}
    # neuer Banner (oder Pool erst jetzt geladen) mit Karte 7; in Banner 1 wurde Karte 7 gezogen
    msgs, cards, outc = wish_events([banner(1, ["7"], out=["7"]), banner(2, ["7", "8"])], state, first=False)
    kinds = sorted((m[0], m[3], m[4]) for m in msgs)
    assert kinds == [("wish_new", 2, "7"), ("wish_new", 2, "8"), ("wish_out", 1, "7")]
    # nur wer die Karte auf der Wunschliste hat, bekommt sie
    got = recipients(msgs, clean_prefs({"wish": ["8"], "new": False}))
    assert [(m[0], m[4]) for m in got] == [("wish_new", "8")]
    assert recipients(msgs, clean_prefs({})) == []


def test_card_search_groups_banners():
    from webapp.server import search_cards
    banners = [
        {"id": 1, "title": "A", "price": 1000, "remaining": 50, "ev_pct": 95.0, "status": "running",
         "cards_brief": {"7": ["Glurak ex", 9000, "img", 1], "8": ["Bisasam", 500, None, 3]}, "out_ids": ["7"]},
        {"id": 2, "title": "B", "price": 2000, "remaining": 9, "ev_pct": 110.0, "status": "endspurt",
         "cards_brief": {"7": ["Glurak ex", 9500, "img", 2]}, "out_ids": []},
    ]
    cards = search_cards(banners, "glurak", set())
    assert len(cards) == 1 and cards[0]["value"] == 9500
    assert [(b["id"], b["out"]) for b in cards[0]["banners"]] == [(2, False), (1, True)]   # noch drin zuerst
    assert [c["id"] for c in search_cards(banners, "", {"8"})] == ["8"]
    assert search_cards(banners, "xyz", set()) == []
