"""
Simple Cache für Banner-Daten
"""

import asyncio
from datetime import datetime, timedelta
from typing import Dict, Optional, Any
from loguru import logger


class BannerCache:
    """
    Einfacher In-Memory Cache für Banner-Daten.
    Verhindert redundante Verarbeitung von unveränderten Bannern.
    """

    def __init__(self, ttl_seconds: int = 300):
        """
        Args:
            ttl_seconds: Time-to-Live für Cache-Einträge in Sekunden (default: 5 Minuten)
        """
        self._cache: Dict[int, Dict[str, Any]] = {}
        self._ttl = timedelta(seconds=ttl_seconds)
        self._lock = asyncio.Lock()

    async def get(self, pack_id: int) -> Optional[Dict]:
        """
        Holt einen Banner aus dem Cache.

        Args:
            pack_id: Banner-ID

        Returns:
            Cached Banner-Daten oder None wenn nicht im Cache/abgelaufen
        """
        async with self._lock:
            entry = self._cache.get(pack_id)
            if not entry:
                return None

            # Prüfe ob abgelaufen
            if datetime.now() > entry['expires']:
                del self._cache[pack_id]
                return None

            return entry['data']

    async def set(self, pack_id: int, data: Dict):
        """
        Speichert Banner-Daten im Cache.

        Args:
            pack_id: Banner-ID
            data: Banner-Daten
        """
        async with self._lock:
            self._cache[pack_id] = {
                'data': data,
                'expires': datetime.now() + self._ttl,
                'created': datetime.now()
            }

    async def has_changed(self, pack_id: int, new_data: Dict, compare_fields: list = None) -> bool:
        """
        Prüft ob sich Banner-Daten geändert haben.

        Args:
            pack_id: Banner-ID
            new_data: Neue Banner-Daten
            compare_fields: Felder die verglichen werden sollen (default: alle)

        Returns:
            True wenn Daten sich geändert haben oder nicht im Cache
        """
        cached = await self.get(pack_id)
        if not cached:
            return True

        if compare_fields is None:
            compare_fields = ['current_packs', 'price_coins', 'entries_per_day', 'total_packs']

        for field in compare_fields:
            old_val = cached.get(field)
            new_val = new_data.get(field)
            if old_val != new_val:
                logger.debug(f"Banner {pack_id} geändert: {field} {old_val} -> {new_val}")
                return True

        return False

    async def invalidate(self, pack_id: int):
        """Entfernt einen Banner aus dem Cache."""
        async with self._lock:
            if pack_id in self._cache:
                del self._cache[pack_id]

    async def clear(self):
        """Leert den gesamten Cache."""
        async with self._lock:
            self._cache.clear()
            logger.debug("Cache geleert")

    async def cleanup_expired(self):
        """Entfernt abgelaufene Einträge aus dem Cache."""
        async with self._lock:
            now = datetime.now()
            expired = [k for k, v in self._cache.items() if now > v['expires']]
            for k in expired:
                del self._cache[k]
            if expired:
                logger.debug(f"Cache: {len(expired)} abgelaufene Einträge entfernt")

    def size(self) -> int:
        """Gibt die Anzahl der Cache-Einträge zurück."""
        return len(self._cache)


# Globale Instanz
banner_cache = BannerCache(ttl_seconds=300)  # 5 Minuten TTL
