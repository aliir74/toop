from __future__ import annotations

import logging
import sqlite3
from datetime import date

from telegram import Update
from telegram.error import TelegramError
from telegram.ext import ContextTypes

from toop.admin import require_admin
from toop.config import settings
from toop.handlers.rsvp import rsvp_keyboard
from toop.rsvp import RsvpCounts, format_rsvp_message
from toop.sessions import (
    SessionStateError,
    close_session,
    list_recent_sessions,
    next_weekday,
    open_session,
)

logger = logging.getLogger(__name__)

OPEN_USAGE = "Usage: /open_session [YYYY-MM-DD]"


def _conn(context: ContextTypes.DEFAULT_TYPE) -> sqlite3.Connection:
    conn = context.bot_data.get("conn")
    if conn is None:
        raise RuntimeError("DB connection missing from bot_data")
    return conn


@require_admin
async def handle_open_session(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    message = update.effective_message
    if message is None:
        return
    if context.args:
        try:
            session_date = date.fromisoformat(context.args[0])
        except ValueError:
            await message.reply_text(OPEN_USAGE)
            return
    else:
        session_date = next_weekday(settings.SESSION_WEEKDAY)
    try:
        sess = open_session(_conn(context), session_date)
    except SessionStateError as exc:
        await message.reply_text(str(exc))
        return
    await message.reply_text(f"Session #{sess.id} opened for {sess.session_date.isoformat()}.")
    if settings.GROUP_CHAT_ID == 0:
        logger.warning("GROUP_CHAT_ID unset — skipping RSVP post")
        return
    rsvp_text = format_rsvp_message(sess.session_date.isoformat(), RsvpCounts(0, 0, 0))
    try:
        await context.bot.send_message(
            chat_id=settings.GROUP_CHAT_ID,
            text=rsvp_text,
            reply_markup=rsvp_keyboard(),
        )
    except TelegramError as exc:
        logger.warning("failed to post RSVP message: %s", exc)
        await message.reply_text(f"Couldn't post to the group chat: {exc}")


@require_admin
async def handle_close_session(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    message = update.effective_message
    if message is None:
        return
    try:
        sess = close_session(_conn(context))
    except SessionStateError as exc:
        await message.reply_text(str(exc))
        return
    await message.reply_text(f"Session #{sess.id} ({sess.session_date.isoformat()}) closed.")


@require_admin
async def handle_list_sessions(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    message = update.effective_message
    if message is None:
        return
    sessions_ = list_recent_sessions(_conn(context))
    if not sessions_:
        await message.reply_text("No sessions yet.")
        return
    lines = ["Recent sessions:"]
    for s in sessions_:
        lines.append(f"#{s.id} {s.session_date.isoformat()} — {s.status}")
    await message.reply_text("\n".join(lines))
