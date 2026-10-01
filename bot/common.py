"""Gemeinsame Importe, Konstanten und Hilfsfunktionen für die Bot-Module."""

import asyncio
import json
import re
from dataclasses import dataclass
from datetime import datetime, timedelta
from math import comb
import re as regex_module
from typing import Optional

import aiosqlite
import discord
from discord import app_commands
from discord.ext import commands
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from loguru import logger

import os
import sys
from config import (
    GUILD_ID, SCRAPE_INTERVAL_MINUTES, BASE_URL,
    CHANNEL_IDS, CATEGORIES, SCRAPE_TIMEOUT_SECONDS,
    MENTION_ON_NEW_THREAD, MENTION_ON_PACK_UPDATE,
    HOT_BANNER_CHANNEL_ID, HOT_BANNER_ENABLED,
    DAILY_RESTART_TIME
)
from scraper.gtcha_scraper import GTCHAScraper
from scraper.models import ScrapedBanner
from database.db import Database
from utils.notifications import (
    set_bot_client, notify_scrape_error,
    notify_all_retries_failed, notify_critical_error,
    notify_scrape_success, notify_bot_started, send_notification
)
from utils.rate_limiter import discord_rate_limiter
from utils.memory_monitor import memory_monitor
from utils.cache import banner_cache
from utils.maintenance import backup_database, new_tor_identity, start_watchdog, touch_heartbeat
from utils.banner_info import (
    banner_conditions, category_for, chance_at_least_one, format_conditions, format_shipping,
    sale_end_timestamp,
    is_upcoming, jst_timestamp, parse_thread_title, shipping_stats, thread_title, to_int as _int,
)
from utils.card_pool import (
    card_value, card_value_changes, estimate, fmt_coins, fmt_pct, out_of_banner_value, TIERS, MAX_LISTED, EMBEDS_PER_MESSAGE, decided_value, detect_jump_pulls,
    is_relevant_hit, match_shipped_hits, pool_minimum, match_shipment_history, prefer_claimed, relevant_units, resolve_pulled, shipment_values, tier_keys,
    tracked_units, claimable_units, medal_units, medal_units_hits_first,
)


# Erhöhen, wenn der Startbeitrag neue Felder bekommt: alle Threads werden dann einmal aktualisiert
EMBED_VERSION = 8
# Endspurt-Alarm, sobald höchstens so viel Prozent der Packs übrig sind und noch Hits drin sind
ENDSPURT_PERCENT = float(os.getenv("ENDSPURT_PERCENT") or "10")
# Zeitraum für das Abverkaufs-Tempo
SALES_WINDOW_HOURS = 2
# Zugzahlen für die Hit-Chance in der 🎯-Nachricht
HIT_CHANCE_PULLS = (1, 10, 50)


# Fehlende Kartenpools, die pro Scrape geladen werden (Nachrüsten bestehender Threads)
POOL_FETCH_PER_SCRAPE = 8
# Normale Scrapes laufen nur über pack/list; Tabs werden höchstens so oft komplett durchgeklickt
FULL_SCRAPE_EVERY_MINUTES = 15
# Thread löschen, wenn ein Banner so oft hintereinander fehlt oder ausverkauft ist
NOT_FOUND_DELETE_AFTER = 2
# Pool-Wechsel-Alarm ab so vielen abgefangenen Pack-Anstiegen in einem Scrape (mind. 20 % der Banner)
POOL_SWITCH_MIN_RISES = 5
# Meldung im Admin-Kanal nach so vielen fehlerhaften Scrapes in Folge
SCRAPE_PROBLEM_ALERT_AFTER = 3


MEDAL_EMOJIS = {"T1": "🥇", "T2": "🥈", "T3": "🥉", "T4": "4️⃣", "T5": "5️⃣", "T6": "6️⃣",
                "T7": "7️⃣", "T8": "8️⃣", "T9": "9️⃣", "T10": "🔟"}
EMOJI_TO_MEDAL = {emoji: tier for tier, emoji in MEDAL_EMOJIS.items()}
MEDAL_EMOJI_DEFAULT = "🏅"  # ab T11 (keine eigene Ziffer; Gewinner steht in der Hit-Liste)




def format_end_date_countdown(sale_end_date: str) -> str:
    """Konvertiert Enddatum zu Countdown-Format (z.B. 'Endet in 3 Tagen')."""
    if not sale_end_date:
        return None

    try:
        # Versuche Datum aus String zu extrahieren (Format: "2026/01/23 まで販売" oder "2026/01/23")
        date_match = regex_module.search(r'(\d{4})/(\d{2})/(\d{2})', sale_end_date)
        if not date_match:
            return sale_end_date  # Fallback zum Original

        year, month, day = int(date_match.group(1)), int(date_match.group(2)), int(date_match.group(3))
        end_date = datetime(year, month, day, 23, 59, 59)  # Ende des Tages

        now = datetime.now()
        delta = end_date - now
        days = delta.days

        if days < 0:
            return "Abgelaufen"
        elif days == 0:
            return "Endet heute!"
        elif days == 1:
            return "Endet morgen"
        elif days <= 7:
            return f"Endet in {days} Tagen"
        else:
            # Deutsches Datumsformat für längere Zeiträume
            months_de = ["", "Januar", "Februar", "März", "April", "Mai", "Juni",
                        "Juli", "August", "September", "Oktober", "November", "Dezember"]
            return f"{day}. {months_de[month]} {year}"
    except Exception:
        return sale_end_date  # Fallback zum Original


@dataclass
class RecoveredBanner:
    """Minimale Banner-Daten für Wiederherstellung aus Discord."""
    pack_id: int
    category: str
    title: str = None
    best_hit: str = None
    price_coins: int = None
    current_packs: int = None
    total_packs: int = None
    entries_per_day: int = None
    sale_end_date: str = None
    image_url: str = None
    detail_page_url: str = None


# Alle Namen (auch mit Unterstrich) für "from bot.common import *" freigeben
__all__ = [name for name in dict(globals()) if not name.startswith("__")]

