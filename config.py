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
            "Bonus": int(os.getenv("CHANNEL_BONUS", "0")),
            "MIX": int(os.getenv("CHANNEL_MIX", "0")),
            "Yu-Gi-Oh!": int(os.getenv("CHANNEL_YUGIOH", "0")),
            "Pokémon": int(os.getenv("CHANNEL_POKEMON", "0")),
            "Weiss Schwarz": int(os.getenv("CHANNEL_WEISS_SCHWARZ", "0")),
            "One piece": int(os.getenv("CHANNEL_ONE_PIECE", "0")),
            "Hobby": int(os.getenv("CHANNEL_HOBBY", "0")),
        }

        return cls(
            discord_token=discord_token,
            guild_id=int(guild_id),
            channel_ids=channel_ids,
            scrape_interval_minutes=int(os.getenv("SCRAPE_INTERVAL_MINUTES", "5")),
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
SCRAPE_INTERVAL_MINUTES = int(os.getenv("SCRAPE_INTERVAL_MINUTES", "5"))
DATABASE_PATH = os.getenv("DATABASE_PATH", "data/gtcha_bot.db")

CHANNEL_IDS = {
    "Bonus": int(os.getenv("CHANNEL_BONUS", "0")),
    "MIX": int(os.getenv("CHANNEL_MIX", "0")),
    "Yu-Gi-Oh!": int(os.getenv("CHANNEL_YUGIOH", "0")),
    "Pokémon": int(os.getenv("CHANNEL_POKEMON", "0")),
    "Weiss Schwarz": int(os.getenv("CHANNEL_WEISS_SCHWARZ", "0")),
    "One piece": int(os.getenv("CHANNEL_ONE_PIECE", "0")),
    "Hobby": int(os.getenv("CHANNEL_HOBBY", "0")),
}

# Admin-Channel für Bot-Benachrichtigungen (optional)
# Der Bot postet hier Status-Updates, Fehler und Erfolge
ADMIN_CHANNEL_ID = int(os.getenv("ADMIN_CHANNEL_ID", "0"))
