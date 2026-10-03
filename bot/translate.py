"""Japanische Karten- und Pack-Namen auf Deutsch (siehe utils/translate.py).

Alle 5 Minuten: unbekannte Namensteile aller laufenden Banner und Store-Packs einmal über DeepL übersetzen,
dann die gespeicherten Kartenpools mit deutschen Namen überschreiben (Original bleibt als name_ja). Pack-Titel
bleiben in der Datenbank im Original (sie benennen Discord-Threads) - die App übersetzt sie beim Anzeigen.
"""

from bot.common import *  # noqa: F401,F403
from utils import translate


class TranslateMixin:
    async def _load_translations(self):
        translate.set_cache(await self.db.get_translations())

    async def _translate_names(self):
        if getattr(self, "_translate_running", False):
            return
        self._translate_running = True
        try:
            await self._load_translations()
            rows = {**await self.db.get_active_banners(), **await self.db.get_store_banners()}
            pools = {pid: json.loads(r['card_pool']) for pid, r in rows.items() if r.get('card_pool')}
            texts = [t for r in rows.values() for t in (r.get('title'), r.get('best_hit'))]
            texts += [t for pool in pools.values() for t in translate.pool_texts(pool)]
            todo = translate.missing(texts)
            key = os.getenv("DEEPL_API_KEY", "").strip()
            if todo:
                # DeepL nur mit Key in der .env; sonst MyMemory (kostenlos, ohne Anmeldung)
                found = await (translate.deepl(todo, key) if key else translate.mymemory(todo))
                if found:
                    await self.db.save_translations(found)
                    await self._load_translations()
                    logger.info(f"[ÜBERSETZUNG] {len(found)} neue Namensteile übersetzt "
                                f"({'DeepL' if key else 'MyMemory'}), offen {len(todo) - len(found)}")
            for pid, pool in pools.items():
                pool, changed = translate.translate_pool(pool)
                if changed:
                    await self.db.replace_pool_names(pid, rows[pid]['card_pool'], pool)
        except Exception as e:
            logger.warning(f"[ÜBERSETZUNG] fehlgeschlagen: {type(e).__name__}: {e}")
        finally:
            self._translate_running = False
