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
