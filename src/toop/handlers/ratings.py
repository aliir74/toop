from __future__ import annotations

import logging
import sqlite3

from telegram import Update
from telegram.ext import ContextTypes

from toop.admin import require_admin
from toop.config import settings
from toop.rating import refresh_ratings

logger = logging.getLogger(__name__)


def _conn(context: ContextTypes.DEFAULT_TYPE) -> sqlite3.Connection:
    conn = context.bot_data.get("conn")
    if conn is None:
        raise RuntimeError("DB connection missing from bot_data")
    return conn


@require_admin
async def handle_refresh_ratings(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    message = update.effective_message
    if message is None:
        return
    rows = refresh_ratings(
        _conn(context),
        settings.CALIBRATION_THRESHOLD,
        normalize=settings.NORMALIZATION_ENABLED,
        norm_min_ratings=settings.NORM_MIN_RATINGS,
        shrinkage_k=settings.SHRINKAGE_K,
    )
    await message.reply_text(f"Refit ratings — wrote {rows} rows across 6 indicators.")
