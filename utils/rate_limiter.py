"""
Rate Limiter für Discord API Aufrufe
"""

import asyncio
from datetime import datetime, timedelta
from typing import Dict
from loguru import logger


class RateLimiter:
    """
    Einfacher Rate Limiter für Discord API Aufrufe.
    Verhindert zu viele Requests in kurzer Zeit.
    """

    def __init__(self, requests_per_second: float = 2.0):
        """
        Args:
            requests_per_second: Maximale Anfragen pro Sekunde (default: 2)
        """
        self.min_interval = 1.0 / requests_per_second
        self._last_request: Dict[str, datetime] = {}
        self._lock = asyncio.Lock()

    async def acquire(self, bucket: str = "default"):
        """
        Wartet falls nötig um Rate-Limit einzuhalten.

        Args:
            bucket: Kategorie für das Rate-Limit (z.B. "thread_create", "message_send")
        """
        async with self._lock:
            now = datetime.now()
            last = self._last_request.get(bucket)

            if last:
                elapsed = (now - last).total_seconds()
                if elapsed < self.min_interval:
                    wait_time = self.min_interval - elapsed
                    logger.debug(f"Rate-Limit: Warte {wait_time:.2f}s für {bucket}")
                    await asyncio.sleep(wait_time)

            self._last_request[bucket] = datetime.now()


# Globale Instanz für Discord API Calls
discord_rate_limiter = RateLimiter(requests_per_second=2.0)


async def rate_limited_call(coro, bucket: str = "default"):
    """
    Führt einen Coroutine mit Rate-Limiting aus.

    Beispiel:
        await rate_limited_call(thread.send("Hello"), "message_send")
    """
    await discord_rate_limiter.acquire(bucket)
    return await coro
