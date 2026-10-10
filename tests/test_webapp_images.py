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


def test_image_detection_by_magic_bytes():
    from webapp.images import _looks_like_image
    assert _looks_like_image(b"RIFF\x00\x00\x00\x00WEBPVP8 ")
    assert _looks_like_image(b"\x89PNG\r\n\x1a\n....")
    assert _looks_like_image(b"\xff\xd8\xff\xe0")
    assert not _looks_like_image(b"<!DOCTYPE html><html>")


def test_content_type_is_set_without_system_mime_table(tmp_path):
    from webapp.images import content_type
    webp = tmp_path / "a.webp"
    webp.write_bytes(b"RIFF\x00\x00\x00\x00WEBPVP8 ")
    unknown = tmp_path / "b.img"
    unknown.write_bytes(b"\x89PNG\r\n\x1a\n")
    assert content_type(webp) == "image/webp"
    assert content_type(unknown) == "image/png"


def test_resized_copies_and_cleanup(tmp_path):
    import os
    import time
    from PIL import Image
    from webapp.images import ImageCache
    cache = ImageCache(str(tmp_path))
    big = "https://gtchaxonline.com/pack/1.png"
    anim = "https://gtchaxonline.com/pack/2.gif"
    Image.new("RGB", (2400, 1440), (200, 30, 30)).save(cache.dir / cache_name(big))
    frames = [Image.new("P", (1600, 900), i) for i in (1, 2)]
    frames[0].save(cache.dir / cache_name(anim), save_all=True, append_images=frames[1:])
    small = cache.resized(cache.cached(big), 640)
    with Image.open(small) as im:
        assert im.size == (640, 384) and im.format == "WEBP"
    assert cache.resized(cache.cached(big), 640) == small            # nur einmal erzeugt
    with Image.open(cache.resized(cache.cached(anim), 320)) as im:
        assert im.size == (320, 180)
    # Aufräumen: Kopien bleiben mit ihrem Original, verschwinden mit ihm
    old = time.time() - 3600
    for p in cache.dir.iterdir():
        os.utime(p, (old, old))
    cache.cleanup([big])
    names = sorted(p.name for p in cache.dir.iterdir())
    assert names == sorted([cache_name(big), small.name])


def _webp(size=(800, 480)) -> bytes:
    import io
    from PIL import Image
    buf = io.BytesIO()
    Image.effect_noise(size, 80).convert("RGB").save(buf, "WEBP", quality=95)   # Rauschen: groß, nicht komprimierbar
    return buf.getvalue()


def test_download_reads_all_chunks_and_rejects_truncated(tmp_path):
    """Bild kommt in vielen kleinen Paketen (wie von GTCHA): vollständig speichern, abgeschnittenes nie."""
    import asyncio

    from aiohttp import web
    from aiohttp.test_utils import TestServer

    from webapp.images import ImageCache, complete
    data = _webp()
    assert len(data) > 30000 and complete(data) and not complete(data[:7940])

    async def chunked(request):
        resp = web.StreamResponse(headers={"Content-Type": "image/webp"})
        await resp.prepare(request)
        for i in range(0, len(data), 4000):
            await resp.write(data[i:i + 4000])
            await asyncio.sleep(0.01)
        return resp

    async def cut(request):
        return web.Response(body=data[:7940], content_type="image/webp")

    async def run():
        app = web.Application()
        app.router.add_get("/full.webp", chunked)
        app.router.add_get("/cut.webp", cut)
        server = TestServer(app, port=0)
        await server.start_server()
        cache = ImageCache(str(tmp_path))
        try:
            path = await cache._download(str(server.make_url("/full.webp")))
            assert path and path.read_bytes() == data
            assert await cache._download(str(server.make_url("/cut.webp"))) is None
            assert "unvollständig" in cache.last_error
        finally:
            await cache.close()
            await server.close()


    asyncio.run(run())


def test_repair_removes_truncated_images_and_their_copies(tmp_path):
    from webapp.images import ImageCache
    cache = ImageCache(str(tmp_path))
    data = _webp()
    good, bad = "https://gtchaxonline.com/pack/1/1.webp", "https://gtchaxonline.com/pack/2/1.webp"
    (cache.dir / cache_name(good)).write_bytes(data)
    (cache.dir / cache_name(bad)).write_bytes(data[:7940])
    (cache.dir / (cache_name(bad).rsplit(".", 1)[0] + ".w640.webp")).write_bytes(b"x")
    assert cache.repair() == 1
    assert cache.cached(good) and not cache.cached(bad)
    assert not list(cache.dir.glob("*.w640.webp"))
