from __future__ import annotations

import logging
import sqlite3

from telegram.error import TelegramError
from telegram.ext import ContextTypes

from toop.config import settings
from toop.i18n import t
from toop.players import dont_know_stats

logger = logging.getLogger(__name__)


def _conn(context: ContextTypes.DEFAULT_TYPE) -> sqlite3.Connection:
    conn = context.bot_data.get("conn")
    if conn is None:
        raise RuntimeError("DB connection missing from bot_data")
    return conn


def _rateable_ids(conn: sqlite3.Connection) -> set[int]:
    """Players currently in the rating pool — already-paused/disabled ones are
    excluded so the alert never re-suggests pausing someone already paused."""
    rows = conn.execute(
        "SELECT telegram_id FROM players WHERE active=1 AND in_pool=1 "
        "AND (pool_paused_until IS NULL OR pool_paused_until <= CURRENT_TIMESTAMP)"
    ).fetchall()
    return {r["telegram_id"] for r in rows}


async def dk_alert_job(context: ContextTypes.DEFAULT_TYPE) -> None:
    """Scheduled job: DM the admin suggesting players to pause from the rating
    pool when their don't-know signal crosses both configured thresholds.

    Acting on a suggestion (pausing) removes the player from the next scan, so
    this won't keep re-alerting the same person day after day.
    """
    conn = _conn(context)
    if settings.ADMIN_TELEGRAM_ID == 0:
        logger.warning("dk_alert: ADMIN_TELEGRAM_ID unset; not DMing")
        return
    rateable = _rateable_ids(conn)
    flagged = [
        s
        for s in dont_know_stats(conn)
        if s.telegram_id in rateable
        and s.dk_count >= settings.DK_ALERT_MIN_PROMPTS
        and s.dk_rate >= settings.DK_ALERT_RATE
    ]
    if not flagged:
        logger.info("dk_alert: no players over the don't-know thresholds")
        return
    lines = [t("alert.header")]
    for s in flagged:
        pct = round(s.dk_rate * 100)
        lines.append(
            t(
                "alert.row",
                name=s.display_name,
                dk=s.dk_count,
                total=s.total,
                pct=pct,
                id=s.telegram_id,
                days=settings.DEFAULT_PAUSE_DAYS,
            )
        )
    try:
        await context.bot.send_message(chat_id=settings.ADMIN_TELEGRAM_ID, text="\n".join(lines))
    except TelegramError as exc:
        logger.warning("dk_alert: failed to DM admin: %s", exc)
