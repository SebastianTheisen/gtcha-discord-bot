"""Bannerbild: GTCHA hängt bei jedem Abruf ?d=<Uhrzeit> an - das darf nicht als neues Bild zählen."""

import asyncio

import aiosqlite

from bot.scraping import api_image
from database.db import Database, strip_time_param
from scraper.models import ScrapedBanner

URL = "https://gtchaxonline.com/pack/24082/1.webp"


def test_strip_time_param():
    assert strip_time_param(URL + "?d=1791917511") == URL
    assert strip_time_param(URL + "?v=2&d=1791917511") == URL + "?v=2"
    assert strip_time_param(URL) == URL
    assert strip_time_param(None) is None
    assert api_image({"image": ["/pack/24082/1.webp?d=1791917572"]}).endswith("/pack/24082/1.webp")


def test_same_image_with_new_time_is_not_new(tmp_path):
    async def run():
        db = Database(str(tmp_path / "b.db"))
        await db.init()
        await db.save_banner(ScrapedBanner(pack_id=24082, category="One piece", title="x", price_coins=100,
                                           current_packs=5, total_packs=10))
        async with aiosqlite.connect(db.db_path) as conn:   # Altbestand: mit ?d=… gespeichert
            await conn.execute("UPDATE banners SET image_url = ? WHERE pack_id = 24082", (URL + "?d=1791916943",))
            await conn.commit()
        first = await db.set_image(24082, URL + "?d=1791917511")    # nur bereinigt, nicht neu
        stored = (await db.get_banner(24082))["image_url"]
        again = await db.set_image(24082, URL + "?d=1791917572")
        other = await db.set_image(24082, "https://gtchaxonline.com/pack/24082/2.webp?d=1")
        return first, stored, again, other

    first, stored, again, other = asyncio.run(run())
    assert (first, stored, again, other) == (False, URL, False, True)


def test_versioned_image_differs_per_banner_under_same_id():
    """GTCHA vergibt 24260 neu, das Bild liegt unter derselben Adresse: je Banner (Verkaufsstart) eigene Adresse."""
    from utils.banner_info import versioned_image
    old, new = versioned_image(URL, 1_790_000_000), versioned_image(URL, 1_791_900_000)
    assert old != new and old.startswith(URL + "?s=")
    assert versioned_image(URL + "?v=2", 5) == URL + "?v=2&s=5"
    assert versioned_image(URL, None) == URL and versioned_image(None, 5) is None
