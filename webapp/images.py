"""Bild-Zwischenspeicher: Bilder von GTCHA einmal auf den VPS laden und von dort ausliefern.

Die Seite liefert Bilder aus Japan langsam (~2 s pro Bild). Der VPS lädt jedes Bild nur einmal,
legt es in data/img_cache ab und schickt es mit langer Cache-Dauer ans iPhone.
Nur Adressen von gtchaxonline.com werden geladen - kein offener Proxy.
"""

import asyncio
import hashlib
import os
from pathlib import Path
from typing import Optional
from urllib.parse import urlparse

import aiohttp
from loguru import logger

ALLOWED_HOST = "gtchaxonline.com"
MAX_BYTES = 8 * 1024 * 1024
MAX_CACHE_BYTES = 500 * 1024 * 1024
TIMEOUT = aiohttp.ClientTimeout(total=30)
EXTENSIONS = {".webp", ".png", ".jpg", ".jpeg", ".gif", ".avif"}


def allowed(url: str) -> bool:
    try:
        parts = urlparse(url)
    except ValueError:
        return False
    host = (parts.hostname or "").lower()
    return parts.scheme == "https" and (host == ALLOWED_HOST or host.endswith("." + ALLOWED_HOST))


def cache_name(url: str) -> str:
    ext = os.path.splitext(urlparse(url).path)[1].lower()
    return hashlib.sha256(url.encode()).hexdigest()[:32] + (ext if ext in EXTENSIONS else ".img")


class ImageCache:
    def __init__(self, data_dir: str):
        self.dir = Path(data_dir) / "img_cache"
        self.dir.mkdir(parents=True, exist_ok=True)
        self._locks: dict = {}
        self._limit = asyncio.Semaphore(4)
        self._session: Optional[aiohttp.ClientSession] = None

    async def close(self):
        if self._session:
            await self._session.close()

    def cached(self, url: str) -> Optional[Path]:
        path = self.dir / cache_name(url)
        return path if path.exists() else None

    async def get(self, url: str) -> Optional[Path]:
        """Pfad zur lokalen Kopie; lädt das Bild beim ersten Mal von GTCHA."""
        if not allowed(url):
            return None
        path = self.cached(url)
        if path:
            return path
        lock = self._locks.setdefault(url, asyncio.Lock())
        async with lock:
            path = self.cached(url)
            if path:
                return path
            try:
                return await self._download(url)
            finally:
                self._locks.pop(url, None)

    async def _download(self, url: str) -> Optional[Path]:
        if self._session is None:
            self._session = aiohttp.ClientSession(timeout=TIMEOUT, headers={"User-Agent": "Mozilla/5.0"})
        async with self._limit:
            try:
                async with self._session.get(url) as resp:
                    if resp.status != 200 or not resp.headers.get("Content-Type", "").startswith("image/"):
                        logger.debug(f"Bild nicht geladen ({resp.status}): {url}")
                        return None
                    data = await resp.content.read(MAX_BYTES + 1)
            except Exception as e:
                logger.debug(f"Bild nicht geladen: {url}: {e}")
                return None
        if len(data) > MAX_BYTES:
            return None
        path = self.dir / cache_name(url)
        tmp = path.with_suffix(path.suffix + ".tmp")
        tmp.write_bytes(data)
        tmp.replace(path)
        return path

    async def warm(self, urls):
        """Lädt fehlende Bilder vorab (nacheinander, damit die Seite nicht belastet wird)."""
        loaded = 0
        for url in urls:
            if url and allowed(url) and not self.cached(url):
                if await self.get(url):
                    loaded += 1
        if loaded:
            logger.info(f"Bilder vorgeladen: {loaded}")
        self.prune()

    def prune(self):
        """Hält den Zwischenspeicher unter MAX_CACHE_BYTES (älteste Dateien zuerst weg)."""
        files = sorted((p for p in self.dir.iterdir() if p.is_file()), key=lambda p: p.stat().st_mtime)
        total = sum(p.stat().st_size for p in files)
        for p in files:
            if total <= MAX_CACHE_BYTES:
                break
            total -= p.stat().st_size
            p.unlink(missing_ok=True)
