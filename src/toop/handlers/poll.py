from __future__ import annotations

import logging
import sqlite3
from datetime import UTC, datetime

from telegram import ReplyParameters, Update, User
from telegram.error import TelegramError
from telegram.ext import ContextTypes

from toop.config import settings
from toop.drift import (
    compute_drift,
    display_names,
    drift_signature,
    get_last_drift_signature,
    set_drift_signature,
)
from toop.i18n import t
from toop.pause import events_are_paused
from toop.players import add_player
from toop.poll import (
    PollRow,
    attendance_options,
    attendance_question,
    capacity_message,
    get_poll,
    quorum_message,
    record_attendance_answer,
    record_poll,
    roster_message,
    set_cap_announced,
    set_quorum_announced,
    set_roster_message_id,
    voters_by_status,
)
from toop.rsvp import count_rsvps, is_player_on_roster
from toop.selection import select_attendees
from toop.sessions import (
    Session,
    next_weekday,
    reopen_session,
)
from toop.snapshots import get_snapshot

logger = logging.getLogger(__name__)

# A burst of votes (the poll's first minutes) collapses into one roster edit,
# keeping the bot under Telegram's ~20 messages/minute per-group limit.
ROSTER_EDIT_DELAY_SECONDS = 3


def _conn(context: ContextTypes.DEFAULT_TYPE) -> sqlite3.Connection:
    conn = context.bot_data.get("conn")
    if conn is None:
        raise RuntimeError("DB connection missing from bot_data")
    return conn


def _ensure_voter_registered(conn: sqlite3.Connection, user: User) -> None:
    """Auto-add a poll voter to the roster if they aren't already on it.

    Group polls are bot-owned and only group members can answer, so every voter
    is a real player. The rsvps/waitlist tables FK to players, and a roster gate
    used to silently drop votes from anyone not yet added — which made quorum and
    capacity undercount actual attendance (a 14-yes poll counted as 13).
    Registering unknown voters first means every vote counts. Only fills in
    unknown voters; never overwrites an existing player's admin-set name.
    """
    if is_player_on_roster(conn, user.id):
        return
    add_player(conn, user.id, user.full_name or str(user.id), user.username)


async def post_attendance_poll(
    context: ContextTypes.DEFAULT_TYPE, conn: sqlite3.Connection, session: Session
) -> None:
    """Send the weekly yes/no attendance poll to the group, record it, and post
    the live voter list as a reply under it.

    Non-anonymous + single-answer is the only Bot-API way to learn who voted.
    Revoting is explicit so voters can always retract. No-op when GROUP_CHAT_ID
    is unset.
    """
    if settings.GROUP_CHAT_ID == 0:
        logger.warning("GROUP_CHAT_ID unset — skipping attendance poll")
        return
    try:
        message = await context.bot.send_poll(
            chat_id=settings.GROUP_CHAT_ID,
            question=attendance_question(),
            options=attendance_options(),
            is_anonymous=False,
            allows_multiple_answers=False,
            # PTB 22.7 has no kwarg yet; Bot API 9.x defaults it on for regular polls.
            api_kwargs={"allows_revoting": True},
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
    try:
        roster = await context.bot.send_message(
            chat_id=settings.GROUP_CHAT_ID,
            text=_render_roster(conn, session.id),
            reply_parameters=ReplyParameters(message_id=message.message_id),
        )
    except TelegramError as exc:
        logger.warning("failed to post voter list: %s", exc)
        return
    set_roster_message_id(conn, message.poll.id, roster.message_id)


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


def _render_roster(conn: sqlite3.Connection, session_id: int) -> str:
    selection = select_attendees(conn, session_id, settings.MAX_ATTENDEES)
    return roster_message(
        display_names(conn, selection.selected),
        display_names(conn, selection.cut),
        display_names(conn, voters_by_status(conn, session_id, "no")),
        settings.MAX_ATTENDEES,
    )


async def _edit_roster(
    context: ContextTypes.DEFAULT_TYPE, conn: sqlite3.Connection, poll: PollRow
) -> None:
    """Rewrite the voter list under the poll from the current RSVPs."""
    if poll.roster_message_id is None:
        return
    try:
        await context.bot.edit_message_text(
            chat_id=settings.GROUP_CHAT_ID,
            message_id=poll.roster_message_id,
            text=_render_roster(conn, poll.session_id),
        )
    except TelegramError as exc:
        # A vote and its retraction inside one debounce window leave the text
        # unchanged, and Telegram rejects a no-op edit.
        if "not modified" not in str(exc).lower():
            logger.warning("failed to update voter list: %s", exc)


async def _roster_edit_job(context: ContextTypes.DEFAULT_TYPE) -> None:
    job = context.job
    if job is None:
        return
    conn = _conn(context)
    poll = get_poll(conn, str(job.data))
    if poll is not None:
        await _edit_roster(context, conn, poll)


async def _schedule_roster_edit(
    context: ContextTypes.DEFAULT_TYPE, conn: sqlite3.Connection, poll: PollRow
) -> None:
    """Queue one delayed roster edit per poll; votes landing while it is pending
    ride along, since the job re-reads the RSVPs when it fires."""
    if poll.roster_message_id is None:
        return
    job_queue = context.job_queue
    if job_queue is None:
        await _edit_roster(context, conn, poll)
        return
    name = f"roster:{poll.poll_id}"
    if job_queue.get_jobs_by_name(name):
        return
    job_queue.run_once(_roster_edit_job, ROSTER_EDIT_DELAY_SECONDS, data=poll.poll_id, name=name)


async def _announce_capacity(
    context: ContextTypes.DEFAULT_TYPE, conn: sqlite3.Connection, poll: PollRow
) -> None:
    """Capacity reached: say so once. The poll stays open so attendees can
    still retract; later yes votes queue as the waitlist."""
    await _safe_send(context, capacity_message())
    set_cap_announced(conn, poll.poll_id)


async def _maybe_fire_thresholds(
    context: ContextTypes.DEFAULT_TYPE, conn: sqlite3.Connection, poll: PollRow
) -> None:
    """Fire the quorum announcement and the capacity close, each at most once.

    Checked in order so a single batch that lands straight on the cap still posts
    the quorum + payment announcement before the capacity notice.
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
    if not poll.cap_announced and yes >= settings.MAX_ATTENDEES:
        await _announce_capacity(context, conn, poll)


async def _maybe_notify_drift(
    context: ContextTypes.DEFAULT_TYPE, conn: sqlite3.Connection, session_id: int
) -> None:
    """DM the admin when attendance drifts from the snapshot it was built on.

    Only fires once a snapshot exists, and dedupes on the drift signature so a
    vote that doesn't move the attendee set (or an unchanged drift state) never
    re-pings. Compares against who is in now, not every yes: joining the
    waitlist is no drift, but a dropout promoting waitlist #1 is. The DM lists
    who joined/dropped plus the current waitlist so the admin can rebalance
    with /change_player.
    """
    if settings.ADMIN_TELEGRAM_ID == 0:
        return
    snap = get_snapshot(conn, session_id)
    if snap is None:
        return
    selection = select_attendees(conn, session_id, settings.MAX_ATTENDEES)
    added, removed = compute_drift(set(snap.team_a) | set(snap.team_b), set(selection.selected))
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
    if selection.cut:
        parts.append(t("poll.drift_waitlist", names=", ".join(display_names(conn, selection.cut))))
    parts.append(t("poll.drift_fix"))
    try:
        await context.bot.send_message(chat_id=settings.ADMIN_TELEGRAM_ID, text="\n".join(parts))
    except TelegramError as exc:
        logger.warning("failed to DM admin about drift: %s", exc)
    set_drift_signature(conn, session_id, signature)


async def handle_poll_answer(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Ingest a vote on a bot-owned attendance poll, then fire any thresholds
    and refresh the voter list. Answers on a retired reservation poll are ignored."""
    answer = update.poll_answer
    if answer is None:
        return
    conn = _conn(context)
    poll = get_poll(conn, answer.poll_id)
    if poll is None or poll.kind != "attendance" or answer.user is None:
        return
    _ensure_voter_registered(conn, answer.user)
    record_attendance_answer(conn, poll.session_id, answer.user.id, list(answer.option_ids))
    await _maybe_fire_thresholds(context, conn, poll)
    await _maybe_notify_drift(context, conn, poll.session_id)
    await _schedule_roster_edit(context, conn, poll)
