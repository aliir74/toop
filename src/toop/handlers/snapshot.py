from __future__ import annotations

import logging
import sqlite3

from telegram import Update
from telegram.error import TelegramError
from telegram.ext import ContextTypes

from toop.admin import require_admin
from toop.balance import (
    TeamMetrics,
    compute_metrics,
    generate_teams,
    swap_players,
)
from toop.config import settings
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

SWAP_USAGE = "Usage: /swap @player_a @player_b"


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


def _format_teams(conn: sqlite3.Connection, snap: Snapshot, session_date: str) -> str:
    a_names = [
        (_fetch_player(conn, pid) or Player(pid, None, f"#{pid}", True, True)).display_name
        for pid in snap.team_a
    ]
    b_names = [
        (_fetch_player(conn, pid) or Player(pid, None, f"#{pid}", True, True)).display_name
        for pid in snap.team_b
    ]
    rows = []
    for i in range(max(len(a_names), len(b_names))):
        left = a_names[i] if i < len(a_names) else ""
        right = b_names[i] if i < len(b_names) else ""
        rows.append(f"{left:<20} | {right}")
    metrics = snap.metrics
    table = "\n".join(rows)
    delta = metrics.abs_delta
    return (
        f"📅 *{session_date}* — proposed teams\n\n"
        f"```\n{'Team A':<20} | Team B\n{'-' * 20}-+-{'-' * 20}\n{table}\n```\n"
        f"Composite Δ: *{delta:.3f}* "
        f"(A={metrics.team_a_total:.2f}, B={metrics.team_b_total:.2f})\n"
        f"Calibration confidence: *{metrics.calibration_confidence}*"
    )


def _format_snapshot_summary(conn: sqlite3.Connection, snap: Snapshot, cut: list[int]) -> str:
    cut_note = ""
    if cut:
        cut_names = [
            (_fetch_player(conn, pid) or Player(pid, None, f"#{pid}", True, True)).display_name
            for pid in cut
        ]
        cut_note = "\n\nCut: " + ", ".join(cut_names)
    swap_note = " (setter swap applied)" if snap.metrics.setter_swap_applied else ""
    return (
        f"Snapshot saved for session #{snap.session_id}.{swap_note}\n"
        f"Preview with /teams, swap with /swap, ship with /publish.{cut_note}"
    )


@require_admin
async def handle_snapshot(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    message = update.effective_message
    if message is None:
        return
    conn = _conn(context)
    sess = get_active_session(conn)
    if sess is None:
        await message.reply_text("No active session. Open one with /open_session.")
        return
    result = take_snapshot(conn, _weights(), settings.MAX_ATTENDEES, settings.CALIBRATION_THRESHOLD)
    if result is None:
        await message.reply_text("No yes-RSVPs yet — nothing to snapshot.")
        return
    snap, cut = result
    await message.reply_text(_format_snapshot_summary(conn, snap, cut))


async def auto_snapshot_job(context: ContextTypes.DEFAULT_TYPE) -> None:
    """Scheduled job: runs the snapshot pipeline and DMs admin when done.
    Does NOT auto-publish — admin must /publish manually.
    """
    conn = _conn(context)
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
            text=f"⏰ Auto-snapshot ran.\n\n{summary}",
        )
    except TelegramError as exc:
        logger.warning("auto_snapshot: failed to DM admin: %s", exc)


@require_admin
async def handle_teams(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    message = update.effective_message
    if message is None:
        return
    conn = _conn(context)
    sess = get_active_session(conn)
    if sess is None:
        await message.reply_text("No active session.")
        return
    snap = get_snapshot(conn, sess.id)
    if snap is None:
        await message.reply_text("No snapshot yet. Run /snapshot first.")
        return
    text = _format_teams(conn, snap, sess.session_date.isoformat())
    await message.reply_text(text, parse_mode="Markdown")


@require_admin
async def handle_swap(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    message = update.effective_message
    if message is None:
        return
    if not context.args or len(context.args) < 2:
        await message.reply_text(SWAP_USAGE)
        return
    conn = _conn(context)
    sess = get_active_session(conn)
    if sess is None:
        await message.reply_text("No active session.")
        return
    snap = get_snapshot(conn, sess.id)
    if snap is None:
        await message.reply_text("No snapshot yet. Run /snapshot first.")
        return

    player_a = _fetch_player_by_username(conn, context.args[0])
    player_b = _fetch_player_by_username(conn, context.args[1])
    if player_a is None or player_b is None:
        await message.reply_text("One or both usernames aren't on the roster.")
        return

    try:
        new_a, new_b = swap_players(
            snap.team_a, snap.team_b, player_a.telegram_id, player_b.telegram_id
        )
    except ValueError:
        await message.reply_text("Both players must be on opposite teams to swap.")
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
        f"🔁 Swapped {player_a.display_name} ↔ {player_b.display_name}\n\n{text}",
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
        await message.reply_text("No active session.")
        return
    snap = get_snapshot(conn, sess.id)
    if snap is None:
        await message.reply_text("No snapshot to publish.")
        return
    if settings.GROUP_CHAT_ID == 0:
        await message.reply_text("GROUP_CHAT_ID is unset — can't publish to group.")
        return

    text = _format_teams(conn, snap, sess.session_date.isoformat())
    body = f"🏐 Teams for {sess.session_date.isoformat()}:\n\n{text}\n\nSee you on court! 🙌"
    try:
        await context.bot.send_message(
            chat_id=settings.GROUP_CHAT_ID,
            text=body,
            parse_mode="Markdown",
        )
    except TelegramError as exc:
        await message.reply_text(f"Failed to publish: {exc}")
        return

    write_attendance(conn, sess.id)
    set_session_status(conn, sess.id, "published")
    await message.reply_text(
        f"✅ Published session #{sess.id} and recorded {len(snap.team_a) + len(snap.team_b)} "
        f"attendance rows."
    )
