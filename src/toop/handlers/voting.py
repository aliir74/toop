from __future__ import annotations

import logging
import sqlite3

from telegram import (
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Update,
    User,
)
from telegram.constants import ChatType, ParseMode
from telegram.error import BadRequest, Forbidden
from telegram.ext import ContextTypes

from toop.admin import require_admin
from toop.config import settings
from toop.contacts import upsert_contact
from toop.players import Player
from toop.voting_queue import (
    Prompt,
    add_snooze,
    mark_dont_know,
    peek_next_prompt,
    record_vote,
    refill_queue,
)

logger = logging.getLogger(__name__)

CALLBACK_PREFIX = "v:"
# Sent privately when someone runs /vote in the group and the bot can DM them.
GROUP_VOTE_DM_NUDGE = "👋 Tap /vote right here in our DM to rate teammates. 🏐"
# Last-resort transient group nudge when the bot can't DM the sender (they
# haven't started the bot). Self-deletes so it never lingers in the group.
GROUP_VOTE_BLOCKED_NUDGE = "start a DM with me (@{bot}) and tap /vote there 🤫"
GROUP_VOTE_NUDGE_TTL = 10  # seconds before the transient group nudge is deleted
NO_PROMPTS_REPLY = (
    "🎉 No prompts right now. Check back later — new pairs surface as the roster grows."
)

START_DM = (
    "Hi 👋 I'm توپ — I help balance our weekly volleyball teams.\n\n"
    "Tap /vote any time to rate your teammates on attack, defense, and setting. "
    "Your individual votes stay private — only the running tally is used. "
    "The more you vote, the more accurate the teams. 🏐"
)

START_GROUP = "👋 I'm توپ. Tap RSVP buttons here in the group, and DM me to /vote on player skills."


def _conn(context: ContextTypes.DEFAULT_TYPE) -> sqlite3.Connection:
    conn = context.bot_data.get("conn")
    if conn is None:
        raise RuntimeError("DB connection missing from bot_data")
    return conn


def _get_player(conn: sqlite3.Connection, telegram_id: int) -> Player | None:
    row = conn.execute(
        "SELECT telegram_id, username, display_name, is_calibrating, active "
        "FROM players WHERE telegram_id=?",
        (telegram_id,),
    ).fetchone()
    if row is None:
        return None
    return Player(
        telegram_id=row["telegram_id"],
        username=row["username"],
        display_name=row["display_name"],
        is_calibrating=bool(row["is_calibrating"]),
        active=bool(row["active"]),
    )


def _format_prompt(prompt: Prompt, a: Player, b: Player) -> str:
    return f"Who's stronger at *{prompt.axis}*?\n\n*{a.display_name}*  vs  *{b.display_name}*"


def _prompt_keyboard(prompt: Prompt, a: Player, b: Player) -> InlineKeyboardMarkup:
    pair = f"{prompt.player_a}:{prompt.player_b}:{prompt.axis}"
    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton(a.display_name, callback_data=f"{CALLBACK_PREFIX}a:{pair}"),
                InlineKeyboardButton(b.display_name, callback_data=f"{CALLBACK_PREFIX}b:{pair}"),
            ],
            [
                InlineKeyboardButton("🤷 Don't know", callback_data=f"{CALLBACK_PREFIX}dk:{pair}"),
                InlineKeyboardButton(
                    "😴 Snooze axis 1w", callback_data=f"{CALLBACK_PREFIX}sn:{prompt.axis}"
                ),
            ],
        ]
    )


async def _send_next_prompt(
    conn: sqlite3.Connection,
    context: ContextTypes.DEFAULT_TYPE,
    chat_id: int,
    voter_id: int,
    edit_message_id: int | None = None,
    exclude_pair: tuple[int, int] | None = None,
) -> None:
    refill_queue(conn, voter_id, settings.QUEUE_DEPTH)
    prompt = peek_next_prompt(conn, voter_id, exclude_pair=exclude_pair)
    if prompt is None:
        if edit_message_id is not None:
            try:
                await context.bot.edit_message_text(
                    chat_id=chat_id, message_id=edit_message_id, text=NO_PROMPTS_REPLY
                )
                return
            except BadRequest as exc:
                logger.warning("failed to edit prompt message: %s", exc)
        await context.bot.send_message(chat_id=chat_id, text=NO_PROMPTS_REPLY)
        return
    a = _get_player(conn, prompt.player_a)
    b = _get_player(conn, prompt.player_b)
    if a is None or b is None:
        logger.warning(
            "prompt references missing player(s) %s %s", prompt.player_a, prompt.player_b
        )
        await context.bot.send_message(chat_id=chat_id, text=NO_PROMPTS_REPLY)
        return
    text = _format_prompt(prompt, a, b)
    keyboard = _prompt_keyboard(prompt, a, b)
    if edit_message_id is not None:
        try:
            await context.bot.edit_message_text(
                chat_id=chat_id,
                message_id=edit_message_id,
                text=text,
                parse_mode=ParseMode.MARKDOWN,
                reply_markup=keyboard,
            )
            return
        except BadRequest as exc:
            logger.warning("failed to edit prompt message: %s", exc)
    await context.bot.send_message(
        chat_id=chat_id,
        text=text,
        parse_mode=ParseMode.MARKDOWN,
        reply_markup=keyboard,
    )


async def _delete_message_job(context: ContextTypes.DEFAULT_TYPE) -> None:
    """Delete a previously posted transient message (scheduled via job_queue)."""
    job = context.job
    if job is None or job.data is None:
        return
    chat_id, message_id = job.data
    try:
        await context.bot.delete_message(chat_id=chat_id, message_id=message_id)
    except (BadRequest, Forbidden) as exc:
        logger.debug("could not delete transient message %s in %s: %s", message_id, chat_id, exc)


async def _safe_delete(context: ContextTypes.DEFAULT_TYPE, chat_id: int, message_id: int) -> None:
    """Best-effort delete; the bot may lack delete permission in the group."""
    try:
        await context.bot.delete_message(chat_id=chat_id, message_id=message_id)
    except (BadRequest, Forbidden) as exc:
        logger.debug("could not delete message %s in %s: %s", message_id, chat_id, exc)


async def _try_dm_voter(
    conn: sqlite3.Connection, context: ContextTypes.DEFAULT_TYPE, user_id: int
) -> bool:
    """DM the voter their next prompt (or a nudge). Returns True if the DM landed.

    Fails gracefully when the user has never started the bot (``Forbidden``).
    """
    try:
        if _get_player(conn, user_id) is not None:
            await _send_next_prompt(conn, context, chat_id=user_id, voter_id=user_id)
        else:
            await context.bot.send_message(chat_id=user_id, text=GROUP_VOTE_DM_NUDGE)
        return True
    except (Forbidden, BadRequest) as exc:
        logger.info("could not DM voter %s: %s", user_id, exc)
        return False


async def _post_transient_group_nudge(
    context: ContextTypes.DEFAULT_TYPE, chat_id: int, user: User, bot_username: str | None
) -> None:
    """Post a self-deleting group nudge (no reply quote) when the DM was blocked."""
    mention = f"@{user.username}" if user.username else user.full_name
    text = f"{mention} {GROUP_VOTE_BLOCKED_NUDGE.format(bot=bot_username or 'me')}"
    try:
        sent = await context.bot.send_message(chat_id=chat_id, text=text)
    except (BadRequest, Forbidden) as exc:
        logger.warning("could not post transient group nudge: %s", exc)
        return
    if context.job_queue is not None:
        context.job_queue.run_once(
            _delete_message_job,
            when=GROUP_VOTE_NUDGE_TTL,
            data=(chat_id, sent.message_id),
            name=f"del_vote_nudge_{sent.message_id}",
        )


async def handle_vote_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    message = update.effective_message
    chat = update.effective_chat
    user = update.effective_user
    if message is None or chat is None or user is None:
        return
    conn = _conn(context)
    if chat.type != ChatType.PRIVATE:
        # Never leave a standing reply in the group: it quotes the /vote and
        # orphans into "Deleted message" chatter when the player tidies up.
        # Instead push the prompt into a DM and clear the command from the group.
        dm_sent = await _try_dm_voter(conn, context, user.id)
        await _safe_delete(context, chat.id, message.message_id)
        if not dm_sent:
            await _post_transient_group_nudge(context, chat.id, user, context.bot.username)
        return
    if _get_player(conn, user.id) is None:
        await message.reply_text("You're not on the roster yet — ask the admin to add you.")
        return
    await _send_next_prompt(conn, context, chat_id=chat.id, voter_id=user.id)


async def handle_vote_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    if query is None or query.data is None or query.from_user is None or query.message is None:
        return
    chat_id = query.message.chat.id
    message_id = query.message.message_id
    voter_id = query.from_user.id
    conn = _conn(context)

    payload = query.data.removeprefix(CALLBACK_PREFIX)
    parts = payload.split(":")
    action = parts[0]

    if action == "sn":
        if len(parts) < 2:
            await query.answer()
            return
        axis = parts[1]
        if axis not in ("attack", "defense", "setting"):
            await query.answer()
            return
        add_snooze(conn, voter_id, axis)
        await query.answer(f"Snoozed {axis} for 1 week 😴", show_alert=False)
        await _send_next_prompt(
            conn, context, chat_id=chat_id, voter_id=voter_id, edit_message_id=message_id
        )
        return

    if action in ("a", "b", "dk"):
        if len(parts) < 4:
            await query.answer()
            return
        try:
            player_a = int(parts[1])
            player_b = int(parts[2])
        except ValueError:
            await query.answer()
            return
        axis = parts[3]
        if axis not in ("attack", "defense", "setting"):
            await query.answer()
            return
        if action == "dk":
            mark_dont_know(conn, voter_id, player_a, player_b, axis)
            await query.answer("Skipped 🤷")
        else:
            record_vote(conn, voter_id, player_a, player_b, axis, action)
            await query.answer("Recorded ✅")
        # Prefer a different pair next so the prompt visibly advances instead of
        # cycling the same two names across attack/defense/setting.
        await _send_next_prompt(
            conn,
            context,
            chat_id=chat_id,
            voter_id=voter_id,
            edit_message_id=message_id,
            exclude_pair=(player_a, player_b),
        )
        return

    await query.answer()


async def handle_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    message = update.effective_message
    chat = update.effective_chat
    if message is None or chat is None:
        return
    if chat.type == ChatType.PRIVATE:
        user = update.effective_user
        if user is not None:
            upsert_contact(
                _conn(context),
                user.id,
                username=user.username,
                display_name=user.full_name,
            )
        await message.reply_text(START_DM)
    else:
        await message.reply_text(START_GROUP)


def _build_nudge_templates(conn: sqlite3.Connection, limit: int = 5) -> list[str]:
    """Return raw DM-able templates per low-completion voter.

    Sorted ascending by lifetime answered_prompts count. Privacy-safe — counts
    completion only, never reveals what they voted.
    """
    rows = conn.execute(
        """
        SELECT p.telegram_id, p.username, p.display_name,
               (SELECT COUNT(*) FROM answered_prompts ap WHERE ap.voter_id = p.telegram_id)
                   AS lifetime
        FROM players p
        WHERE p.active = 1
        ORDER BY lifetime ASC, p.display_name COLLATE NOCASE
        LIMIT ?
        """,
        (limit,),
    ).fetchall()
    templates: list[str] = []
    for r in rows:
        handle = f"@{r['username']}" if r["username"] else r["display_name"]
        first_name = r["display_name"].split()[0]
        templates.append(
            f"--- {r['display_name']} ({handle}) — {r['lifetime']} lifetime votes ---\n"
            f"Hey {first_name}! Whenever you get a sec, "
            f"could you ping توپ on Telegram and run /vote? "
            f"It helps me balance teams better. 🙏 Takes ~30s."
        )
    return templates


@require_admin
async def handle_nudge(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    message = update.effective_message
    if message is None:
        return
    templates = _build_nudge_templates(_conn(context))
    if not templates:
        await message.reply_text("No players on the roster yet.")
        return
    body = "\n\n".join(templates)
    header = (
        "Copy/paste these to nudge the lowest-completion voters. "
        "(Manual sends only — no auto-DMs.)\n\n"
    )
    await message.reply_text(header + body)
