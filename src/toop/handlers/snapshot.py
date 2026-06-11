from __future__ import annotations

import logging
import sqlite3
from datetime import UTC, datetime

from telegram import Update
from telegram.error import TelegramError
from telegram.ext import ContextTypes
from telegram.helpers import escape_markdown

from toop.admin import require_admin
from toop.balance import (
    TeamMetrics,
    compute_metrics,
    generate_teams,
    swap_players,
)
from toop.config import settings
from toop.i18n import t
from toop.pause import events_are_paused
from toop.players import Player
from toop.rating import refresh_ratings
from toop.selection import select_attendees
from toop.sessions import get_active_session, set_session_status
from toop.snapshots import (
    Snapshot,
    get_snapshot,
    save_snapshot,
    update_teams,
    write_attendance,
)

logger = logging.getLogger(__name__)


def take_snapshot(
    conn: sqlite3.Connection,
    weights: dict[str, float],
    max_attendees: int,
    calibration_threshold: int,
) -> tuple[Snapshot, list[int]] | None:
    """Run the full snapshot pipeline. Returns (snap, cut) or None when no
    active session / no yes-RSVPs.
    """
    from toop.sessions import get_active_session as _get_active

    sess = _get_active(conn)
    if sess is None:
        return None
    selection = select_attendees(conn, sess.id, max_attendees)
    if not selection.selected:
        return None
    refresh_ratings(
        conn,
        calibration_threshold,
        normalize=settings.NORMALIZATION_ENABLED,
        norm_min_ratings=settings.NORM_MIN_RATINGS,
        shrinkage_k=settings.SHRINKAGE_K,
    )
    team_a, team_b, metrics = generate_teams(conn, selection.selected, weights)
    save_snapshot(conn, sess.id, team_a, team_b, selection.cut, metrics)
    set_session_status(conn, sess.id, "snapshotted", snapshot_at=True)
    snap = get_snapshot(conn, sess.id)
    assert snap is not None
    return snap, selection.cut


def _conn(context: ContextTypes.DEFAULT_TYPE) -> sqlite3.Connection:
    conn = context.bot_data.get("conn")
    if conn is None:
        raise RuntimeError("DB connection missing from bot_data")
    return conn


def _weights() -> dict[str, float]:
    return settings.composite_weights()


def _fetch_player(conn: sqlite3.Connection, telegram_id: int) -> Player | None:
    row = conn.execute(
        "SELECT telegram_id, username, display_name, is_calibrating, active "
        "FROM players WHERE telegram_id=?",
        (telegram_id,),
    ).fetchone()
    if row is None:
        return None
    return Player(
        telegram_id=row["telegram_id"],
        username=row["username"],
        display_name=row["display_name"],
        is_calibrating=bool(row["is_calibrating"]),
        active=bool(row["active"]),
    )


def _fetch_player_by_username(conn: sqlite3.Connection, username: str) -> Player | None:
    handle = username.lstrip("@").lower()
    row = conn.execute(
        "SELECT telegram_id, username, display_name, is_calibrating, active "
        "FROM players WHERE username=? AND active=1",
        (handle,),
    ).fetchone()
    if row is None:
        return None
    return Player(
        telegram_id=row["telegram_id"],
        username=row["username"],
        display_name=row["display_name"],
        is_calibrating=bool(row["is_calibrating"]),
        active=bool(row["active"]),
    )


def _names(conn: sqlite3.Connection, ids: list[int]) -> list[str]:
    return [
        (_fetch_player(conn, pid) or Player(pid, None, f"#{pid}", True, True)).display_name
        for pid in ids
    ]


def _format_attendance(conn: sqlite3.Connection, snap: Snapshot) -> str:
    """Roster line(s) posted alongside the teams: who's playing, plus any cut.
    Names are markdown-escaped since this is sent with parse_mode="Markdown"."""
    attendees = [escape_markdown(n, version=1) for n in _names(conn, snap.team_a + snap.team_b)]
    line = t("snapshot.attending", n=len(attendees)) + ", ".join(attendees)
    if snap.cut:
        cut = [escape_markdown(n, version=1) for n in _names(conn, snap.cut)]
        line += t("snapshot.cut", names=", ".join(cut))
    return line


def _team_block(label: str, names: list[str]) -> str:
    """One team as a labelled, numbered vertical list — readable on a phone
    without the column drift of a fixed-width two-column table."""
    lines = [f"{label} — {len(names)}"]
    for i, name in enumerate(names, start=1):
        lines.append(f"{i}. {escape_markdown(name, version=1)}")
    return "\n".join(lines)


def _format_teams(conn: sqlite3.Connection, snap: Snapshot, session_date: str) -> str:
    a_block = _team_block(t("snapshot.team_a_label"), _names(conn, snap.team_a))
    b_block = _team_block(t("snapshot.team_b_label"), _names(conn, snap.team_b))
    metrics = snap.metrics
    return (
        t("snapshot.proposed", date=session_date) + "\n\n"
        f"{a_block}\n\n{b_block}\n\n"
        + t(
            "snapshot.composite_delta",
            delta=metrics.abs_delta,
            a=metrics.team_a_total,
            b=metrics.team_b_total,
        )
        + "\n"
        + t("snapshot.calibration_conf", conf=metrics.calibration_confidence)
    )


def _format_snapshot_summary(conn: sqlite3.Connection, snap: Snapshot, cut: list[int]) -> str:
    cut_note = ""
    if cut:
        cut_names = [
            (_fetch_player(conn, pid) or Player(pid, None, f"#{pid}", True, True)).display_name
            for pid in cut
        ]
        cut_note = t("snapshot.summary_cut", names=", ".join(cut_names))
    swap_note = t("snapshot.setter_swap") if snap.metrics.setter_swap_applied else ""
    return t("snapshot.summary", sid=snap.session_id, swap=swap_note, cut=cut_note)


@require_admin
async def handle_snapshot(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    message = update.effective_message
    if message is None:
        return
    conn = _conn(context)
    sess = get_active_session(conn)
    if sess is None:
        await message.reply_text(t("snapshot.no_active_open"))
        return
    result = take_snapshot(conn, _weights(), settings.MAX_ATTENDEES, settings.CALIBRATION_THRESHOLD)
    if result is None:
        await message.reply_text(t("snapshot.no_rsvps"))
        return
    snap, cut = result
    await message.reply_text(
        f"{_format_snapshot_summary(conn, snap, cut)}\n\n"
        f"{_format_teams(conn, snap, sess.session_date.isoformat())}",
        parse_mode="Markdown",
    )


async def auto_snapshot_job(context: ContextTypes.DEFAULT_TYPE) -> None:
    """Scheduled job: runs the snapshot pipeline and DMs admin when done.
    Does NOT auto-publish — admin must /publish manually.

    Skips while the schedule is paused (/pause_events) so a leftover open session
    can't get snapshotted during a paused week.
    """
    conn = _conn(context)
    if events_are_paused(conn, datetime.now(UTC)):
        logger.info("auto_snapshot: events paused; skipping")
        return
    result = take_snapshot(conn, _weights(), settings.MAX_ATTENDEES, settings.CALIBRATION_THRESHOLD)
    if result is None:
        logger.info("auto_snapshot: no active session with yes-RSVPs; skipping")
        return
    snap, cut = result
    if settings.ADMIN_TELEGRAM_ID == 0:
        logger.warning("auto_snapshot: ADMIN_TELEGRAM_ID unset; not DMing")
        return
    summary = _format_snapshot_summary(conn, snap, cut)
    try:
        await context.bot.send_message(
            chat_id=settings.ADMIN_TELEGRAM_ID,
            text=t("snapshot.auto_ran", summary=summary),
        )
    except TelegramError as exc:
        logger.warning("auto_snapshot: failed to DM admin: %s", exc)


@require_admin
async def handle_swap(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    message = update.effective_message
    if message is None:
        return
    if not context.args or len(context.args) < 2:
        await message.reply_text(t("snapshot.swap_usage"))
        return
    conn = _conn(context)
    sess = get_active_session(conn)
    if sess is None:
        await message.reply_text(t("snapshot.no_active"))
        return
    snap = get_snapshot(conn, sess.id)
    if snap is None:
        await message.reply_text(t("snapshot.no_snapshot_yet"))
        return

    player_a = _fetch_player_by_username(conn, context.args[0])
    player_b = _fetch_player_by_username(conn, context.args[1])
    if player_a is None or player_b is None:
        await message.reply_text(t("snapshot.usernames_not_roster"))
        return

    try:
        new_a, new_b = swap_players(
            snap.team_a, snap.team_b, player_a.telegram_id, player_b.telegram_id
        )
    except ValueError:
        await message.reply_text(t("snapshot.opposite_teams"))
        return

    new_metrics: TeamMetrics = compute_metrics(conn, new_a, new_b, _weights())
    update_teams(conn, sess.id, new_a, new_b, new_metrics)
    text = _format_teams(
        conn,
        Snapshot(
            session_id=sess.id,
            team_a=new_a,
            team_b=new_b,
            cut=snap.cut,
            metrics=new_metrics,
            created_at=snap.created_at,
        ),
        sess.session_date.isoformat(),
    )
    await message.reply_text(
        t("snapshot.swapped", a=player_a.display_name, b=player_b.display_name, text=text),
        parse_mode="Markdown",
    )


@require_admin
async def handle_publish(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    message = update.effective_message
    if message is None:
        return
    conn = _conn(context)
    sess = get_active_session(conn)
    if sess is None:
        await message.reply_text(t("snapshot.no_active"))
        return
    snap = get_snapshot(conn, sess.id)
    if snap is None:
        await message.reply_text(t("snapshot.no_snapshot_publish"))
        return
    if settings.GROUP_CHAT_ID == 0:
        await message.reply_text(t("snapshot.group_unset"))
        return

    text = _format_teams(conn, snap, sess.session_date.isoformat())
    attendance = _format_attendance(conn, snap)
    body = t(
        "snapshot.publish_body",
        date=sess.session_date.isoformat(),
        attendance=attendance,
        text=text,
    )
    try:
        await context.bot.send_message(
            chat_id=settings.GROUP_CHAT_ID,
            text=body,
            parse_mode="Markdown",
        )
    except TelegramError as exc:
        await message.reply_text(t("snapshot.publish_failed", err=exc))
        return

    write_attendance(conn, sess.id)
    set_session_status(conn, sess.id, "published")
    await message.reply_text(
        t("snapshot.published", sid=sess.id, n=len(snap.team_a) + len(snap.team_b))
    )
