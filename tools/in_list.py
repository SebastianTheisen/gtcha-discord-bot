"""Steht ein Banner noch in pack/list (der Liste, aus der der Bot alle Zahlen holt)? Zeigt seine Rohwerte.

Damit lässt sich prüfen, ob die Seite nach dem Ausverkauf weiter Versand-/Umwandlungszahlen liefert.
Liest nur die öffentliche Liste, wie der Bot.

Aufruf auf dem VPS:
    docker exec -i gtcha-discord-bot python - 24152 < tools/in_list.py
"""

import asyncio
import sys

sys.path.insert(0, "/app")
from config import BASE_URL, SCRAPER_PROXY  # noqa: E402
from utils.card_pool import TAX_FACTOR, fmt_coins  # noqa: E402
from utils.pack_list_client import PackListClient  # noqa: E402

FIELDS = ("pack_count", "total_pack_count", "total_sendcount", "total_sendprice", "total_kangen",
          "total_sendpeople", "point", "end_date")


async def main():
    ids = [int(a) for a in sys.argv[1:]] or [24152]
    client = PackListClient(BASE_URL, SCRAPER_PROXY, fresh_browser=True)
    items = {}
    for attempt in range(3):
        items = await client.fetch()
        if items:
            break
    print(f"pack/list: {len(items)} Banner insgesamt, davon mit 0 Packs: "
          f"{sum(1 for i in items.values() if not int(float(i.get('pack_count') or 0)))}")
    for pid in ids:
        item = items.get(pid)
        if not item:
            print(f"\n{pid}: NICHT mehr in der Liste")
            continue
        print(f"\n{pid}: steht noch in der Liste")
        for f in FIELDS:
            print(f"  {f}: {item.get(f)}")
        try:
            sent, conv = float(item.get("total_sendprice") or 0), float(item.get("total_kangen") or 0)
            print(f"  -> verschickt (Kartenwert) {fmt_coins(round(sent * TAX_FACTOR))} + umgewandelt "
                  f"{fmt_coins(round(conv))} = {fmt_coins(round(sent * TAX_FACTOR + conv))}")
        except (TypeError, ValueError):
            pass
    close = getattr(client, "close", None)
    if close:
        await close()


asyncio.run(main())
