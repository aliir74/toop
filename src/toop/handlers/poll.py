from __future__ import annotations

import logging
import sqlite3

from telegram import Update
from telegram.error import TelegramError
from telegram.ext import ContextTypes

from toop.config import settings
from toop.poll import (
    ATTENDANCE_OPTIONS,
    CAPACITY_MESSAGE,
    RESERVATION_OPTIONS,
    RESERVATION_QUESTION,
    PollRow,
    get_poll,
    quorum_message,
    record_attendance_answer,
    record_poll,
    record_reservation_answer,
    set_cap_closed,
    set_quorum_announced,
)
from toop.rsvp import count_rsvps
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


async def _send_and_record_poll(
    context: ContextTypes.DEFAULT_TYPE,
    conn: sqlite3.Connection,
    session_id: int,
    question: str,
    options: list[str],
    kind: str,
) -> None:
    """Send a non-anonymous single-answer poll to the group and record it.

    Non-anonymous + single-answer is the only Bot-API way to learn who voted.
    No-op when GROUP_CHAT_ID is unset.
    """
    if settings.GROUP_CHAT_ID == 0:
        logger.warning("GROUP_CHAT_ID unset — skipping %s poll", kind)
        return
    try:
        message = await context.bot.send_poll(
            chat_id=settings.GROUP_CHAT_ID,
            question=question,
            options=options,
            is_anonymous=False,
            allows_multiple_answers=False,
        )
    except TelegramError as exc:
        logger.warning("failed to post %s poll: %s", kind, exc)
        return
    if message.poll is None:
        return
    record_poll(
        conn,
        session_id=session_id,
        poll_id=message.poll.id,
        kind=kind,
        message_id=message.message_id,
    )


async def post_attendance_poll(
    context: ContextTypes.DEFAULT_TYPE, conn: sqlite3.Connection, session: Session
) -> None:
    """Send the weekly بلی/خیر attendance poll to the group and record it."""
    await _send_and_record_poll(
        context, conn, session.id, ATTENDANCE_QUESTION, list(ATTENDANCE_OPTIONS), "attendance"
    )


async def post_reservation_poll(
    context: ContextTypes.DEFAULT_TYPE, conn: sqlite3.Connection, session_id: int
) -> None:
    """Send the reservation/waitlist poll opened once attendance caps."""
    await _send_and_record_poll(
        context, conn, session_id, RESERVATION_QUESTION, list(RESERVATION_OPTIONS), "reservation"
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


async def _safe_send(context: ContextTypes.DEFAULT_TYPE, text: str) -> None:
    try:
        await context.bot.send_message(chat_id=settings.GROUP_CHAT_ID, text=text)
    except TelegramError as exc:
        logger.warning("failed to post to group: %s", exc)


async def _close_attendance_poll(
    context: ContextTypes.DEFAULT_TYPE, conn: sqlite3.Connection, poll: PollRow
) -> None:
    """Capacity reached: stop the poll, announce it's full, latch it closed."""
    if poll.message_id is not None:
        try:
            await context.bot.stop_poll(settings.GROUP_CHAT_ID, poll.message_id)
        except TelegramError as exc:
            logger.warning("failed to stop attendance poll: %s", exc)
    await _safe_send(context, CAPACITY_MESSAGE)
    set_cap_closed(conn, poll.poll_id)
    await post_reservation_poll(context, conn, poll.session_id)


async def _maybe_fire_thresholds(
    context: ContextTypes.DEFAULT_TYPE, conn: sqlite3.Connection, poll: PollRow
) -> None:
    """Fire the quorum announcement and the capacity close, each at most once.

    Checked in order so a single batch that lands straight on the cap still posts
    the quorum + payment announcement before closing.
    """
    if settings.GROUP_CHAT_ID == 0:
        return
    yes = count_rsvps(conn, poll.session_id).yes
    if not poll.quorum_announced and yes > settings.QUORUM_THRESHOLD:
        await _safe_send(
            context,
            quorum_message(
                settings.PAYMENT_AMOUNT, settings.PAYMENT_EMAIL, settings.ACCOUNTING_SHEET_URL
            ),
        )
        set_quorum_announced(conn, poll.poll_id)
    if not poll.cap_closed and yes >= settings.MAX_ATTENDEES:
        await _close_attendance_poll(context, conn, poll)


async def handle_poll_answer(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Ingest a vote on a bot-owned attendance poll, then fire any thresholds."""
    answer = update.poll_answer
    if answer is None:
        return
    conn = _conn(context)
    poll = get_poll(conn, answer.poll_id)
    if poll is None or answer.user is None:
        return
    option_ids = list(answer.option_ids)
    if poll.kind == "attendance":
        record_attendance_answer(conn, poll.session_id, answer.user.id, option_ids)
        await _maybe_fire_thresholds(context, conn, poll)
    else:
        record_reservation_answer(conn, poll.session_id, answer.user.id, option_ids)
