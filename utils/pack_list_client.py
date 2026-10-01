"""Schneller Abruf von pack/list ohne Browser: curl über den Tor-SOCKS-Proxy mit Sitzungs-Cookie.

Die API antwortet nur mit Daten, wenn die Sitzung der Seite (Cookie) mitgeschickt wird, und sie
liefert pack/list pro Sitzung zwischengespeichert: mit einer alten Sitzung kommen veraltete
Pack-Zahlen. Deshalb wird für jeden Abruf eine neue Sitzung geholt (über die kleine Adresse
api/user/point, notfalls über die Startseite).
"""

import asyncio
import json
import os
import tempfile
import time
from typing import Dict, Optional

from loguru import logger

USER_AGENT = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
              "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36")


class PackListClient:
    def __init__(self, base_url: str, proxy: Optional[str]):
        self.base_url = base_url.rstrip("/")
        # socks5h: Namensauflösung ebenfalls über Tor
        self.proxy = proxy.replace("socks5://", "socks5h://") if proxy else None
        self.cookie_file = os.path.join(tempfile.gettempdir(), "gtcha_pack_list_cookies.txt")

    async def _curl(self, url: str, extra: list, timeout: int = 20) -> str:
        cmd = ["curl", "-sS", "--max-time", str(timeout), "-A", USER_AGENT,
               "-H", "Accept-Language: de-DE,de;q=0.9", "-b", self.cookie_file, "-c", self.cookie_file]
        if self.proxy:
            cmd += ["--proxy", self.proxy]
        proc = await asyncio.create_subprocess_exec(
            *cmd, *extra, url, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=timeout + 5)
        if proc.returncode != 0:
            raise RuntimeError(stderr.decode(errors="replace").strip()[:150] or f"curl {proc.returncode}")
        return stdout.decode(errors="replace")

    async def _new_session(self, full_page: bool = False):
        try:
            os.remove(self.cookie_file)
        except FileNotFoundError:
            pass
        url = self.base_url + "/" if full_page else f"{self.base_url}/api/user/point"
        await self._curl(url, ["-o", os.devnull])

    async def fetch(self) -> Dict[int, dict]:
        """Alle Banner aus pack/list (leer bei Fehler)."""
        for attempt in (1, 2):
            await self._new_session(full_page=attempt == 2)
            body = await self._curl(f"{self.base_url}/api/user/pack/list?_={int(time.time())}",
                                    ["-H", "Accept: application/json"])
            try:
                items = json.loads(body).get("list") or []
            except (ValueError, AttributeError):
                items = []
            if items:
                return {int(it["id"]): it for it in items if it.get("id")}
            logger.debug(f"[SCHNELL] pack/list leer (Versuch {attempt})")
        return {}
