"""
GTCHA Discord Bot - Haupteinstiegspunkt
"""

import os
import sys
from loguru import logger

# Loguru konfigurieren BEVOR andere Imports
log_level = os.getenv("LOG_LEVEL", "INFO").upper()

# Entferne default handler
logger.remove()

# Neuen Handler mit korrektem Level
logger.add(
    sys.stderr,
    level=log_level,
    format="<level>{time:HH:mm:ss}</level> | <level>{level: <7}</level> | <level>{message}</level>",
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
