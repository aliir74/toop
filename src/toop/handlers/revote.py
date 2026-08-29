"""Weekly re-vote nudge job, and the admin's on-demand /revote_ping.

Both are DELIVERY ONLY. Neither touches REVOTE_AFTER_DAYS and neither writes to
`scores`, so nothing here can make anyone re-rate a score that is still inside
the window. `/revote_ping force` waives the per-voter DM cooldown and nothing
else. This is the single most likely thing for a future reader to get wrong.
"""

from __future__ import annotations

import logging
import sqlite3
from datetime import UTC, datetime

from telegram import Update
from telegram.error import BadRequest, Forbidden
from telegram.ext import ContextTypes

from toop.admin import require_admin
from toop.config import settings
from toop.i18n import t
from toop.pause import events_are_paused
from toop.revote import NudgeTarget, nudge_targets, record_nudge

logger = logging.getLogger(__name__)


def _conn(context: ContextTypes.DEFAULT_TYPE) -> sqlite3.Connection:
    conn = context.bot_data.get("conn")
    if conn is None:
        raise RuntimeError("DB connection missing from bot_data")
    return conn


async def _run_nudges(
    conn: sqlite3.Connection,
    context: ContextTypes.DEFAULT_TYPE,
    *,
    ignore_cooldown: bool,
) -> tuple[list[NudgeTarget], int]:
    """DM everyone due a nudge. Returns (targets actually DMed, failure count).

    record_nudge runs only after a send succeeds, so a transient failure is
    retried next week instead of silently starting that player's cooldown.
    Permanently unreachable players are kept out of the list by
    revote.nudge_targets rather than being retried forever here.
    """
    targets = nudge_targets(
        conn,
        cooldown_days=settings.REVOTE_NUDGE_COOLDOWN_DAYS,
        min_pending=settings.REVOTE_NUDGE_MIN_PENDING,
        ignore_cooldown=ignore_cooldown,
    )
    sent: list[NudgeTarget] = []
    failed = 0
    for target in targets:
        try:
            await context.bot.send_message(
                chat_id=target.telegram_id,
                text=t("revote.dm_nudge", first=target.first_name, total=target.total),
            )
        except (Forbidden, BadRequest) as exc:
            failed += 1
            logger.info("revote nudge: could not DM %s: %s", target.telegram_id, exc)
            continue
        record_nudge(conn, target.telegram_id)
        sent.append(target)
    return sent, failed


async def revote_nudge_job(context: ContextTypes.DEFAULT_TYPE) -> None:
    """Weekly job: DM everyone holding pending targets.

    Skips entirely while /pause_events is in effect. The attendance poll and
    auto-snapshot already stand down during a break, and a bot that keeps
    chasing votes while nobody is playing is exactly what gets it muted.
    """
    conn = _conn(context)
    if events_are_paused(conn, datetime.now(UTC)):
        logger.info("revote nudge: events paused, skipping")
        return
    sent, failed = await _run_nudges(conn, context, ignore_cooldown=False)
    logger.info("revote nudge: %s DMed, %s failed", len(sent), failed)


@require_admin
async def handle_revote_ping(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    message = update.effective_message
    if message is None:
        return
    force = bool(context.args) and context.args[0].lower() == "force"
    sent, failed = await _run_nudges(_conn(context), context, ignore_cooldown=force)
    if not sent:
        await message.reply_text(t("revote.ping_none"))
        return
    lines = [t("revote.ping_header")]
    lines += [t("revote.ping_row", name=x.display_name, total=x.total) for x in sent]
    lines.append(t("revote.ping_sent", sent=len(sent), failed=failed))
    await message.reply_text("\n".join(lines))
