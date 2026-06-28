from __future__ import annotations

import logging
import sqlite3
from datetime import UTC, datetime

from telegram import Update
from telegram.error import TelegramError
from telegram.ext import ContextTypes

from toop.config import settings
from toop.drift import (
    compute_drift,
    current_yes_set,
    display_names,
    drift_signature,
    get_last_drift_signature,
    set_drift_signature,
)
from toop.i18n import t
from toop.pause import events_are_paused
from toop.poll import (
    PollRow,
    attendance_options,
    attendance_question,
    capacity_message,
    get_poll,
    list_waitlist,
    quorum_message,
    record_attendance_answer,
    record_poll,
    record_reservation_answer,
    reservation_options,
    reservation_question,
    set_cap_closed,
    set_quorum_announced,
)
from toop.rsvp import count_rsvps
from toop.sessions import (
    Session,
    next_weekday,
    reopen_session,
)
from toop.snapshots import get_snapshot

logger = logging.getLogger(__name__)


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
    """Send the weekly yes/no attendance poll to the group and record it."""
    await _send_and_record_poll(
        context, conn, session.id, attendance_question(), attendance_options(), "attendance"
    )


async def post_reservation_poll(
    context: ContextTypes.DEFAULT_TYPE, conn: sqlite3.Connection, session_id: int
) -> None:
    """Send the reservation/waitlist poll opened once attendance caps."""
    await _send_and_record_poll(
        context, conn, session_id, reservation_question(), reservation_options(), "reservation"
    )


async def weekly_attendance_job(context: ContextTypes.DEFAULT_TYPE) -> None:
    """Scheduled job: auto-close the prior session, open the coming one, and
    post the attendance poll. Closing here is what retires last week's session
    now that there is no manual /close_session.

    Skips entirely while the schedule is paused (/pause_events): no session is
    opened and no poll is posted in that window.
    """
    conn = _conn(context)
    if events_are_paused(conn, datetime.now(UTC)):
        logger.info("weekly_attendance_job: events paused; skipping poll")
        return
    sess = reopen_session(conn, next_weekday(settings.SESSION_WEEKDAY))
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
    await _safe_send(context, capacity_message())
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
    if not poll.quorum_announced and yes >= settings.QUORUM_THRESHOLD:
        await _safe_send(
            context,
            quorum_message(
                settings.PAYMENT_AMOUNT, settings.PAYMENT_EMAIL, settings.ACCOUNTING_SHEET_URL
            ),
        )
        set_quorum_announced(conn, poll.poll_id)
    if not poll.cap_closed and yes >= settings.MAX_ATTENDEES:
        await _close_attendance_poll(context, conn, poll)


async def _maybe_notify_drift(
    context: ContextTypes.DEFAULT_TYPE, conn: sqlite3.Connection, session_id: int
) -> None:
    """DM the admin when attendance drifts from the snapshot it was built on.

    Only fires once a snapshot exists, and dedupes on the drift signature so a
    vote that doesn't move the attendee set (or an unchanged drift state) never
    re-pings. The DM lists who joined/dropped plus the current waitlist so the
    admin can promote with /change_player.
    """
    if settings.ADMIN_TELEGRAM_ID == 0:
        return
    snap = get_snapshot(conn, session_id)
    if snap is None:
        return
    snapshot_ids = set(snap.team_a) | set(snap.team_b) | set(snap.cut)
    added, removed = compute_drift(snapshot_ids, current_yes_set(conn, session_id))
    if not added and not removed:
        return
    signature = drift_signature(added, removed)
    if signature == get_last_drift_signature(conn, session_id):
        return
    parts = [t("poll.drift_header", sid=session_id)]
    if added:
        parts.append(t("poll.drift_added", names=", ".join(display_names(conn, added))))
    if removed:
        parts.append(t("poll.drift_dropped", names=", ".join(display_names(conn, removed))))
    waitlist = list_waitlist(conn, session_id)
    if waitlist:
        parts.append(t("poll.drift_waitlist", names=", ".join(display_names(conn, waitlist))))
    parts.append(t("poll.drift_fix"))
    try:
        await context.bot.send_message(chat_id=settings.ADMIN_TELEGRAM_ID, text="\n".join(parts))
    except TelegramError as exc:
        logger.warning("failed to DM admin about drift: %s", exc)
    set_drift_signature(conn, session_id, signature)


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
        await _maybe_notify_drift(context, conn, poll.session_id)
    else:
        record_reservation_answer(conn, poll.session_id, answer.user.id, option_ids)
