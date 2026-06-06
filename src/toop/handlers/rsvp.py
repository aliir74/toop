from __future__ import annotations

import logging
import sqlite3

from telegram import Update
from telegram.ext import ContextTypes

from toop.admin import require_admin
from toop.handlers.roster import _pick_id, _player_keyboard, _safe_edit
from toop.players import list_active_players
from toop.rsvp import lock_in_player
from toop.sessions import get_active_session

logger = logging.getLogger(__name__)

LOCK_IN_USAGE = "Usage: /lock_in @username  (or /lock_in <telegram_id>)"
LOCKPICK_PREFIX = "lockpick:"


def _conn(context: ContextTypes.DEFAULT_TYPE) -> sqlite3.Connection:
    conn = context.bot_data.get("conn")
    if conn is None:
        raise RuntimeError("DB connection missing from bot_data")
    return conn


def _who_label(display_name: str | None, username: str | None, telegram_id: int) -> str:
    return display_name or (f"@{username}" if username else f"id {telegram_id}")


@require_admin
async def handle_lock_in(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Force a player's RSVP to yes. With no args, lists the active roster as
    buttons (only while a session is open); with a target, runs the one-shot."""
    message = update.effective_message
    if message is None:
        return
    conn = _conn(context)
    active = get_active_session(conn)
    if not context.args:
        if active is None:
            await message.reply_text("No active session to lock into.")
            return
        players = list_active_players(conn)
        if not players:
            await message.reply_text("Roster is empty — add players first.")
            return
        await message.reply_text(
            "Who do you want to lock in?",
            reply_markup=_player_keyboard(players, LOCKPICK_PREFIX),
        )
        return
    # Accept either a numeric telegram_id (for no-username players, mirroring
    # /add_player) or an @username handle.
    raw = context.args[0]
    if raw.isdigit():
        target: int | str = int(raw)
    else:
        username = raw.lstrip("@").lower()
        if not username:
            await message.reply_text(LOCK_IN_USAGE)
            return
        target = username

    if active is None:
        await message.reply_text("No active session to lock into.")
        return

    if isinstance(target, int):
        row = conn.execute(
            "SELECT telegram_id, display_name, username FROM players "
            "WHERE telegram_id=? AND active=1",
            (target,),
        ).fetchone()
        if row is None:
            await message.reply_text(
                f"id {target} isn't on the roster — add them with /add_player first."
            )
            return
    else:
        row = conn.execute(
            "SELECT telegram_id, display_name, username FROM players WHERE username=? AND active=1",
            (target,),
        ).fetchone()
        if row is None:
            await message.reply_text(f"@{target} isn't on the roster.")
            return

    telegram_id = row["telegram_id"]
    who = _who_label(row["display_name"], row["username"], telegram_id)
    if lock_in_player(conn, active.id, telegram_id):
        await message.reply_text(f"🔒 {who} locked into session #{active.id}.")
    else:
        await message.reply_text(f"Couldn't lock {who}.")


@require_admin
async def handle_lock_in_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Admin tapped a player button from /lock_in — force their RSVP to yes."""
    query = update.callback_query
    if query is None or query.data is None:
        return
    telegram_id = _pick_id(query.data, LOCKPICK_PREFIX)
    if telegram_id is None:
        await query.answer()
        return
    conn = _conn(context)
    active = get_active_session(conn)
    if active is None:
        await query.answer("No active session.", show_alert=True)
        return
    row = conn.execute(
        "SELECT display_name, username FROM players WHERE telegram_id=? AND active=1",
        (telegram_id,),
    ).fetchone()
    if row is None:
        await query.answer("That player is no longer on the roster.", show_alert=True)
        return
    who = _who_label(row["display_name"], row["username"], telegram_id)
    lock_in_player(conn, active.id, telegram_id)  # row verified active above
    await query.answer()
    await _safe_edit(query, f"🔒 {who} locked into session #{active.id}.")
