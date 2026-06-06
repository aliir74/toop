from __future__ import annotations

import logging
import sqlite3
from datetime import date

from telegram import Update
from telegram.ext import ContextTypes

from toop.admin import require_admin
from toop.config import settings
from toop.handlers.poll import post_attendance_poll
from toop.sessions import (
    list_recent_sessions,
    next_weekday,
    reopen_session,
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
    sess = reopen_session(_conn(context), session_date)
    await message.reply_text(f"Session #{sess.id} opened for {sess.session_date.isoformat()}.")
    await post_attendance_poll(context, _conn(context), sess)


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
