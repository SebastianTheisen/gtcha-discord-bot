"""Bild-Zwischenspeicher: Bilder von GTCHA einmal auf den VPS laden und von dort ausliefern.

Die Seite liefert Bilder aus Japan langsam (~2 s pro Bild). Der VPS lädt jedes Bild nur einmal,
legt es in data/img_cache ab und schickt es mit langer Cache-Dauer ans iPhone.
Alle Bilder aktiver Banner werden vorgeladen, Bilder beendeter Banner wieder gelöscht.
Nur Adressen von gtchaxonline.com werden geladen - kein offener Proxy.
"""

import asyncio
import hashlib
import os
import time
from pathlib import Path
from typing import Optional
from urllib.parse import urlparse

import aiohttp
from loguru import logger

ALLOWED_HOST = "gtchaxonline.com"
MAX_BYTES = 8 * 1024 * 1024
KEEP_UNUSED_SECONDS = 600   # gerade angesehene Bilder nicht verwaister Banner kurz behalten
TIMEOUT = aiohttp.ClientTimeout(total=30)
# Bildtyp selbst festlegen: im schlanken Container kennt Python ".webp" nicht (keine /etc/mime.types),
# und mit "nosniff" zeigt Safari Dateien ohne Bildtyp nicht an
CONTENT_TYPES = {".webp": "image/webp", ".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg",
                 ".gif": "image/gif", ".avif": "image/avif"}
EXTENSIONS = set(CONTENT_TYPES)


def allowed(url: str) -> bool:
    try:
        parts = urlparse(url)
    except ValueError:
        return False
    host = (parts.hostname or "").lower()
    return parts.scheme == "https" and (host == ALLOWED_HOST or host.endswith("." + ALLOWED_HOST))


def _looks_like_image(data: bytes) -> bool:
    """Bild an den ersten Bytes erkennen (falls der Server keinen Bild-Typ mitschickt)."""
    return (data[:4] == b"RIFF" and data[8:12] == b"WEBP") or data[:8] == b"\x89PNG\r\n\x1a\n" \
        or data[:3] == b"\xff\xd8\xff" or data[:6] in (b"GIF87a", b"GIF89a") or data[4:12] == b"ftypavif"


def content_type(path: Path) -> str:
    """Bildtyp einer gespeicherten Datei (Endung, sonst erste Bytes)."""
    if path.suffix in CONTENT_TYPES:
        return CONTENT_TYPES[path.suffix]
    head = path.read_bytes()[:16]
    for ext, magic in ((".webp", b"WEBP"), (".png", b"PNG"), (".gif", b"GIF"), (".avif", b"avif")):
        if magic in head:
            return CONTENT_TYPES[ext]
    return "image/jpeg"


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
        self.last_error: Optional[str] = None

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
                    ctype = resp.headers.get("Content-Type", "")
                    if resp.status != 200:
                        self.last_error = f"HTTP {resp.status} ({ctype or 'ohne Typ'}) bei {url}"
                        return None
                    data = await resp.content.read(MAX_BYTES + 1)
            except Exception as e:
                self.last_error = f"{type(e).__name__}: {e} bei {url}"
                return None
        if not ctype.startswith("image/") and not _looks_like_image(data):
            self.last_error = f"kein Bild ({ctype or 'ohne Typ'}, {len(data)} Bytes) bei {url}"
            return None
        if len(data) > MAX_BYTES:
            return None
        path = self.dir / cache_name(url)
        tmp = path.with_suffix(path.suffix + ".tmp")
        tmp.write_bytes(data)
        tmp.replace(path)
        return path

    async def warm(self, urls) -> int:
        """Lädt alle fehlenden Bilder vorab (höchstens 4 gleichzeitig, siehe _limit)."""
        missing = list(dict.fromkeys(u for u in urls if u and allowed(u) and not self.cached(u)))
        if not missing:
            return 0
        logger.info(f"Bilder: lade {len(missing)} fehlende vor...")
        loaded = 0
        for i in range(0, len(missing), 20):
            results = await asyncio.gather(*(self.get(u) for u in missing[i:i + 20]))
            loaded += sum(1 for r in results if r)
        if loaded < len(missing):
            logger.warning(f"Bilder: {loaded} von {len(missing)} geladen, {len(missing) - loaded} fehlgeschlagen "
                           f"- zuletzt: {self.last_error}")
        else:
            logger.info(f"Bilder: {loaded} von {len(missing)} geladen")
        return loaded

    def cleanup(self, keep_urls) -> int:
        """Löscht Bilder, die zu keinem aktiven Banner mehr gehören."""
        keep = {cache_name(u) for u in keep_urls if u}
        keep |= {name.rsplit(".", 1)[0] for name in keep}   # Stamm -> verkleinerte Kopien "<stamm>.w640.webp"
        now = time.time()
        removed = 0
        for path in self.dir.iterdir():
            if not path.is_file() or path.name in keep or path.name.split(".w", 1)[0] in keep:
                continue
            if now - path.stat().st_mtime < KEEP_UNUSED_SECONDS:
                continue
            path.unlink(missing_ok=True)
            removed += 1
        if removed:
            logger.info(f"Bilder: {removed} von beendeten Bannern gelöscht")
        return removed

    def resized(self, path: Path, width: int) -> Path:
        """Verkleinerte Kopie (Breite in Pixeln, WebP) neben dem Original; einmal erzeugt, dann gespeichert.

        GTCHA-Bilder sind teils sehr groß oder animiert - das iPhone muss sie sonst in voller Größe
        entpacken, auch wenn sie nur klein angezeigt werden (dauerte ~1 s pro Bild).
        """
        out = path.with_name(f"{path.stem}.w{width}.webp")
        if out.exists():
            return out
        from PIL import Image
        with Image.open(path) as im:
            im.seek(0)                                   # bei GIF/animiertem WebP: erstes Bild
            im = im.convert("RGBA" if im.mode in ("RGBA", "LA", "P") else "RGB")
            if im.width > width:
                im = im.resize((width, max(1, round(im.height * width / im.width))), Image.LANCZOS)
            tmp = out.with_suffix(".tmp")
            im.save(tmp, "WEBP", quality=82, method=4)
            tmp.replace(out)
        return out

    def stats(self) -> tuple:
        files = [p for p in self.dir.iterdir() if p.is_file()]
        return len(files), sum(p.stat().st_size for p in files)
