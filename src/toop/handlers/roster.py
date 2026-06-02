from __future__ import annotations

import logging
import re
import shlex
import sqlite3
from collections.abc import Callable
from datetime import UTC, datetime, timedelta

from telegram import (
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
    Update,
)
from telegram.constants import ChatType
from telegram.error import BadRequest
from telegram.ext import ContextTypes

from toop.admin import require_admin
from toop.contacts import get_contact, list_contacts
from toop.players import (
    Player,
    add_ghost_player,
    add_player,
    disable_player_pool,
    dont_know_stats,
    enable_player_pool,
    get_player_by_username,
    link_ghost_player,
    list_active_players,
    pause_player_pool,
    rename_player,
    soft_remove_player,
)
from toop.voting_queue import bootstrap_calibration_prompts

logger = logging.getLogger(__name__)

ADD_USAGE = (
    'Usage: /add_player @username "Display Name"  (or /add_player <telegram_id> "Display Name")'
)
REMOVE_USAGE = "Usage: /remove_player @username"
PAUSE_USAGE = "Usage: /pause_voting <@username|telegram_id> <duration like 2w or 10d>"
DISABLE_USAGE = "Usage: /disable_voting <@username|telegram_id>"
ENABLE_USAGE = "Usage: /enable_voting <@username|telegram_id>"
ADD_GHOST_USAGE = 'Usage: /add_ghost "Display Name"'
LINK_USAGE = "Usage: /link_player <ghost_id> <@username|real_telegram_id>"
RENAME_PREFIX = "rename:"
RENAME_USAGE = 'Usage: /rename (no args) for buttons, or /rename <@username|telegram_id> "New Name"'
RENAME_EMPTY_ROSTER = "No players on the roster yet — use /add_player first."
PENDING_RENAME_KEY = "pending_rename"

# Callback prefixes for the button-driven admin flows. Kept short because
# callback_data is capped at 64 bytes; only telegram_ids ride behind them.
RMPICK_PREFIX = "rmpick:"
DISPICK_PREFIX = "dispick:"
ENPICK_PREFIX = "enpick:"
PAUSEPICK_PREFIX = "pausepick:"
PAUSEDUR_PREFIX = "pausedur:"
LNKGHOST_PREFIX = "lnkghost:"
LNKREAL_PREFIX = "lnkreal:"
ADDPICK_PREFIX = "addpick:"
PENDING_ADD_KEY = "pending_add"

# Button durations offered by /pause_voting after a player is picked — each
# token round-trips through _parse_duration so the typed fallback stays in sync.
PAUSE_DURATIONS: tuple[tuple[str, str], ...] = (
    ("1 week", "1w"),
    ("2 weeks", "2w"),
    ("1 month", "1m"),
)


def _conn(context: ContextTypes.DEFAULT_TYPE) -> sqlite3.Connection:
    conn = context.bot_data.get("conn")
    if conn is None:
        raise RuntimeError("DB connection missing from bot_data")
    return conn


def _player_label(display_name: str, username: str | None) -> str:
    return f"{display_name} (@{username})" if username else display_name


def _player_keyboard(players: list[Player], prefix: str) -> InlineKeyboardMarkup:
    """One button per player, callback_data = prefix + telegram_id."""
    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton(
                    _player_label(p.display_name, p.username),
                    callback_data=f"{prefix}{p.telegram_id}",
                )
            ]
            for p in players
        ]
    )


def _pick_id(data: str, prefix: str) -> int | None:
    """Recover the telegram_id (possibly a negative ghost id) from callback_data,
    or None when the trailing token isn't an int (stale/forged data)."""
    try:
        return int(data.removeprefix(prefix))
    except ValueError:
        return None


async def _safe_edit(
    query: CallbackQuery, text: str, reply_markup: InlineKeyboardMarkup | None = None
) -> None:
    """Edit a callback's message, swallowing the BadRequest Telegram raises when
    the message is unchanged or too old to edit."""
    try:
        await query.edit_message_text(text, reply_markup=reply_markup)
    except BadRequest as exc:  # pragma: no cover - only on stale/identical message
        logger.warning("failed to edit message: %s", exc)


async def _single_pick_action(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    prefix: str,
    action: Callable[[sqlite3.Connection, int], object],
    success: Callable[[str], str],
) -> None:
    """Shared body for one-tap player-pick callbacks (remove/disable/enable):
    resolve the id, look up the active player, run `action`, then edit the
    message with `success(display_name)`. Alerts when the player has gone."""
    query = update.callback_query
    if query is None or query.data is None:
        return
    telegram_id = _pick_id(query.data, prefix)
    if telegram_id is None:
        await query.answer()
        return
    conn = _conn(context)
    row = conn.execute(
        "SELECT display_name FROM players WHERE telegram_id=? AND active=1",
        (telegram_id,),
    ).fetchone()
    if row is None:
        await query.answer("That player is no longer on the roster.", show_alert=True)
        return
    action(conn, telegram_id)
    await query.answer()
    await _safe_edit(query, success(row["display_name"]))


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
    """Soft-remove a player. With no args, lists the active roster as buttons;
    with a @username, runs the typed one-shot (resolves the handle via Telegram)."""
    message = update.effective_message
    if message is None:
        return
    conn = _conn(context)
    if not context.args:
        players = list_active_players(conn)
        if not players:
            await message.reply_text("Roster is empty — nobody to remove.")
            return
        await message.reply_text(
            "Who do you want to remove?",
            reply_markup=_player_keyboard(players, RMPICK_PREFIX),
        )
        return
    username = context.args[0].lstrip("@").lower()
    if not username:
        await message.reply_text(REMOVE_USAGE)
        return
    telegram_id = await _resolve_telegram_id(context, username)
    if telegram_id is None:
        await message.reply_text(f"Couldn't find @{username}.")
        return
    if soft_remove_player(conn, telegram_id):
        await message.reply_text(f"Removed @{username}.")
    else:
        await message.reply_text(f"@{username} wasn't in the active roster.")


@require_admin
async def handle_remove_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Admin tapped a player button from /remove_player — soft-remove them."""
    await _single_pick_action(
        update,
        context,
        RMPICK_PREFIX,
        soft_remove_player,
        lambda name: f"Removed {name}. ✅",
    )


_DURATION_DAYS = {"d": 1, "w": 7, "m": 30}


def _parse_duration(token: str) -> timedelta | None:
    """Parse a pause duration like ``2w``, ``10d`` or ``1m`` into a timedelta.

    ``m`` is a coarse month (30 days) — exact enough for a "pull them for a
    month" pause. Returns None when the token doesn't match.
    """
    match = re.fullmatch(r"(\d+)([dwm])", token.lower())
    if not match:
        return None
    amount = int(match.group(1))
    return timedelta(days=amount * _DURATION_DAYS[match.group(2)])


def _is_paused(pool_paused_until: str | None, now: datetime) -> bool:
    """True when a timed pause is set and still in the future (UTC)."""
    if pool_paused_until is None:
        return False
    until = datetime.strptime(pool_paused_until, "%Y-%m-%d %H:%M:%S").replace(tzinfo=UTC)
    return until > now


def _resolve_pool_target(conn: sqlite3.Connection, token: str) -> int | None:
    """Resolve a roster player from a digit id (incl. negative ghost ids) or a
    @username already on the roster. No network call — pool targets are on-roster."""
    raw = token.lstrip("@")
    if raw.lstrip("-").isdigit():
        return int(raw)
    player = get_player_by_username(conn, raw)
    return player.telegram_id if player else None


@require_admin
async def handle_pause_voting(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Temporarily pull a player from the rating pool. Others stop being asked to
    rate them until the timer expires; the player can still vote on others."""
    message = update.effective_message
    if message is None:
        return
    if len(context.args) < 2:
        await message.reply_text(PAUSE_USAGE)
        return
    delta = _parse_duration(context.args[1])
    if delta is None:
        await message.reply_text(PAUSE_USAGE)
        return
    conn = _conn(context)
    target = _resolve_pool_target(conn, context.args[0])
    until = datetime.now(UTC) + delta
    if target is not None and pause_player_pool(conn, target, until):
        await message.reply_text(
            f"Paused {context.args[0]} until {until:%Y-%m-%d} — others won't be asked to "
            "rate them, but they can still vote. /enable_voting to undo early."
        )
    else:
        await message.reply_text(f"Couldn't find {context.args[0]} on the active roster.")


@require_admin
async def handle_disable_voting(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Pull a player from the rating pool indefinitely (until /enable_voting).

    With no args, lists the active roster as buttons; with a target, runs the
    typed one-shot."""
    message = update.effective_message
    if message is None:
        return
    conn = _conn(context)
    if not context.args:
        players = list_active_players(conn)
        if not players:
            await message.reply_text("Roster is empty — nobody to disable.")
            return
        await message.reply_text(
            "Who do you want to pull from the rating pool?",
            reply_markup=_player_keyboard(players, DISPICK_PREFIX),
        )
        return
    target = _resolve_pool_target(conn, context.args[0])
    if target is not None and disable_player_pool(conn, target):
        await message.reply_text(
            f"Disabled {context.args[0]} from the rating pool — others won't be asked to "
            "rate them. They can still vote. /enable_voting to restore."
        )
    else:
        await message.reply_text(f"Couldn't find {context.args[0]} on the active roster.")


@require_admin
async def handle_disable_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Admin tapped a player button from /disable_voting — pull them from the pool."""
    await _single_pick_action(
        update,
        context,
        DISPICK_PREFIX,
        disable_player_pool,
        lambda name: f"Disabled {name} from the rating pool 🚫 — /enable_voting to restore.",
    )


@require_admin
async def handle_enable_voting(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Restore a player to the rating pool, clearing any disable AND any pause.

    With no args, lists ONLY players currently disabled or actively paused as
    buttons — the whole roster would bury the few that are actually restorable.
    With a target, runs the typed one-shot."""
    message = update.effective_message
    if message is None:
        return
    conn = _conn(context)
    if not context.args:
        now = datetime.now(UTC)
        players = [
            p
            for p in list_active_players(conn)
            if not p.in_pool or _is_paused(p.pool_paused_until, now)
        ]
        if not players:
            await message.reply_text("Nobody is paused or disabled right now. ✅")
            return
        await message.reply_text(
            "Who do you want to restore to the rating pool?",
            reply_markup=_player_keyboard(players, ENPICK_PREFIX),
        )
        return
    target = _resolve_pool_target(conn, context.args[0])
    if target is not None and enable_player_pool(conn, target):
        await message.reply_text(f"Restored {context.args[0]} to the rating pool. ✅")
    else:
        await message.reply_text(f"Couldn't find {context.args[0]} on the active roster.")


@require_admin
async def handle_enable_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Admin tapped a player button from /enable_voting — restore them to the pool."""
    await _single_pick_action(
        update,
        context,
        ENPICK_PREFIX,
        enable_player_pool,
        lambda name: f"Restored {name} to the rating pool. ✅",
    )


@require_admin
async def handle_add_ghost(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Add an accountless player others can vote on before they join Telegram."""
    message = update.effective_message
    if message is None or message.text is None:
        return
    try:
        tokens = shlex.split(message.text)
    except ValueError:
        await message.reply_text(ADD_GHOST_USAGE)
        return
    name = " ".join(tokens[1:]).strip()
    if not name:
        await message.reply_text(ADD_GHOST_USAGE)
        return
    conn = _conn(context)
    ghost = add_ghost_player(conn, name)
    seeded = bootstrap_calibration_prompts(conn, ghost.telegram_id)
    suffix = f" Seeded {seeded} calibration prompts." if seeded else ""
    await message.reply_text(
        f"👻 Added ghost {ghost.display_name} (id {ghost.telegram_id}).{suffix} "
        f"When they join Telegram, run /link_player {ghost.telegram_id} @their_username."
    )


@require_admin
async def handle_link_player(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Merge a ghost player into a real account once that person joins Telegram.

    The real account must have DM'd the bot (contacts row) so we can message them
    for voting — the same DM-ability rule as /add_player by id.
    """
    message = update.effective_message
    if message is None:
        return
    if len(context.args) < 2:
        await message.reply_text(LINK_USAGE)
        return
    conn = _conn(context)
    ghost_token = context.args[0]
    if not ghost_token.lstrip("-").isdigit():
        await message.reply_text(LINK_USAGE)
        return
    ghost_id = int(ghost_token)
    ghost_row = conn.execute(
        "SELECT display_name, is_ghost FROM players WHERE telegram_id=?", (ghost_id,)
    ).fetchone()
    if ghost_row is None or ghost_row["is_ghost"] != 1:
        await message.reply_text(
            f"{ghost_id} isn't a ghost player. Run /list_players and use a 👻 id."
        )
        return

    real_token = context.args[1]
    if real_token.isdigit():
        real_id = int(real_token)
    else:
        username = real_token.lstrip("@").lower()
        resolved = await _resolve_telegram_id(context, username)
        if resolved is None:
            await message.reply_text(f"Couldn't find @{username}. Ask them to DM me /start first.")
            return
        real_id = resolved

    contact = get_contact(conn, real_id)
    if contact is None:
        await message.reply_text(
            f"That user (id {real_id}) hasn't DM'd the bot yet — they must /start so "
            "I can message them for voting."
        )
        return
    result = link_ghost_player(
        conn,
        ghost_id,
        real_id,
        contact.username,
        contact.display_name or ghost_row["display_name"],
    )
    await message.reply_text(
        f"🔗 Linked ghost {ghost_row['display_name']} → id {real_id}. Moved "
        f"{result.vote_rows} vote pairs, {result.ratings} ratings, {result.rsvps} RSVPs, "
        f"{result.attendance} attendance rows."
    )


@require_admin
async def handle_dk_report(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Show each active player's don't-know rate, highest first — the head of the
    list is who the group can least confidently rate (a pause candidate)."""
    message = update.effective_message
    if message is None:
        return
    stats = dont_know_stats(_conn(context))
    if not stats:
        await message.reply_text("No players on the roster yet.")
        return
    lines = ["🤷 Don't-know report (highest rate first):"]
    for i, s in enumerate(stats, start=1):
        pct = round(s.dk_rate * 100)
        lines.append(f"{i}. {s.display_name} — {s.dk_count}/{s.total} don't-know ({pct}%)")
    await message.reply_text("\n".join(lines))


@require_admin
async def handle_list_players(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    message = update.effective_message
    if message is None:
        return
    players = list_active_players(_conn(context))
    if not players:
        await message.reply_text("Roster is empty. Use /add_player to start.")
        return
    now = datetime.now(UTC)
    lines = ["Roster:"]
    for i, p in enumerate(players, start=1):
        marker = "🟡 calibrating" if p.is_calibrating else "✅"
        if p.is_ghost:
            handle = "👻 ghost"
        elif p.username:
            handle = f"@{p.username}"
        else:
            handle = "(no username)"
        tags = ""
        if not p.in_pool:
            tags = " — 🚫 voting disabled"
        elif _is_paused(p.pool_paused_until, now):
            tags = " — ⏸ voting paused"
        lines.append(f"{i}. {p.display_name} {handle} — {marker}{tags}")
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


# ----- /rename: pick a player, then type the new display name -----


def _parse_rename_args(text: str) -> tuple[int | str, str] | None:
    """Parse the one-shot shortcut `/rename <@username|telegram_id> "New Name"`.

    Returns (identifier, new_name) where identifier is an int telegram_id when
    the first arg is all-digits, otherwise the normalized username str.
    """
    try:
        tokens = shlex.split(text)
    except ValueError:
        return None
    if len(tokens) < 3:
        return None
    raw = tokens[1]
    new_name = " ".join(tokens[2:]).strip()
    if not new_name:
        return None
    if raw.isdigit():
        return int(raw), new_name
    username = raw.lstrip("@").lower()
    if not username:
        return None
    return username, new_name


async def _rename_one_shot(
    message: Message, conn: sqlite3.Connection, identifier: int | str, new_name: str
) -> None:
    """Resolve a roster player from the shortcut identifier and rename in place."""
    if isinstance(identifier, int):
        old_name = rename_player(conn, identifier, new_name)
        if old_name is None:
            await message.reply_text(f"No active player with id {identifier}.")
            return
    else:
        player = get_player_by_username(conn, identifier)
        if player is None:
            await message.reply_text(f"@{identifier} isn't on the active roster.")
            return
        old_name = rename_player(conn, player.telegram_id, new_name)
        if old_name is None:  # pragma: no cover - racey soft-delete between lookup and update
            await message.reply_text(f"@{identifier} isn't on the active roster.")
            return
    await message.reply_text(f"Renamed {old_name} → {new_name} ✅")


@require_admin
async def handle_rename(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Rename a player's display_name (DM-only, admin-only).

    With no args, lists active players as inline buttons and waits for the admin
    to type the new name. With args, runs the one-shot shortcut.
    """
    message = update.effective_message
    chat = update.effective_chat
    if message is None:
        return
    if chat is not None and chat.type != ChatType.PRIVATE:
        await message.reply_text("DM me to rename players. 🤫")
        return
    conn = _conn(context)
    if message.text is not None and len(message.text.split()) > 1:
        parsed = _parse_rename_args(message.text)
        if parsed is None:
            await message.reply_text(RENAME_USAGE)
            return
        identifier, new_name = parsed
        await _rename_one_shot(message, conn, identifier, new_name)
        return
    players = list_active_players(conn)
    if not players:
        await message.reply_text(RENAME_EMPTY_ROSTER)
        return
    await message.reply_text(
        "Who do you want to rename?",
        reply_markup=_player_keyboard(players, RENAME_PREFIX),
    )


@require_admin
async def handle_rename_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Admin tapped a player button: stash the target and prompt for the new name."""
    query = update.callback_query
    if query is None or query.data is None:
        return
    raw = query.data.removeprefix(RENAME_PREFIX)
    try:
        telegram_id = int(raw)
    except ValueError:
        await query.answer()
        return
    conn = _conn(context)
    row = conn.execute(
        "SELECT display_name FROM players WHERE telegram_id=? AND active=1",
        (telegram_id,),
    ).fetchone()
    if row is None:
        await query.answer("That player is no longer on the roster.", show_alert=True)
        return
    if context.user_data is not None:
        context.user_data[PENDING_RENAME_KEY] = telegram_id
    await query.answer()
    await _safe_edit(query, f"Send the new display name for {row['display_name']}:")


async def handle_rename_text(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Apply a pending rename from a plain DM text message.

    Registered in a lower-priority handler group so it sees every private text
    message (commands still reach their CommandHandler in the default group).
    Acts only when this admin has a rename pending — otherwise returns silently
    so normal messages are never swallowed. A command sent while pending cancels
    the rename instead of being consumed as the new name.
    """
    message = update.effective_message
    if message is None or message.text is None or context.user_data is None:
        return
    telegram_id = context.user_data.get(PENDING_RENAME_KEY)
    if telegram_id is None:
        return
    text = message.text.strip()
    if text.startswith("/"):
        context.user_data.pop(PENDING_RENAME_KEY, None)
        await message.reply_text("Rename cancelled — you sent a command instead of a name.")
        return
    if not text:
        await message.reply_text("Name can't be empty — send the new display name.")
        return
    old_name = rename_player(_conn(context), telegram_id, text)
    context.user_data.pop(PENDING_RENAME_KEY, None)
    if old_name is None:
        await message.reply_text("That player is no longer on the roster.")
        return
    await message.reply_text(f"Renamed {old_name} → {text} ✅")
