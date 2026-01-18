"""
Datenmodelle fuer den Scraper.
"""

from dataclasses import dataclass
from typing import Optional


@dataclass
class ScrapedBanner:
    pack_id: int
    category: str
    title: Optional[str] = None
    best_hit: Optional[str] = None
    price_coins: Optional[int] = None
    current_packs: Optional[int] = None
    total_packs: Optional[int] = None
    entries_per_day: Optional[int] = None
    sale_end_date: Optional[str] = None
    image_url: Optional[str] = None
    detail_page_url: Optional[str] = None
    screenshot: Optional[bytes] = None
