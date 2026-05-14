from __future__ import annotations

import logging
import shlex
import sqlite3

from telegram import Update
from telegram.error import BadRequest
from telegram.ext import ContextTypes

from toop.admin import require_admin
from toop.players import (
    add_player,
    list_active_players,
    soft_remove_player,
)

logger = logging.getLogger(__name__)

ADD_USAGE = 'Usage: /add_player @username "Display Name"'
REMOVE_USAGE = "Usage: /remove_player @username"


def _conn(context: ContextTypes.DEFAULT_TYPE) -> sqlite3.Connection:
    conn = context.bot_data.get("conn")
    if conn is None:
        raise RuntimeError("DB connection missing from bot_data")
    return conn


def _parse_add_args(text: str) -> tuple[str, str] | None:
    """Parse `/add_player @username "Display Name"` → (username, display_name)."""
    try:
        tokens = shlex.split(text)
    except ValueError:
        return None
    if len(tokens) < 3:
        return None
    username = tokens[1].lstrip("@").lower()
    display_name = " ".join(tokens[2:])
    if not username or not display_name:
        return None
    return username, display_name


async def _resolve_telegram_id(
    context: ContextTypes.DEFAULT_TYPE, username: str
) -> int | None:
    try:
        chat = await context.bot.get_chat(f"@{username}")
    except BadRequest:
        return None
    return chat.id


@require_admin
async def handle_add_player(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    message = update.effective_message
    if message is None or message.text is None:
        return
    parsed = _parse_add_args(message.text)
    if parsed is None:
        await message.reply_text(ADD_USAGE)
        return
    username, display_name = parsed
    telegram_id = await _resolve_telegram_id(context, username)
    if telegram_id is None:
        await message.reply_text(
            f"Couldn't find @{username}. Ask them to DM me /start, then try again."
        )
        return
    player = add_player(_conn(context), telegram_id, display_name, username)
    await message.reply_text(
        f"Added {player.display_name} (@{player.username}) — calibrating."
    )


@require_admin
async def handle_remove_player(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    message = update.effective_message
    if message is None or not context.args:
        if message is not None:
            await message.reply_text(REMOVE_USAGE)
        return
    username = context.args[0].lstrip("@").lower()
    if not username:
        await message.reply_text(REMOVE_USAGE)
        return
    telegram_id = await _resolve_telegram_id(context, username)
    if telegram_id is None:
        await message.reply_text(f"Couldn't find @{username}.")
        return
    if soft_remove_player(_conn(context), telegram_id):
        await message.reply_text(f"Removed @{username}.")
    else:
        await message.reply_text(f"@{username} wasn't in the active roster.")


@require_admin
async def handle_list_players(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    message = update.effective_message
    if message is None:
        return
    players = list_active_players(_conn(context))
    if not players:
        await message.reply_text("Roster is empty. Use /add_player to start.")
        return
    lines = ["Roster:"]
    for i, p in enumerate(players, start=1):
        marker = "🟡 calibrating" if p.is_calibrating else "✅"
        handle = f"@{p.username}" if p.username else "(no username)"
        lines.append(f"{i}. {p.display_name} {handle} — {marker}")
    await message.reply_text("\n".join(lines))
