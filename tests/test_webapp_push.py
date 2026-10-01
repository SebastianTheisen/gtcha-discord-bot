from webapp.push import build_events


def banner(pid, pct=50.0, hits=(), detected=()):
    return {"id": pid, "title": f"Pack {pid}", "price": 1000, "remaining": 100, "ev_pct": pct,
            "hits": [{"key": k, "name": f"Karte {k}", "value": 5000} for k in hits],
            "hit_keys_detected": list(detected)}


def hot(*banners):
    return [{**b, "pct": b["ev_pct"]} for b in banners]


def test_first_run_only_remembers():
    b = banner(1, 150, hits=["a"], detected=["a"])
    messages, state = build_events([b], hot(b), {})
    assert messages == []
    assert state["known"] == [1] and state["alerted"] == [1] and state["detected"] == {"1": ["a"]}


def test_new_banner_value_and_hit_events():
    old = banner(1, 50, hits=["a", "b"])
    _, state = build_events([old], hot(old), {})
    now = banner(1, 120, hits=["a", "b"], detected=["b"])
    fresh = banner(2, 40)
    messages, state = build_events([now, fresh], hot(now, fresh), state)
    kinds = sorted(m[0] for m in messages)
    assert kinds == ["hit", "new", "value"]
    hit = next(m for m in messages if m[0] == "hit")
    assert "Karte b" in hit[2] and hit[3] == 1
    # nichts Neues -> keine Meldungen
    again, _ = build_events([now, fresh], hot(now, fresh), state)
    assert again == []


def full(pid, remaining=100, out=(), ship=0, pct=50.0, status="running"):
    return {"id": pid, "title": f"Pack {pid}", "price": 1000, "remaining": remaining, "total": 500,
            "ev_pct": pct, "status": status, "ship_cards": ship, "ship_value": ship * 1000,
            "out": [{"name": n, "value": 5000} for n in out], "hits": [], "hit_keys_detected": []}


def test_watched_banner_events():
    _, state = build_events([full(1), full(2)], [], {}, watched={1})
    now = [full(1, 90, out=["Lugia"], ship=2, pct=120, status="endspurt"), full(2, 50)]
    messages, state = build_events(now, [], state, watched={1})
    kinds = sorted(m[0] for m in messages if m[4])
    assert kinds == ["ev", "hit", "low", "packs", "ship"]
    assert all(m[3] == 1 for m in messages if m[4])          # Banner 2 wird nicht beobachtet
    # Banner verschwindet -> beendet
    messages, _ = build_events([full(2, 40)], [], state, watched={1})
    assert [m[0] for m in messages if m[4]] == ["end"]


def test_newly_watched_banner_starts_quietly():
    _, state = build_events([full(1)], [], {}, watched=set())
    messages, _ = build_events([full(1, 90)], [], state, watched={1})
    assert [m[0] for m in messages if m[4]] == ["packs"]     # Stand war schon da -> ab jetzt Meldungen
    _, state = build_events([], [], {"known": []}, watched=set())
    messages, _ = build_events([full(5, 90)], [], state, watched={5})
    assert not [m for m in messages if m[4]]                 # neuer Banner: erst merken


def test_prefs_and_recipients():
    from webapp.push import clean_prefs, recipients
    prefs = clean_prefs({"hit": True, "value": False, "watch": {"24149": ["hit", "packs", "bogus"], "x": ["hit"]}})
    assert prefs["watch"] == {"24149": ["hit", "packs"]} and prefs["new"] is True
    messages = [("hit", "allgemein", "", 24149, False), ("hit", "beobachtet", "", 24149, True),
                ("hit", "anderer", "", 1, False), ("value", "lohnt", "", 1, False),
                ("packs", "packs", "", 24149, True), ("ship", "versand", "", 24149, True)]
    got = [m[1] for m in recipients(messages, prefs)]
    assert got == ["beobachtet", "packs", "anderer"]           # kein Doppel, value aus, ship nicht gewählt
