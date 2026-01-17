from dataclasses import dataclass, field
from typing import Optional
from datetime import datetime


@dataclass
class ScrapedBanner:
    pack_id: int
    category: str
    title: Optional[str] = None
    price_coins: Optional[int] = None
    current_packs: Optional[int] = None
    total_packs: Optional[int] = None
    entries_per_day: Optional[int] = None
    sale_end_date: Optional[str] = None
    image_url: Optional[str] = None
    best_hit: Optional[str] = None
    detail_page_url: Optional[str] = None
    banner_screenshot: Optional[bytes] = field(default=None, repr=False)
    detail_screenshot: Optional[bytes] = field(default=None, repr=False)
    scraped_at: datetime = field(default_factory=datetime.utcnow)

    def __post_init__(self):
        if isinstance(self.pack_id, str):
            self.pack_id = int(self.pack_id)
        if isinstance(self.price_coins, str):
            self.price_coins = int(self.price_coins.replace(".", "").replace(",", ""))

    @property
    def thread_title(self) -> str:
        return f"{self.price_coins or 0}c {self.best_hit or 'Unknown'} / x{self.entries_per_day or 0} / {self.total_packs or 0}"

    def to_dict(self) -> dict:
        return {
            "pack_id": self.pack_id,
            "category": self.category,
            "title": self.title,
            "best_hit": self.best_hit,
            "price_coins": self.price_coins,
            "entries_per_day": self.entries_per_day,
            "total_packs": self.total_packs,
            "current_packs": self.current_packs,
            "sale_end_date": self.sale_end_date,
            "image_url": self.image_url,
            "detail_page_url": self.detail_page_url,
        }
