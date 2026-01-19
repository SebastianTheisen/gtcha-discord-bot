"""
Discord Webhook Utility für Fehler-Benachrichtigungen
"""

import aiohttp
from datetime import datetime
from typing import Optional
from loguru import logger

from config import ERROR_WEBHOOK_URL


async def send_error_notification(
    title: str,
    description: str,
    color: int = 0xFF0000,  # Rot für Fehler
    fields: Optional[list] = None
) -> bool:
    """
    Sendet eine Fehler-Benachrichtigung über Discord Webhook.

    Args:
        title: Titel der Nachricht
        description: Beschreibung des Fehlers
        color: Embed-Farbe (default: rot)
        fields: Optionale zusätzliche Felder [{name, value, inline}]

    Returns:
        True bei Erfolg, False bei Fehler oder wenn kein Webhook konfiguriert
    """
    if not ERROR_WEBHOOK_URL:
        logger.debug("Kein ERROR_WEBHOOK_URL konfiguriert - überspringe Benachrichtigung")
        return False

    embed = {
        "title": title,
        "description": description,
        "color": color,
        "timestamp": datetime.utcnow().isoformat(),
        "footer": {
            "text": "GTCHA Bot Alert"
        }
    }

    if fields:
        embed["fields"] = fields

    payload = {
        "embeds": [embed]
    }

    try:
        async with aiohttp.ClientSession() as session:
            async with session.post(
                ERROR_WEBHOOK_URL,
                json=payload,
                timeout=aiohttp.ClientTimeout(total=10)
            ) as response:
                if response.status in (200, 204):
                    logger.debug(f"Webhook-Benachrichtigung gesendet: {title}")
                    return True
                else:
                    logger.warning(f"Webhook-Fehler: Status {response.status}")
                    return False
    except Exception as e:
        logger.warning(f"Webhook-Fehler: {e}")
        return False


async def notify_scrape_error(error_type: str, details: str, attempt: int = 0, max_attempts: int = 0):
    """Benachrichtigt über Scrape-Fehler."""
    fields = []
    if max_attempts > 0:
        fields.append({
            "name": "Versuch",
            "value": f"{attempt + 1}/{max_attempts + 1}",
            "inline": True
        })

    await send_error_notification(
        title=f"Scrape-Fehler: {error_type}",
        description=details,
        fields=fields if fields else None
    )


async def notify_critical_error(error_message: str):
    """Benachrichtigt über kritische Fehler."""
    await send_error_notification(
        title="Kritischer Fehler",
        description=error_message,
        color=0x8B0000  # Dunkelrot
    )


async def notify_low_banner_count(found: int, expected_min: int):
    """Benachrichtigt wenn zu wenige Banner gefunden wurden."""
    await send_error_notification(
        title="Warnung: Wenige Banner gefunden",
        description=f"Nur **{found}** Banner gefunden (Minimum: {expected_min}).\n"
                    f"Not-Found-Tracking wurde übersprungen.",
        color=0xFFA500,  # Orange für Warnung
        fields=[
            {"name": "Gefunden", "value": str(found), "inline": True},
            {"name": "Minimum", "value": str(expected_min), "inline": True}
        ]
    )


async def notify_all_retries_failed():
    """Benachrichtigt wenn alle Retries fehlgeschlagen sind."""
    await send_error_notification(
        title="Alle Scrape-Versuche fehlgeschlagen",
        description="Der Scrape-Job konnte nach mehreren Versuchen nicht erfolgreich "
                    "abgeschlossen werden. Bitte System überprüfen!",
        color=0x8B0000  # Dunkelrot
    )
