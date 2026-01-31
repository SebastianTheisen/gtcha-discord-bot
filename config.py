"""
GTCHA Discord Bot - Konfiguration
"""

import os
from pathlib import Path
from dotenv import load_dotenv
from dataclasses import dataclass
from typing import Dict

load_dotenv()


@dataclass
class Config:
    discord_token: str
    guild_id: int
    channel_ids: Dict[str, int]
    scrape_interval_minutes: int
    base_url: str
    database_path: Path
    log_level: str

    @classmethod
    def from_env(cls) -> "Config":
        discord_token = os.getenv("DISCORD_TOKEN")
        if not discord_token:
            raise ValueError("DISCORD_TOKEN ist nicht gesetzt!")

        guild_id = os.getenv("GUILD_ID")
        if not guild_id:
            raise ValueError("GUILD_ID ist nicht gesetzt!")

        channel_ids = {
            "Bonus": int(os.getenv("CHANNEL_BONUS") or "0"),
            "MIX": int(os.getenv("CHANNEL_MIX") or "0"),
            "Yu-Gi-Oh!": int(os.getenv("CHANNEL_YUGIOH") or "0"),
            "Pokémon": int(os.getenv("CHANNEL_POKEMON") or "0"),
            "Weiss Schwarz": int(os.getenv("CHANNEL_WEISS_SCHWARZ") or "0"),
            "One piece": int(os.getenv("CHANNEL_ONE_PIECE") or "0"),
            "Hobby": int(os.getenv("CHANNEL_HOBBY") or "0"),
        }

        return cls(
            discord_token=discord_token,
            guild_id=int(guild_id),
            channel_ids=channel_ids,
            scrape_interval_minutes=int(os.getenv("SCRAPE_INTERVAL_MINUTES") or "5"),
            base_url=os.getenv("BASE_URL", "https://gtchaxonline.com"),
            database_path=Path(os.getenv("DATABASE_PATH", "data/gtcha_bot.db")),
            log_level=os.getenv("LOG_LEVEL", "INFO"),
        )

    def get_channel_id(self, category: str) -> int:
        return self.channel_ids.get(category, 0)


try:
    config = Config.from_env()
except ValueError as e:
    print(f"Konfigurationsfehler: {e}")
    config = None

# Direkte Exporte fuer einfachen Import
CATEGORIES = ["Bonus", "MIX", "Yu-Gi-Oh!", "Pokémon", "Weiss Schwarz", "One piece", "Hobby"]
MEDAL_EMOJIS = {"T1": "🥇", "T2": "🥈", "T3": "🥉"}

# Kompatibilitaets-Exporte
DISCORD_TOKEN = os.getenv("DISCORD_TOKEN")
GUILD_ID = os.getenv("GUILD_ID")
BASE_URL = os.getenv("BASE_URL", "https://gtchaxonline.com")
SCRAPE_INTERVAL_MINUTES = int(os.getenv("SCRAPE_INTERVAL_MINUTES") or "5")
DATABASE_PATH = os.getenv("DATABASE_PATH", "data/gtcha_bot.db")

CHANNEL_IDS = {
    "Bonus": int(os.getenv("CHANNEL_BONUS") or "0"),
    "MIX": int(os.getenv("CHANNEL_MIX") or "0"),
    "Yu-Gi-Oh!": int(os.getenv("CHANNEL_YUGIOH") or "0"),
    "Pokémon": int(os.getenv("CHANNEL_POKEMON") or "0"),
    "Weiss Schwarz": int(os.getenv("CHANNEL_WEISS_SCHWARZ") or "0"),
    "One piece": int(os.getenv("CHANNEL_ONE_PIECE") or "0"),
    "Hobby": int(os.getenv("CHANNEL_HOBBY") or "0"),
}

# Admin-Channel für Bot-Benachrichtigungen (optional)
# Der Bot postet hier Status-Updates, Fehler und Erfolge
ADMIN_CHANNEL_ID = int(os.getenv("ADMIN_CHANNEL_ID") or "0")

# Memory-Monitor Schwellwerte (in MB)
# Für kleine Server können diese Werte reduziert werden
MEMORY_WARNING_MB = int(os.getenv("MEMORY_WARNING_MB") or "300")
MEMORY_CRITICAL_MB = int(os.getenv("MEMORY_CRITICAL_MB") or "500")

# Paralleles Scrapen aktivieren (kann mehr RAM verbrauchen)
PARALLEL_SCRAPING = os.getenv("PARALLEL_SCRAPING", "false").lower() == "true"

# Scraper-Timeout in Sekunden (default: 180 = 3 Minuten)
SCRAPE_TIMEOUT_SECONDS = int(os.getenv("SCRAPE_TIMEOUT_SECONDS") or "180")

# @everyone Mentions bei neuen Threads und Updates
# MENTION_ON_NEW_THREAD: @everyone wenn neuer Banner-Thread erstellt wird
# MENTION_ON_PACK_UPDATE: @everyone wenn Pack-Update gepostet wird
MENTION_ON_NEW_THREAD = os.getenv("MENTION_ON_NEW_THREAD", "true").lower() == "true"
MENTION_ON_PACK_UPDATE = os.getenv("MENTION_ON_PACK_UPDATE", "true").lower() == "true"

# Hot-Banner Channel (Forum) - Top 10 Banner mit höchster Hit-Chance
# Wird alle 30 Minuten aktualisiert, exkludiert nur Bonus-Kategorie
# Unbegrenzte Pulls werden mit einfacher Wahrscheinlichkeit (hits/packs) berechnet
HOT_BANNER_CHANNEL_ID = int(os.getenv("HOT_BANNER_CHANNEL_ID") or "0")
# Hot-Banner Feature aktivieren/deaktivieren (true/false)
HOT_BANNER_ENABLED = os.getenv("HOT_BANNER_ENABLED", "false").lower() == "true"

# Discord-Benachrichtigungen: nur Fehler melden (true = nur Fehler, false = alles)
DISCORD_NOTIFY_ERRORS_ONLY = os.getenv("DISCORD_NOTIFY_ERRORS_ONLY", "false").lower() == "true"

# Täglicher Auto-Restart (Railway) - Uhrzeit im Format "HH:MM" (UTC), leer = deaktiviert
DAILY_RESTART_TIME = os.getenv("DAILY_RESTART_TIME", "")

