from __future__ import annotations

import logging
import sqlite3
from datetime import UTC, datetime

from telegram import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.error import BadRequest
from telegram.ext import ContextTypes

from toop.admin import require_admin
from toop.durations import parse_duration
from toop.i18n import t
from toop.pause import (
    clear_events_pause,
    events_are_paused,
    events_paused_until,
    pause_events_until,
)

logger = logging.getLogger(__name__)

EVPAUSEDUR_PREFIX = "evpausedur:"

# Durations offered by /pause_events when called with no argument. Each token
# round-trips through parse_duration so the typed fallback stays in sync.
_PAUSE_DURATIONS: tuple[tuple[str, str], ...] = (
    ("roster.dur_1week", "1w"),
    ("roster.dur_2weeks", "2w"),
    ("roster.dur_1month", "1m"),
)


def _conn(context: ContextTypes.DEFAULT_TYPE) -> sqlite3.Connection:
    conn = context.bot_data.get("conn")
    if conn is None:
        raise RuntimeError("DB connection missing from bot_data")
    return conn


def _duration_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [InlineKeyboardButton(t(label), callback_data=f"{EVPAUSEDUR_PREFIX}{token}")]
            for label, token in _PAUSE_DURATIONS
        ]
    )


async def _safe_edit(query: CallbackQuery, text: str) -> None:
    """Edit a callback's message, swallowing the BadRequest Telegram raises when
    the message is unchanged or too old to edit."""
    try:
        await query.edit_message_text(text)
    except BadRequest as exc:  # pragma: no cover - only on stale/identical message
        logger.warning("failed to edit message: %s", exc)


@require_admin
async def handle_pause_events(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Pause the weekly schedule for a window. With a duration argument
    (``/pause_events 2w``) it applies immediately; with no argument it offers
    duration buttons. While paused, no attendance poll or session is created."""
    message = update.effective_message
    if message is None:
        return
    conn = _conn(context)
    if context.args:
        delta = parse_duration(context.args[0])
        if delta is None:
            await message.reply_text(t("events.pause_usage"))
            return
        until = datetime.now(UTC) + delta
        pause_events_until(conn, until)
        await message.reply_text(t("events.paused_until", date=f"{until:%Y-%m-%d}"))
        return
    prompt = t("events.how_long_pause")
    paused_until = events_paused_until(conn)
    if paused_until is not None and paused_until > datetime.now(UTC):
        status = t("events.currently_paused", date=f"{paused_until:%Y-%m-%d}")
        prompt = f"{status}\n{prompt}"
    await message.reply_text(prompt, reply_markup=_duration_keyboard())


@require_admin
async def handle_pause_events_dur_callback(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
    """Admin tapped a duration button from /pause_events — apply the pause."""
    query = update.callback_query
    if query is None or query.data is None:
        return
    delta = parse_duration(query.data.removeprefix(EVPAUSEDUR_PREFIX))
    if delta is None:  # stale/forged callback — token isn't a real duration
        await query.answer()
        return
    conn = _conn(context)
    until = datetime.now(UTC) + delta
    pause_events_until(conn, until)
    await query.answer()
    await _safe_edit(query, t("events.paused_until", date=f"{until:%Y-%m-%d}"))


@require_admin
async def handle_resume_events(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Lift an active schedule pause so the weekly poll resumes."""
    message = update.effective_message
    if message is None:
        return
    conn = _conn(context)
    if events_are_paused(conn, datetime.now(UTC)):
        clear_events_pause(conn)
        await message.reply_text(t("events.resumed"))
    else:
        await message.reply_text(t("events.not_paused"))
