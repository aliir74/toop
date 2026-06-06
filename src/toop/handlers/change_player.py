from __future__ import annotations

import sqlite3

from telegram import (
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
    Update,
)
from telegram.constants import ChatType
from telegram.ext import ContextTypes

from toop.admin import require_admin
from toop.config import settings
from toop.handlers.roster import _pick_id, _safe_edit
from toop.handlers.snapshot import (
    _format_attendance,
    _format_teams,
    _weights,
    take_snapshot,
)
from toop.i18n import t
from toop.poll import list_waitlist, remove_from_waitlist
from toop.rsvp import lock_in_player, upsert_rsvp
from toop.sessions import Session, get_active_session
from toop.snapshots import Snapshot, get_snapshot

CP_REMOVE_PREFIX = "cprm:"
CP_PROMOTE_PREFIX = "cpadd:"


def _conn(context: ContextTypes.DEFAULT_TYPE) -> sqlite3.Connection:
    conn = context.bot_data.get("conn")
    if conn is None:
        raise RuntimeError("DB connection missing from bot_data")
    return conn


def _label(conn: sqlite3.Connection, telegram_id: int) -> str:
    row = conn.execute(
        "SELECT display_name FROM players WHERE telegram_id=?", (telegram_id,)
    ).fetchone()
    return row["display_name"] if row is not None else f"#{telegram_id}"


def _resolve_target(conn: sqlite3.Connection, raw: str) -> int | None:
    """Resolve an @username or numeric id to an active roster telegram_id."""
    if raw.isdigit():
        row = conn.execute(
            "SELECT telegram_id FROM players WHERE telegram_id=? AND active=1", (int(raw),)
        ).fetchone()
    else:
        handle = raw.lstrip("@").lower()
        if not handle:
            return None
        row = conn.execute(
            "SELECT telegram_id FROM players WHERE username=? AND active=1", (handle,)
        ).fetchone()
    return row["telegram_id"] if row is not None else None


def _apply_add(conn: sqlite3.Connection, session_id: int, telegram_id: int) -> None:
    """Force the player into the attendee set and drop them off the waitlist."""
    lock_in_player(conn, session_id, telegram_id)
    remove_from_waitlist(conn, session_id, telegram_id)


def _apply_remove(conn: sqlite3.Connection, session_id: int, telegram_id: int) -> None:
    upsert_rsvp(conn, session_id, telegram_id, "no")


def _teams_message(conn: sqlite3.Connection, snap: Snapshot, session_date: str) -> str:
    return f"{_format_attendance(conn, snap)}\n\n{_format_teams(conn, snap, session_date)}"


def _change_keyboard(
    conn: sqlite3.Connection, snap: Snapshot, session_id: int
) -> InlineKeyboardMarkup:
    rows: list[list[InlineKeyboardButton]] = []
    for pid in snap.team_a + snap.team_b:
        rows.append(
            [
                InlineKeyboardButton(
                    t("change.btn_remove", name=_label(conn, pid)),
                    callback_data=f"{CP_REMOVE_PREFIX}{pid}",
                )
            ]
        )
    for pid in list_waitlist(conn, session_id):
        rows.append(
            [
                InlineKeyboardButton(
                    t("change.btn_promote", name=_label(conn, pid)),
                    callback_data=f"{CP_PROMOTE_PREFIX}{pid}",
                )
            ]
        )
    return InlineKeyboardMarkup(rows)


@require_admin
async def handle_change_player(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Add / remove an attendee and rebalance. DM-only, admin-only.

    With no args, lists current attendees (➖ remove) and the waitlist (⬆️
    promote) as buttons; with +@user / -@user, runs the one-shot.
    """
    message = update.effective_message
    if message is None:
        return
    chat = update.effective_chat
    if chat is not None and chat.type != ChatType.PRIVATE:
        await message.reply_text(t("change.dm_only"))
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
    if not context.args:
        await message.reply_text(
            t("change.pick_prompt"),
            reply_markup=_change_keyboard(conn, snap, sess.id),
        )
        return
    token = context.args[0]
    if token[:1] not in ("+", "-"):
        await message.reply_text(t("change.usage"))
        return
    target = _resolve_target(conn, token[1:])
    if target is None:
        await message.reply_text(t("change.not_found", target=token[1:]))
        return
    if token[0] == "+":
        _apply_add(conn, sess.id, target)
    else:
        _apply_remove(conn, sess.id, target)
    await _rebalance_and_reply(message, conn, sess)


async def _rebalance_and_reply(message: Message, conn: sqlite3.Connection, sess: Session) -> None:
    """Re-run the snapshot pipeline and reply with the fresh teams."""
    result = take_snapshot(conn, _weights(), settings.MAX_ATTENDEES, settings.CALIBRATION_THRESHOLD)
    if result is None:
        await message.reply_text(t("change.none_left"))
        return
    snap, _cut = result
    await message.reply_text(
        _teams_message(conn, snap, sess.session_date.isoformat()), parse_mode="Markdown"
    )


async def _change_callback(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    prefix: str,
    add: bool,
) -> None:
    query = update.callback_query
    if query is None or query.data is None:
        return
    telegram_id = _pick_id(query.data, prefix)
    if telegram_id is None:
        await query.answer()
        return
    conn = _conn(context)
    sess = get_active_session(conn)
    if sess is None:
        await query.answer(t("snapshot.no_active"), show_alert=True)
        return
    if add:
        _apply_add(conn, sess.id, telegram_id)
    else:
        _apply_remove(conn, sess.id, telegram_id)
    await query.answer()
    await _rebalance_and_edit(query, conn, sess)


async def _rebalance_and_edit(
    query: CallbackQuery, conn: sqlite3.Connection, sess: Session
) -> None:
    result = take_snapshot(conn, _weights(), settings.MAX_ATTENDEES, settings.CALIBRATION_THRESHOLD)
    if result is None:
        await _safe_edit(query, t("change.none_left"))
        return
    snap, _cut = result
    await _safe_edit(query, _teams_message(conn, snap, sess.session_date.isoformat()))


@require_admin
async def handle_change_remove_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await _change_callback(update, context, CP_REMOVE_PREFIX, add=False)


@require_admin
async def handle_change_promote_callback(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
    await _change_callback(update, context, CP_PROMOTE_PREFIX, add=True)
