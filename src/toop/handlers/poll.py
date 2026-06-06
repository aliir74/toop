from __future__ import annotations

import logging
import sqlite3

from telegram import Update
from telegram.error import TelegramError
from telegram.ext import ContextTypes

from toop.config import settings
from toop.poll import (
    ATTENDANCE_OPTIONS,
    get_poll,
    record_attendance_answer,
    record_poll,
)
from toop.sessions import (
    Session,
    SessionStateError,
    next_weekday,
    open_session,
)

logger = logging.getLogger(__name__)

ATTENDANCE_QUESTION = "آیا در برنامه والیبال دوشنبه آینده (از ساعت ۶ تا ۸) شرکت میکنید؟"


def _conn(context: ContextTypes.DEFAULT_TYPE) -> sqlite3.Connection:
    conn = context.bot_data.get("conn")
    if conn is None:
        raise RuntimeError("DB connection missing from bot_data")
    return conn


async def post_attendance_poll(
    context: ContextTypes.DEFAULT_TYPE, conn: sqlite3.Connection, session: Session
) -> None:
    """Send the weekly بلی/خیر attendance poll to the group and record it.

    Non-anonymous + single-answer so the bot receives a poll_answer per voter
    (the only Bot-API way to learn who voted). No-op when GROUP_CHAT_ID is unset.
    """
    if settings.GROUP_CHAT_ID == 0:
        logger.warning("GROUP_CHAT_ID unset — skipping attendance poll")
        return
    try:
        message = await context.bot.send_poll(
            chat_id=settings.GROUP_CHAT_ID,
            question=ATTENDANCE_QUESTION,
            options=list(ATTENDANCE_OPTIONS),
            is_anonymous=False,
            allows_multiple_answers=False,
        )
    except TelegramError as exc:
        logger.warning("failed to post attendance poll: %s", exc)
        return
    if message.poll is None:
        return
    record_poll(
        conn,
        session_id=session.id,
        poll_id=message.poll.id,
        kind="attendance",
        message_id=message.message_id,
    )


async def weekly_attendance_job(context: ContextTypes.DEFAULT_TYPE) -> None:
    """Scheduled job: open the coming session and post the attendance poll.

    Skips when a session is still active (the prior one wasn't closed yet), so a
    missed /publish never double-posts the poll.
    """
    conn = _conn(context)
    try:
        sess = open_session(conn, next_weekday(settings.SESSION_WEEKDAY))
    except SessionStateError:
        logger.info("weekly poll: a session is already active; skipping")
        return
    await post_attendance_poll(context, conn, sess)


async def handle_poll_answer(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Ingest a vote on a bot-owned attendance poll into the rsvps table."""
    answer = update.poll_answer
    if answer is None:
        return
    conn = _conn(context)
    poll = get_poll(conn, answer.poll_id)
    if poll is None or poll.kind != "attendance":
        return
    if answer.user is None:
        return
    record_attendance_answer(conn, poll.session_id, answer.user.id, list(answer.option_ids))
