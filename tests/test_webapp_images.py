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
