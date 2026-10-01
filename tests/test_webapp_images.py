from webapp.images import allowed, cache_name


def test_only_gtcha_images_are_proxied():
    assert allowed("https://gtchaxonline.com/pack/24014/1.webp")
    assert allowed("https://cdn.gtchaxonline.com/card/1.png")
    assert not allowed("http://gtchaxonline.com/pack/1.webp")
    assert not allowed("https://gtchaxonline.com.evil.example/x.webp")
    assert not allowed("https://evil.example/?gtchaxonline.com")
    assert not allowed("https://127.0.0.1/x.png")
    assert not allowed("file:///etc/passwd")


def test_cache_name_is_stable_and_keeps_extension():
    a = cache_name("https://gtchaxonline.com/pack/24014/1.webp")
    assert a == cache_name("https://gtchaxonline.com/pack/24014/1.webp") and a.endswith(".webp")
    assert cache_name("https://gtchaxonline.com/x?y=../../etc").endswith(".img")
    assert "/" not in cache_name("https://gtchaxonline.com/a/b/c.png")


def test_cleanup_keeps_active_and_recent_images(tmp_path):
    import os
    import time
    from webapp.images import ImageCache
    cache = ImageCache(str(tmp_path))
    active = "https://gtchaxonline.com/card/1.webp"
    ended = "https://gtchaxonline.com/card/2.webp"
    just_viewed = "https://gtchaxonline.com/card/3.webp"
    old = time.time() - 3600
    for url in (active, ended, just_viewed):
        (cache.dir / cache_name(url)).write_bytes(b"x")
    for url in (active, ended):
        os.utime(cache.dir / cache_name(url), (old, old))
    assert cache.cleanup([active]) == 1
    assert cache.cached(active) and cache.cached(just_viewed) and not cache.cached(ended)
