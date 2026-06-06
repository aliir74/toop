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
from telegram.error import BadRequest, Forbidden, TimedOut
from telegram.ext import ContextTypes

from toop.admin import require_admin
from toop.contacts import Contact, get_contact, list_addable_contacts, list_contacts
from toop.i18n import t
from toop.photos import delete_photo_bytes, save_photo_bytes
from toop.players import (
    LinkResult,
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
    set_player_photo,
    soft_remove_player,
)

logger = logging.getLogger(__name__)

RENAME_PREFIX = "rename:"
PENDING_RENAME_KEY = "pending_rename"
SETPHOTO_PREFIX = "setphoto:"
UNSETPHOTO_PREFIX = "unsetphoto:"
PENDING_SET_PHOTO_KEY = "pending_set_photo"

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
    ("roster.dur_1week", "1w"),
    ("roster.dur_2weeks", "2w"),
    ("roster.dur_1month", "1m"),
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


def _contact_label(contact: Contact) -> str:
    handle = f"@{contact.username}" if contact.username else "(no username)"
    return f"{handle} · {contact.display_name or '?'}"


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
        await query.answer(t("roster.player_gone"), show_alert=True)
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
    conn = _conn(context)
    # Args present (more than just "/add_player") → the typed one-shot.
    if len(message.text.split()) > 1:
        parsed = _parse_add_args(message.text)
        if parsed is None:
            await message.reply_text(t("roster.add_usage"))
            return
        identifier, display_name = parsed
        if isinstance(identifier, int):
            # Add-by-id: the contacts row proves we can DM them later for voting.
            contact = get_contact(conn, identifier)
            if contact is None:
                await message.reply_text(t("roster.add_by_id_not_dmd", id=identifier))
                return
            telegram_id = identifier
            username = contact.username  # may be None — that's fine.
        else:
            username = identifier
            resolved = await _resolve_telegram_id(context, username)
            if resolved is None:
                await message.reply_text(t("roster.cant_find_username", username=username))
                return
            telegram_id = resolved
        await message.reply_text(_add_and_describe(conn, telegram_id, username, display_name))
        return
    # No args → button flow: pick an addable contact, then type a display name.
    # DM-only because the name step consumes a private text message.
    chat = update.effective_chat
    if chat is not None and chat.type != ChatType.PRIVATE:
        await message.reply_text(t("roster.dm_to_add"))
        return
    contacts = list_addable_contacts(conn)
    if not contacts:
        await message.reply_text(t("roster.no_new_contacts"))
        return
    keyboard = InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton(
                    _contact_label(c), callback_data=f"{ADDPICK_PREFIX}{c.telegram_id}"
                )
            ]
            for c in contacts
        ]
    )
    await message.reply_text(t("roster.who_add"), reply_markup=keyboard)


def _add_and_describe(
    conn: sqlite3.Connection, telegram_id: int, username: str | None, display_name: str
) -> str:
    """Insert or revive a player and return the confirmation line. Shared by the
    typed one-shot and the button + typed-name flow."""
    existed = conn.execute(
        "SELECT active FROM players WHERE telegram_id=?", (telegram_id,)
    ).fetchone()
    is_new = existed is None
    player = add_player(conn, telegram_id, display_name, username)
    if is_new:
        suffix = ""
    else:
        was_inactive = existed is not None and existed["active"] == 0
        suffix = t("roster.revived_suffix") if was_inactive else ""
    handle = f"@{player.username}" if player.username else t("roster.no_username")
    return t("roster.added", name=player.display_name, handle=handle, suffix=suffix)


@require_admin
async def handle_add_pick_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Admin tapped a contact button from /add_player — stash it and ask for a name."""
    query = update.callback_query
    if query is None or query.data is None:
        return
    telegram_id = _pick_id(query.data, ADDPICK_PREFIX)
    if telegram_id is None:
        await query.answer()
        return
    conn = _conn(context)
    contact = get_contact(conn, telegram_id)
    if contact is None:
        await query.answer(t("roster.contact_unavailable"), show_alert=True)
        return
    if context.user_data is not None:
        context.user_data[PENDING_ADD_KEY] = telegram_id
    await query.answer()
    await _safe_edit(query, t("roster.send_display_name", label=_contact_label(contact)))


async def handle_add_player_text(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Apply a pending /add_player from a plain DM text message.

    Registered in a lower-priority group (separate from the rename consumer) so
    every private text is seen; acts only when this admin has an add pending,
    otherwise returns silently. A command cancels the pending add.
    """
    message = update.effective_message
    if message is None or message.text is None or context.user_data is None:
        return
    telegram_id = context.user_data.get(PENDING_ADD_KEY)
    if telegram_id is None:
        return
    text = message.text.strip()
    if text.startswith("/"):
        context.user_data.pop(PENDING_ADD_KEY, None)
        await message.reply_text(t("roster.add_cancelled"))
        return
    if not text:
        await message.reply_text(t("roster.name_empty"))
        return
    conn = _conn(context)
    contact = get_contact(conn, telegram_id)
    context.user_data.pop(PENDING_ADD_KEY, None)
    if contact is None:
        await message.reply_text(t("roster.contact_unavailable"))
        return
    await message.reply_text(_add_and_describe(conn, telegram_id, contact.username, text))


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
            await message.reply_text(t("roster.roster_empty_remove"))
            return
        await message.reply_text(
            t("roster.who_remove"),
            reply_markup=_player_keyboard(players, RMPICK_PREFIX),
        )
        return
    username = context.args[0].lstrip("@").lower()
    if not username:
        await message.reply_text(t("roster.remove_usage"))
        return
    telegram_id = await _resolve_telegram_id(context, username)
    if telegram_id is None:
        await message.reply_text(t("roster.cant_find_username_short", username=username))
        return
    if soft_remove_player(conn, telegram_id):
        await message.reply_text(t("roster.removed_username", username=username))
    else:
        await message.reply_text(t("roster.not_in_active", username=username))


@require_admin
async def handle_remove_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Admin tapped a player button from /remove_player — soft-remove them."""
    await _single_pick_action(
        update,
        context,
        RMPICK_PREFIX,
        soft_remove_player,
        lambda name: t("roster.removed_name", name=name),
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
    conn = _conn(context)
    if not context.args:
        players = list_active_players(conn)
        if not players:
            await message.reply_text(t("roster.roster_empty_pause"))
            return
        await message.reply_text(
            t("roster.who_pause"),
            reply_markup=_player_keyboard(players, PAUSEPICK_PREFIX),
        )
        return
    if len(context.args) < 2:
        await message.reply_text(t("roster.pause_usage"))
        return
    delta = _parse_duration(context.args[1])
    if delta is None:
        await message.reply_text(t("roster.pause_usage"))
        return
    target = _resolve_pool_target(conn, context.args[0])
    until = datetime.now(UTC) + delta
    if target is not None and pause_player_pool(conn, target, until):
        await message.reply_text(
            t("roster.paused_until", target=context.args[0], date=f"{until:%Y-%m-%d}")
        )
    else:
        await message.reply_text(t("roster.cant_find_roster", target=context.args[0]))


@require_admin
async def handle_pause_pick_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Admin tapped a player button from /pause_voting — offer duration buttons."""
    query = update.callback_query
    if query is None or query.data is None:
        return
    telegram_id = _pick_id(query.data, PAUSEPICK_PREFIX)
    if telegram_id is None:
        await query.answer()
        return
    conn = _conn(context)
    row = conn.execute(
        "SELECT display_name FROM players WHERE telegram_id=? AND active=1",
        (telegram_id,),
    ).fetchone()
    if row is None:
        await query.answer(t("roster.player_gone"), show_alert=True)
        return
    keyboard = InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton(
                    t(label), callback_data=f"{PAUSEDUR_PREFIX}{telegram_id}:{token}"
                )
            ]
            for label, token in PAUSE_DURATIONS
        ]
    )
    await query.answer()
    await _safe_edit(
        query, t("roster.how_long_pause", name=row["display_name"]), reply_markup=keyboard
    )


@require_admin
async def handle_pause_dur_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Admin tapped a duration button — apply the pause to the chosen player."""
    query = update.callback_query
    if query is None or query.data is None:
        return
    # callback_data is "<id>:<token>"; rpartition splits only the trailing token
    # off so a negative ghost id keeps its sign.
    id_str, _sep, token = query.data.removeprefix(PAUSEDUR_PREFIX).rpartition(":")
    try:
        telegram_id = int(id_str)
    except ValueError:
        await query.answer()
        return
    delta = _parse_duration(token)
    if delta is None:  # stale/forged callback — token isn't a real duration
        await query.answer()
        return
    conn = _conn(context)
    row = conn.execute(
        "SELECT display_name FROM players WHERE telegram_id=? AND active=1",
        (telegram_id,),
    ).fetchone()
    if row is None:
        await query.answer(t("roster.player_gone"), show_alert=True)
        return
    until = datetime.now(UTC) + delta
    pause_player_pool(conn, telegram_id, until)  # row verified active above
    await query.answer()
    await _safe_edit(
        query,
        t("roster.paused_until_edit", name=row["display_name"], date=f"{until:%Y-%m-%d}"),
    )


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
            await message.reply_text(t("roster.roster_empty_disable"))
            return
        await message.reply_text(
            t("roster.who_disable"),
            reply_markup=_player_keyboard(players, DISPICK_PREFIX),
        )
        return
    target = _resolve_pool_target(conn, context.args[0])
    if target is not None and disable_player_pool(conn, target):
        await message.reply_text(t("roster.disabled", target=context.args[0]))
    else:
        await message.reply_text(t("roster.cant_find_roster", target=context.args[0]))


@require_admin
async def handle_disable_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Admin tapped a player button from /disable_voting — pull them from the pool."""
    await _single_pick_action(
        update,
        context,
        DISPICK_PREFIX,
        disable_player_pool,
        lambda name: t("roster.disabled_name", name=name),
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
            await message.reply_text(t("roster.nobody_paused"))
            return
        await message.reply_text(
            t("roster.who_restore"),
            reply_markup=_player_keyboard(players, ENPICK_PREFIX),
        )
        return
    target = _resolve_pool_target(conn, context.args[0])
    if target is not None and enable_player_pool(conn, target):
        await message.reply_text(t("roster.restored", target=context.args[0]))
    else:
        await message.reply_text(t("roster.cant_find_roster", target=context.args[0]))


@require_admin
async def handle_enable_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Admin tapped a player button from /enable_voting — restore them to the pool."""
    await _single_pick_action(
        update,
        context,
        ENPICK_PREFIX,
        enable_player_pool,
        lambda name: t("roster.restored_name", name=name),
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
        await message.reply_text(t("roster.add_ghost_usage"))
        return
    name = " ".join(tokens[1:]).strip()
    if not name:
        await message.reply_text(t("roster.add_ghost_usage"))
        return
    conn = _conn(context)
    ghost = add_ghost_player(conn, name)
    await message.reply_text(t("roster.ghost_added", name=ghost.display_name, id=ghost.telegram_id))


@require_admin
async def handle_link_player(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Merge a ghost player into a real account once that person joins Telegram.

    The real account must have DM'd the bot (contacts row) so we can message them
    for voting — the same DM-ability rule as /add_player by id.
    """
    message = update.effective_message
    if message is None:
        return
    conn = _conn(context)
    if not context.args:
        ghosts = [p for p in list_active_players(conn) if p.is_ghost]
        if not ghosts:
            await message.reply_text(t("roster.no_ghosts"))
            return
        keyboard = InlineKeyboardMarkup(
            [
                [
                    InlineKeyboardButton(
                        f"👻 {p.display_name}", callback_data=f"{LNKGHOST_PREFIX}{p.telegram_id}"
                    )
                ]
                for p in ghosts
            ]
        )
        await message.reply_text(t("roster.which_ghost"), reply_markup=keyboard)
        return
    if len(context.args) < 2:
        await message.reply_text(t("roster.link_usage"))
        return
    ghost_token = context.args[0]
    if not ghost_token.lstrip("-").isdigit():
        await message.reply_text(t("roster.link_usage"))
        return
    ghost_id = int(ghost_token)
    ghost_row = conn.execute(
        "SELECT display_name, is_ghost FROM players WHERE telegram_id=?", (ghost_id,)
    ).fetchone()
    if ghost_row is None or ghost_row["is_ghost"] != 1:
        await message.reply_text(t("roster.not_a_ghost", id=ghost_id))
        return

    real_token = context.args[1]
    if real_token.isdigit():
        real_id = int(real_token)
    else:
        username = real_token.lstrip("@").lower()
        resolved = await _resolve_telegram_id(context, username)
        if resolved is None:
            await message.reply_text(t("roster.cant_find_start", username=username))
            return
        real_id = resolved

    contact = get_contact(conn, real_id)
    if contact is None:
        await message.reply_text(t("roster.real_not_dmd", id=real_id))
        return
    result = link_ghost_player(
        conn,
        ghost_id,
        real_id,
        contact.username,
        contact.display_name or ghost_row["display_name"],
    )
    await message.reply_text(_link_summary(ghost_row["display_name"], real_id, result))


def _link_summary(ghost_name: str, real_id: int, result: LinkResult) -> str:
    return t(
        "roster.link_summary",
        name=ghost_name,
        id=real_id,
        scores=result.score_rows,
        ratings=result.ratings,
        rsvps=result.rsvps,
        attendance=result.attendance,
    )


@require_admin
async def handle_link_ghost_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Admin tapped a 👻 button from /link_player — offer the contacts to link to."""
    query = update.callback_query
    if query is None or query.data is None:
        return
    ghost_id = _pick_id(query.data, LNKGHOST_PREFIX)
    if ghost_id is None:
        await query.answer()
        return
    conn = _conn(context)
    ghost_row = conn.execute(
        "SELECT display_name, is_ghost FROM players WHERE telegram_id=? AND active=1",
        (ghost_id,),
    ).fetchone()
    if ghost_row is None or ghost_row["is_ghost"] != 1:
        await query.answer(t("roster.not_ghost_anymore"), show_alert=True)
        return
    contacts = list_addable_contacts(conn)
    if not contacts:
        await query.answer()
        await _safe_edit(
            query,
            t("roster.nobody_new_dmd"),
        )
        return
    keyboard = InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton(
                    _contact_label(c), callback_data=f"{LNKREAL_PREFIX}{ghost_id}:{c.telegram_id}"
                )
            ]
            for c in contacts
        ]
    )
    await query.answer()
    await _safe_edit(
        query, t("roster.link_to_which", name=ghost_row["display_name"]), reply_markup=keyboard
    )


@require_admin
async def handle_link_real_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Admin tapped a contact button — merge the ghost into that real account."""
    query = update.callback_query
    if query is None or query.data is None:
        return
    # callback_data is "<ghost_id>:<real_id>"; rpartition splits the trailing
    # real id off so the negative ghost id keeps its sign.
    ghost_str, _sep, real_str = query.data.removeprefix(LNKREAL_PREFIX).rpartition(":")
    try:
        ghost_id, real_id = int(ghost_str), int(real_str)
    except ValueError:
        await query.answer()
        return
    conn = _conn(context)
    ghost_row = conn.execute(
        "SELECT display_name, is_ghost FROM players WHERE telegram_id=? AND active=1",
        (ghost_id,),
    ).fetchone()
    if ghost_row is None or ghost_row["is_ghost"] != 1:
        await query.answer(t("roster.ghost_unavailable"), show_alert=True)
        return
    contact = get_contact(conn, real_id)
    if contact is None:
        await query.answer(t("roster.contact_unavailable"), show_alert=True)
        return
    result = link_ghost_player(
        conn, ghost_id, real_id, contact.username, contact.display_name or ghost_row["display_name"]
    )
    await query.answer()
    await _safe_edit(query, _link_summary(ghost_row["display_name"], real_id, result))


@require_admin
async def handle_dk_report(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Show each active player's don't-know rate, highest first — the head of the
    list is who the group can least confidently rate (a pause candidate)."""
    message = update.effective_message
    if message is None:
        return
    stats = dont_know_stats(_conn(context))
    if not stats:
        await message.reply_text(t("roster.dk_none"))
        return
    lines = [t("roster.dk_header")]
    for i, s in enumerate(stats, start=1):
        pct = round(s.dk_rate * 100)
        lines.append(
            t("roster.dk_row", i=i, name=s.display_name, dk=s.dk_count, total=s.total, pct=pct)
        )
    await message.reply_text("\n".join(lines))


@require_admin
async def handle_list_players(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    message = update.effective_message
    if message is None:
        return
    players = list_active_players(_conn(context))
    if not players:
        await message.reply_text(t("roster.roster_empty_list"))
        return
    now = datetime.now(UTC)
    lines = [t("roster.list_header")]
    for i, p in enumerate(players, start=1):
        marker = t("roster.calibrating") if p.is_calibrating else "✅"
        if p.is_ghost:
            handle = t("roster.ghost_tag")
        elif p.username:
            handle = f"@{p.username}"
        else:
            handle = t("roster.no_username")
        tags = ""
        if not p.in_pool:
            tags = t("roster.voting_disabled_tag")
        elif _is_paused(p.pool_paused_until, now):
            tags = t("roster.voting_paused_tag")
        lines.append(
            t("roster.list_row", i=i, name=p.display_name, handle=handle, marker=marker, tags=tags)
        )
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
        await message.reply_text(t("roster.contacts_none"))
        return
    roster_ids = {
        row["telegram_id"] for row in conn.execute("SELECT telegram_id FROM players").fetchall()
    }
    lines = [t("roster.contacts_header")]
    addable = 0
    for i, c in enumerate(contacts, start=1):
        handle = f"@{c.username}" if c.username else "(no username)"
        name = c.display_name or "?"
        first_seen = c.first_seen_at[:10] if c.first_seen_at else "?"
        if c.telegram_id in roster_ids:
            lines.append(t("roster.contact_row", i=i, handle=handle, name=name, date=first_seen))
        else:
            addable += 1
            lines.append(
                t("roster.contact_row_new", i=i, handle=handle, name=name, date=first_seen)
            )
            # Ready-to-copy command — works even when the contact has no @username.
            copy_name = c.display_name or c.username or "Player"
            lines.append(f'   /add_player {c.telegram_id} "{copy_name}"')
    if addable:
        lines.append(t("roster.contacts_legend", n=addable))
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
            await message.reply_text(t("roster.no_active_player_id", id=identifier))
            return
    else:
        player = get_player_by_username(conn, identifier)
        if player is None:
            await message.reply_text(t("roster.username_not_active", username=identifier))
            return
        old_name = rename_player(conn, player.telegram_id, new_name)
        if old_name is None:  # pragma: no cover - racey soft-delete between lookup and update
            await message.reply_text(t("roster.username_not_active", username=identifier))
            return
    await message.reply_text(t("roster.renamed", old=old_name, new=new_name))


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
        await message.reply_text(t("roster.dm_to_rename"))
        return
    conn = _conn(context)
    if message.text is not None and len(message.text.split()) > 1:
        parsed = _parse_rename_args(message.text)
        if parsed is None:
            await message.reply_text(t("roster.rename_usage"))
            return
        identifier, new_name = parsed
        await _rename_one_shot(message, conn, identifier, new_name)
        return
    players = list_active_players(conn)
    if not players:
        await message.reply_text(t("roster.rename_empty_roster"))
        return
    await message.reply_text(
        t("roster.who_rename"),
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
        await query.answer(t("roster.player_gone"), show_alert=True)
        return
    if context.user_data is not None:
        context.user_data[PENDING_RENAME_KEY] = telegram_id
    await query.answer()
    await _safe_edit(query, t("roster.send_new_name", name=row["display_name"]))


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
        await message.reply_text(t("roster.rename_cancelled"))
        return
    if not text:
        await message.reply_text(t("roster.new_name_empty"))
        return
    old_name = rename_player(_conn(context), telegram_id, text)
    context.user_data.pop(PENDING_RENAME_KEY, None)
    if old_name is None:
        await message.reply_text(t("roster.player_gone"))
        return
    await message.reply_text(t("roster.renamed", old=old_name, new=text))


# ----- /set_photo: pick a player, then send a photo -----


@require_admin
async def handle_set_photo(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Set a player's profile photo (DM-only, admin-only).

    Lists active players (ghosts included) as inline buttons; tapping one arms a
    pending capture that the next DM photo (handle_set_photo_photo) fulfils. No
    one-shot arg form — a photo can't ride on the command line.
    """
    message = update.effective_message
    chat = update.effective_chat
    if message is None:
        return
    if chat is not None and chat.type != ChatType.PRIVATE:
        await message.reply_text(t("setphoto.dm_only"))
        return
    players = list_active_players(_conn(context))
    if not players:
        await message.reply_text(t("setphoto.empty_roster"))
        return
    await message.reply_text(
        t("setphoto.pick"),
        reply_markup=_player_keyboard(players, SETPHOTO_PREFIX),
    )


@require_admin
async def handle_set_photo_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Admin tapped a player button: stash the target and prompt for the photo."""
    query = update.callback_query
    if query is None or query.data is None:
        return
    telegram_id = _pick_id(query.data, SETPHOTO_PREFIX)
    if telegram_id is None:
        await query.answer()
        return
    conn = _conn(context)
    row = conn.execute(
        "SELECT display_name FROM players WHERE telegram_id=? AND active=1",
        (telegram_id,),
    ).fetchone()
    if row is None:
        await query.answer(t("setphoto.gone"), show_alert=True)
        return
    if context.user_data is not None:
        context.user_data[PENDING_SET_PHOTO_KEY] = telegram_id
    await query.answer()
    await _safe_edit(query, t("setphoto.send_now", name=row["display_name"]))


async def handle_set_photo_photo(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Store a photo for the pending /set_photo target from a DM photo message.

    Registered in its own lower-priority group so it sees every private photo
    (commands still reach their CommandHandler in the default group). Acts only
    when this admin has a set-photo pending — otherwise returns silently.
    """
    message = update.effective_message
    if message is None or not message.photo or context.user_data is None:
        return
    telegram_id = context.user_data.get(PENDING_SET_PHOTO_KEY)
    if telegram_id is None:
        return
    # Largest PhotoSize = the full-resolution file_id (earlier entries are thumbs).
    file_id = message.photo[-1].file_id
    # Best-effort byte backup — a download hiccup must not block storing the
    # file_id, which is the source of truth (bytes are just the recovery copy).
    try:
        tg_file = await context.bot.get_file(file_id)
        raw = bytes(await tg_file.download_as_bytearray())
        save_photo_bytes(telegram_id, raw)
    except (BadRequest, Forbidden, TimedOut) as exc:  # pragma: no cover - network edge
        logger.warning("photo backup failed for %s: %s", telegram_id, exc)
    name = set_player_photo(_conn(context), telegram_id, file_id)
    context.user_data.pop(PENDING_SET_PHOTO_KEY, None)
    if name is None:
        await message.reply_text(t("setphoto.gone"))
        return
    await message.reply_text(t("setphoto.done", name=name))


async def handle_set_photo_text(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Nudge when a pending /set_photo gets a text message instead of a photo.

    A command cancels the pending set; any other text replies with a reminder
    and keeps the flow armed so the admin can still send the photo.
    """
    message = update.effective_message
    if message is None or message.text is None or context.user_data is None:
        return
    if context.user_data.get(PENDING_SET_PHOTO_KEY) is None:
        return
    if message.text.startswith("/"):
        context.user_data.pop(PENDING_SET_PHOTO_KEY, None)
        await message.reply_text(t("setphoto.cancelled"))
        return
    await message.reply_text(t("setphoto.not_photo"))


# ----- /unset_photo: pick a player, clear their photo -----


@require_admin
async def handle_unset_photo(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Clear a player's profile photo (DM-only, admin-only) via inline buttons."""
    message = update.effective_message
    chat = update.effective_chat
    if message is None:
        return
    if chat is not None and chat.type != ChatType.PRIVATE:
        await message.reply_text(t("setphoto.dm_only"))
        return
    players = list_active_players(_conn(context))
    if not players:
        await message.reply_text(t("setphoto.empty_roster"))
        return
    await message.reply_text(
        t("unsetphoto.pick"),
        reply_markup=_player_keyboard(players, UNSETPHOTO_PREFIX),
    )


def _clear_photo(conn: sqlite3.Connection, telegram_id: int) -> None:
    set_player_photo(conn, telegram_id, None)
    delete_photo_bytes(telegram_id)


@require_admin
async def handle_unset_photo_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Admin tapped a player button: clear the stored photo + delete the backup."""
    await _single_pick_action(
        update,
        context,
        UNSETPHOTO_PREFIX,
        action=_clear_photo,
        success=lambda name: t("unsetphoto.done", name=name),
    )
