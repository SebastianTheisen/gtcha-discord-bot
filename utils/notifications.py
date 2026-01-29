"""
Discord Admin-Channel Benachrichtigungen

Sendet Status-Updates, Fehler und Erfolge direkt in einen konfigurierten Admin-Channel.
"""

import discord
from datetime import datetime
from typing import Optional, List, Dict
from loguru import logger

from config import ADMIN_CHANNEL_ID, DISCORD_NOTIFY_ERRORS_ONLY

# Globale Referenz zum Bot-Client (wird von client.py gesetzt)
_bot_client = None


def set_bot_client(client):
    """Setzt die Bot-Client-Referenz für Benachrichtigungen."""
    global _bot_client
    _bot_client = client
    logger.debug("Notification-Client gesetzt")


async def send_notification(
    title: str,
    description: str,
    color: int = 0x3498DB,  # Blau als Default
    fields: Optional[List[Dict]] = None,
    thumbnail_url: Optional[str] = None
) -> bool:
    """
    Sendet eine Benachrichtigung in den Admin-Channel.

    Args:
        title: Titel des Embeds
        description: Beschreibung/Inhalt
        color: Embed-Farbe (hex)
        fields: Optionale Felder [{name, value, inline}]
        thumbnail_url: Optionales Thumbnail-Bild

    Returns:
        True bei Erfolg, False bei Fehler
    """
    if not ADMIN_CHANNEL_ID:
        logger.debug("Kein ADMIN_CHANNEL_ID konfiguriert - überspringe Benachrichtigung")
        return False

    if not _bot_client:
        logger.warning("Bot-Client nicht gesetzt - kann keine Benachrichtigung senden")
        return False

    try:
        channel = _bot_client.get_channel(ADMIN_CHANNEL_ID)
        if not channel:
            try:
                channel = await _bot_client.fetch_channel(ADMIN_CHANNEL_ID)
            except discord.NotFound:
                logger.warning(f"Admin-Channel {ADMIN_CHANNEL_ID} nicht gefunden")
                return False
            except Exception as e:
                logger.warning(f"Fehler beim Holen des Admin-Channels: {e}")
                return False

        # Embed erstellen
        embed = discord.Embed(
            title=title,
            description=description,
            color=color,
            timestamp=datetime.utcnow()
        )

        if fields:
            for field in fields:
                embed.add_field(
                    name=field.get("name", ""),
                    value=field.get("value", ""),
                    inline=field.get("inline", False)
                )

        if thumbnail_url:
            embed.set_thumbnail(url=thumbnail_url)

        embed.set_footer(text="GTCHA Bot")

        await channel.send(embed=embed)
        logger.debug(f"Benachrichtigung gesendet: {title}")
        return True

    except Exception as e:
        logger.warning(f"Fehler beim Senden der Benachrichtigung: {e}")
        return False


# === FEHLER-BENACHRICHTIGUNGEN ===

async def notify_scrape_error(error_type: str, details: str, attempt: int = 0, max_attempts: int = 0):
    """Benachrichtigt über Scrape-Fehler."""
    fields = []
    if max_attempts > 0:
        fields.append({
            "name": "Versuch",
            "value": f"{attempt + 1}/{max_attempts + 1}",
            "inline": True
        })

    await send_notification(
        title=f"Scrape-Fehler: {error_type}",
        description=details,
        color=0xFF0000,  # Rot
        fields=fields if fields else None
    )


async def notify_critical_error(error_message: str):
    """Benachrichtigt über kritische Fehler."""
    await send_notification(
        title="Kritischer Fehler",
        description=error_message,
        color=0x8B0000  # Dunkelrot
    )


async def notify_low_banner_count(found: int, expected_min: int):
    """Benachrichtigt wenn zu wenige Banner gefunden wurden."""
    await send_notification(
        title="Wenige Banner gefunden",
        description=f"Nur **{found}** Banner gefunden (Minimum: {expected_min}).\n"
                    f"Not-Found-Tracking wurde übersprungen.",
        color=0xFFA500,  # Orange
        fields=[
            {"name": "Gefunden", "value": str(found), "inline": True},
            {"name": "Minimum", "value": str(expected_min), "inline": True}
        ]
    )


async def notify_all_retries_failed():
    """Benachrichtigt wenn alle Retries fehlgeschlagen sind."""
    await send_notification(
        title="Alle Scrape-Versuche fehlgeschlagen",
        description="Der Scrape-Job konnte nach mehreren Versuchen nicht erfolgreich "
                    "abgeschlossen werden. Bitte System überprüfen!",
        color=0x8B0000  # Dunkelrot
    )


# === ERFOLGS-BENACHRICHTIGUNGEN ===
# Bei DISCORD_NOTIFY_ERRORS_ONLY=true werden diese übersprungen

async def notify_scrape_success(
    new_banners: int,
    deleted_banners: int,
    expired_banners: int,
    duration_seconds: float,
    total_banners: int = 0
):
    """Benachrichtigt über erfolgreichen Scrape-Durchlauf."""
    if DISCORD_NOTIFY_ERRORS_ONLY:
        return False
    changes = []
    if new_banners > 0:
        changes.append(f"+{new_banners} neu")
    if deleted_banners > 0:
        changes.append(f"-{deleted_banners} archiviert")
    if expired_banners > 0:
        changes.append(f"-{expired_banners} abgelaufen")

    if changes:
        description = f"**Änderungen:** {', '.join(changes)}"
    else:
        description = "Keine Änderungen"

    await send_notification(
        title="Scrape erfolgreich",
        description=description,
        color=0x2ECC71,  # Grün
        fields=[
            {"name": "Dauer", "value": f"{duration_seconds:.1f}s", "inline": True},
            {"name": "Gesamt", "value": str(total_banners), "inline": True}
        ]
    )


async def notify_bot_started():
    """Benachrichtigt dass der Bot gestartet wurde."""
    if DISCORD_NOTIFY_ERRORS_ONLY:
        return False
    await send_notification(
        title="Bot gestartet",
        description="GTCHA Discord Bot ist online und bereit.",
        color=0x2ECC71  # Grün
    )


async def notify_bot_stopped():
    """Benachrichtigt dass der Bot gestoppt wird."""
    if DISCORD_NOTIFY_ERRORS_ONLY:
        return False
    await send_notification(
        title="Bot wird gestoppt",
        description="GTCHA Discord Bot wird heruntergefahren.",
        color=0xE74C3C  # Rot
    )
