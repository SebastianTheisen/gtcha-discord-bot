#!/usr/bin/env python3
import sys
import asyncio
from pathlib import Path
from loguru import logger

logger.remove()
logger.add(
    sys.stdout,
    format="<green>{time:HH:mm:ss}</green> | <level>{level: <8}</level> | <level>{message}</level>",
    level="INFO"
)
logger.add(
    "logs/bot_{time:YYYY-MM-DD}.log",
    rotation="1 day",
    retention="7 days",
    level="DEBUG"
)


def check_config():
    from config import config
    if config is None:
        logger.error(".env Datei fehlt! Kopiere .env.example zu .env")
        return False
    return True


async def main():
    logger.info("=" * 40)
    logger.info("GTCHA Discord Bot startet...")
    logger.info("=" * 40)

    if not check_config():
        sys.exit(1)

    from config import config
    from bot import GTCHABot

    Path("logs").mkdir(exist_ok=True)
    bot = GTCHABot()

    try:
        await bot.start(config.discord_token)
    except KeyboardInterrupt:
        logger.info("Beendet...")
    finally:
        if not bot.is_closed():
            await bot.close()


if __name__ == "__main__":
    if sys.platform == "win32":
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
    asyncio.run(main())
