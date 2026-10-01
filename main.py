"""
GTCHA Discord Bot - Haupteinstiegspunkt
"""

import os
import sys
from loguru import logger

from utils.banner_info import berlin_time

# Loguru konfigurieren BEVOR andere Imports
log_level = os.getenv("LOG_LEVEL", "INFO").upper()

# Entferne default handler
logger.remove()

# Container läuft in UTC (DB-Zeitstempel bleiben so einheitlich) - im Log deutsche Zeit zeigen
logger.configure(patcher=lambda record: record["extra"].update(
    berlin=f"{berlin_time(record['time'].timestamp()):%H:%M:%S}"))

# Neuen Handler mit korrektem Level
logger.add(
    sys.stderr,
    level=log_level,
    format="<level>{extra[berlin]}</level> | <level>{level: <7}</level> | <level>{message}</level>",
    colorize=True,
)

logger.info(f"Log-Level: {log_level}")

# Jetzt andere Imports
from bot.client import GTCHABot
from config import DISCORD_TOKEN


def main():
    logger.info("=" * 40)
    logger.info("GTCHA Discord Bot startet...")
    logger.info("=" * 40)

    if not DISCORD_TOKEN:
        logger.error("DISCORD_TOKEN nicht gesetzt!")
        sys.exit(1)

    bot = GTCHABot()
    bot.run(DISCORD_TOKEN)


if __name__ == "__main__":
    main()
