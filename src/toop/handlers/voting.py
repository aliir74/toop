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
from toop.contacts import upsert_contact
from toop.players import Player
from toop.voting_queue import (
    ScoreTarget,
    record_score,
    record_skip,
    select_next_score_target,
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
    "🎉 All done for now — you've rated everyone. Check back later as the roster grows."
)

START_DM = (
    "Hi 👋 I'm توپ — I help balance our weekly volleyball teams.\n\n"
    "Tap /vote any time to rate your teammates 1–5 on six skills "
    "(حمله، دریافت، دفاع روی تور، پاسور، سرویس، جاگیری-تحرک). "
    "You can re-tap any time to change a score. "
    "The more you rate, the more accurate the teams. 🏐"
)

START_GROUP = "👋 I'm توپ. Tap RSVP buttons here in the group, and DM me to /vote on player skills."

# Persian display label per indicator (shown in the prompt header).
INDICATOR_FA: dict[str, str] = {
    "attack": "حمله",
    "receive": "دریافت",
    "block": "دفاع روی تور",
    "setting": "پاسور",
    "serve": "سرویس",
    "positioning": "جاگیری-تحرک",
}
# Persian scale words shown on the score buttons (NOT digits).
SCORE_FA: dict[int, str] = {1: "خیلی ضعیف", 2: "ضعیف", 3: "متوسط", 4: "خوب", 5: "عالی"}
# Short ASCII codes for callback_data (Telegram's 64-byte limit + long Persian).
INDICATOR_CODE: dict[str, str] = {
    "attack": "atk",
    "receive": "rcv",
    "block": "blk",
    "setting": "set",
    "serve": "srv",
    "positioning": "pos",
}
CODE_INDICATOR: dict[str, str] = {code: ind for ind, code in INDICATOR_CODE.items()}


def _conn(context: ContextTypes.DEFAULT_TYPE) -> sqlite3.Connection:
    conn = context.bot_data.get("conn")
    if conn is None:
        raise RuntimeError("DB connection missing from bot_data")
    return conn


def _get_player(conn: sqlite3.Connection, telegram_id: int) -> Player | None:
    row = conn.execute(
        "SELECT telegram_id, username, display_name, is_calibrating, active, "
        "in_pool, pool_paused_until, is_ghost "
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
        in_pool=bool(row["in_pool"]),
        pool_paused_until=row["pool_paused_until"],
        is_ghost=bool(row["is_ghost"]),
    )


def _format_prompt(target: ScoreTarget, player: Player) -> str:
    label = INDICATOR_FA.get(target.indicator, target.indicator)
    return f"Rate *{player.display_name}* — *{label}*"


def _prompt_keyboard(target: ScoreTarget, player: Player) -> InlineKeyboardMarkup:
    code = INDICATOR_CODE[target.indicator]
    # Five score buttons stacked best→worst (Persian labels carry the meaning;
    # the numeric score only travels in callback_data).
    rows = [
        [
            InlineKeyboardButton(
                SCORE_FA[score],
                callback_data=f"{CALLBACK_PREFIX}s:{player.telegram_id}:{code}:{score}",
            )
        ]
        for score in (5, 4, 3, 2, 1)
    ]
    rows.append(
        [
            InlineKeyboardButton(
                "🤷 ندیدمش", callback_data=f"{CALLBACK_PREFIX}dk:{player.telegram_id}:{code}"
            ),
            InlineKeyboardButton(
                "⏭ Skip", callback_data=f"{CALLBACK_PREFIX}sk:{player.telegram_id}"
            ),
        ]
    )
    return InlineKeyboardMarkup(rows)


async def _send_next_prompt(
    conn: sqlite3.Connection,
    context: ContextTypes.DEFAULT_TYPE,
    chat_id: int,
    voter_id: int,
    edit_message_id: int | None = None,
    exclude_player: int | None = None,
) -> None:
    target = select_next_score_target(conn, voter_id, exclude_player=exclude_player)
    if target is None:
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
    player = _get_player(conn, target.player_id)
    if player is None:
        logger.warning("score target references missing player %s", target.player_id)
        await context.bot.send_message(chat_id=chat_id, text=NO_PROMPTS_REPLY)
        return
    text = _format_prompt(target, player)
    keyboard = _prompt_keyboard(target, player)
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

    if action == "sk":
        if len(parts) < 2:
            await query.answer()
            return
        try:
            player_id = int(parts[1])
        except ValueError:
            await query.answer()
            return
        await query.answer("Skipped ⏭")
        await _send_next_prompt(
            conn,
            context,
            chat_id=chat_id,
            voter_id=voter_id,
            edit_message_id=message_id,
            exclude_player=player_id,
        )
        return

    if action in ("s", "dk"):
        if (action == "s" and len(parts) < 4) or (action == "dk" and len(parts) < 3):
            await query.answer()
            return
        try:
            player_id = int(parts[1])
        except ValueError:
            await query.answer()
            return
        indicator = CODE_INDICATOR.get(parts[2])
        if indicator is None:
            await query.answer()
            return
        if action == "dk":
            record_skip(conn, voter_id, player_id, indicator)
            await query.answer("Skipped 🤷")
        else:
            try:
                score = int(parts[3])
            except ValueError:
                await query.answer()
                return
            if not 1 <= score <= 5:
                await query.answer()
                return
            record_score(conn, voter_id, player_id, indicator, score)
            await query.answer("Recorded ✅")
        # Prefer a different player next so the prompt visibly advances instead
        # of cycling one name across all six indicators.
        await _send_next_prompt(
            conn,
            context,
            chat_id=chat_id,
            voter_id=voter_id,
            edit_message_id=message_id,
            exclude_player=player_id,
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

    Sorted ascending by lifetime scores given. Counts completion only, never
    reveals what they scored.
    """
    rows = conn.execute(
        """
        SELECT p.telegram_id, p.username, p.display_name,
               (SELECT COUNT(*) FROM scores s WHERE s.voter_id = p.telegram_id)
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
            f"--- {r['display_name']} ({handle}) — {r['lifetime']} lifetime ratings ---\n"
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
