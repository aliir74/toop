from __future__ import annotations

import logging
import shlex
import sqlite3

from telegram import Update
from telegram.error import BadRequest
from telegram.ext import ContextTypes

from toop.admin import require_admin
from toop.contacts import get_contact, list_contacts
from toop.players import (
    add_player,
    list_active_players,
    soft_remove_player,
)
from toop.voting_queue import bootstrap_calibration_prompts

logger = logging.getLogger(__name__)

ADD_USAGE = (
    'Usage: /add_player @username "Display Name"  (or /add_player <telegram_id> "Display Name")'
)
REMOVE_USAGE = "Usage: /remove_player @username"


def _conn(context: ContextTypes.DEFAULT_TYPE) -> sqlite3.Connection:
    conn = context.bot_data.get("conn")
    if conn is None:
        raise RuntimeError("DB connection missing from bot_data")
    return conn


def _parse_add_args(text: str) -> tuple[int | str, str] | None:
    """Parse `/add_player <@username|telegram_id> "Display Name"`.

    Returns (identifier, display_name) where identifier is an int telegram_id
    when the first arg is all-digits, otherwise the normalized username str.
    """
    try:
        tokens = shlex.split(text)
    except ValueError:
        return None
    if len(tokens) < 3:
        return None
    raw = tokens[1]
    display_name = " ".join(tokens[2:])
    if not display_name:
        return None
    if raw.isdigit():
        return int(raw), display_name
    username = raw.lstrip("@").lower()
    if not username:
        return None
    return username, display_name


async def _resolve_telegram_id(context: ContextTypes.DEFAULT_TYPE, username: str) -> int | None:
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
    identifier, display_name = parsed
    conn = _conn(context)
    if isinstance(identifier, int):
        # Add-by-id: the contacts row proves we can DM them later for voting.
        contact = get_contact(conn, identifier)
        if contact is None:
            await message.reply_text(
                f"That user (id {identifier}) hasn't DM'd the bot yet — they must "
                "DM /start first so I can message them for voting."
            )
            return
        telegram_id = identifier
        username = contact.username  # may be None — that's fine.
    else:
        username = identifier
        resolved = await _resolve_telegram_id(context, username)
        if resolved is None:
            await message.reply_text(
                f"Couldn't find @{username}. Ask them to DM me /start, then try again. "
                "If they have no Telegram username, run /contacts and use "
                '/add_player <id> "Name" instead.'
            )
            return
        telegram_id = resolved
    existed = conn.execute(
        "SELECT active FROM players WHERE telegram_id=?", (telegram_id,)
    ).fetchone()
    is_new = existed is None
    player = add_player(conn, telegram_id, display_name, username)
    if is_new:
        inserted = bootstrap_calibration_prompts(conn, telegram_id)
        suffix = f" Seeded {inserted} calibration prompts." if inserted else ""
    else:
        was_inactive = existed is not None and existed["active"] == 0
        suffix = " (revived from soft-delete)" if was_inactive else ""
    handle = f"@{player.username}" if player.username else "(no username)"
    await message.reply_text(f"Added {player.display_name} {handle} — calibrating.{suffix}")


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


@require_admin
async def handle_contacts(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """List everyone who has DM'd the bot, flagging who is not yet on the roster.

    These are the people /add_player can resolve (Telegram only resolves a
    @handle once they've messaged the bot). Contacts are a standalone presence
    log — never joined to vote data.
    """
    message = update.effective_message
    if message is None:
        return
    conn = _conn(context)
    contacts = list_contacts(conn)
    if not contacts:
        await message.reply_text("Nobody has DM'd me yet. Ask people to send /start.")
        return
    roster_ids = {
        row["telegram_id"] for row in conn.execute("SELECT telegram_id FROM players").fetchall()
    }
    lines = ["Contacts (people who've DM'd me):"]
    addable = 0
    for i, c in enumerate(contacts, start=1):
        handle = f"@{c.username}" if c.username else "(no username)"
        name = c.display_name or "?"
        first_seen = c.first_seen_at[:10] if c.first_seen_at else "?"
        if c.telegram_id in roster_ids:
            lines.append(f"{i}. {handle} ({name}) — first seen {first_seen}")
        else:
            addable += 1
            lines.append(f"{i}. {handle} ({name}) — first seen {first_seen}  🆕 not on roster")
            # Ready-to-copy command — works even when the contact has no @username.
            copy_name = c.display_name or c.username or "Player"
            lines.append(f'   /add_player {c.telegram_id} "{copy_name}"')
    if addable:
        lines.append(f"\n🆕 = available to /add_player ({addable} not yet on the roster).")
    await message.reply_text("\n".join(lines))
