from __future__ import annotations

import logging
import sqlite3

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.error import BadRequest
from telegram.ext import ContextTypes

from toop.admin import require_admin
from toop.rsvp import (
    count_rsvps,
    format_rsvp_message,
    is_player_on_roster,
    lock_in_player,
    upsert_rsvp,
)
from toop.sessions import get_active_session

logger = logging.getLogger(__name__)

LOCK_IN_USAGE = "Usage: /lock_in @username  (or /lock_in <telegram_id>)"
CALLBACK_PREFIX = "rsvp:"


def _conn(context: ContextTypes.DEFAULT_TYPE) -> sqlite3.Connection:
    conn = context.bot_data.get("conn")
    if conn is None:
        raise RuntimeError("DB connection missing from bot_data")
    return conn


def rsvp_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton("✅ Yes", callback_data=f"{CALLBACK_PREFIX}yes"),
                InlineKeyboardButton("❌ No", callback_data=f"{CALLBACK_PREFIX}no"),
                InlineKeyboardButton("🤔 Maybe", callback_data=f"{CALLBACK_PREFIX}maybe"),
            ]
        ]
    )


async def handle_rsvp_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Process a tap on one of the RSVP buttons."""
    query = update.callback_query
    if query is None or query.data is None or query.from_user is None:
        return
    status = query.data.removeprefix(CALLBACK_PREFIX)
    if status not in ("yes", "no", "maybe"):
        await query.answer()
        return

    conn = _conn(context)
    active = get_active_session(conn)
    if active is None:
        await query.answer("No active session.", show_alert=True)
        return

    voter_id = query.from_user.id
    if not is_player_on_roster(conn, voter_id):
        await query.answer("You're not on the roster — ask the admin to add you.", show_alert=True)
        return

    upsert_rsvp(conn, active.id, voter_id, status)
    counts = count_rsvps(conn, active.id)
    new_text = format_rsvp_message(active.session_date.isoformat(), counts)
    await query.answer(f"You're in: {status}.")
    try:
        await query.edit_message_text(text=new_text, reply_markup=rsvp_keyboard())
    except BadRequest as exc:
        if "not modified" not in str(exc).lower():
            logger.warning("failed to edit rsvp message: %s", exc)


@require_admin
async def handle_lock_in(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    message = update.effective_message
    if message is None or not context.args:
        if message is not None:
            await message.reply_text(LOCK_IN_USAGE)
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

    conn = _conn(context)
    active = get_active_session(conn)
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
    who = row["display_name"] or (f"@{row['username']}" if row["username"] else f"id {telegram_id}")
    if lock_in_player(conn, active.id, telegram_id):
        await message.reply_text(f"🔒 {who} locked into session #{active.id}.")
    else:
        await message.reply_text(f"Couldn't lock {who}.")
