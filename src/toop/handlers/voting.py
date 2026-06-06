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
from toop.i18n import indicator_label, score_word, t
from toop.players import Player
from toop.voting_queue import (
    ScoreTarget,
    record_score,
    record_skip,
    select_next_score_target,
)

logger = logging.getLogger(__name__)

CALLBACK_PREFIX = "v:"
GROUP_VOTE_NUDGE_TTL = 10  # seconds before the transient group nudge is deleted

# Short ASCII codes for callback_data (Telegram's 64-byte limit + long Persian).
# Display labels for indicators and scores live in the i18n catalog.
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
        "in_pool, pool_paused_until, is_ghost, photo_file_id "
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
        photo_file_id=row["photo_file_id"],
    )


def _format_prompt(target: ScoreTarget, player: Player) -> str:
    return t("vote.prompt", name=player.display_name, label=indicator_label(target.indicator))


def _prompt_keyboard(target: ScoreTarget, player: Player) -> InlineKeyboardMarkup:
    code = INDICATOR_CODE[target.indicator]
    # Five score buttons stacked best→worst (the scale word carries the meaning;
    # the numeric score only travels in callback_data).
    rows = [
        [
            InlineKeyboardButton(
                score_word(score),
                callback_data=f"{CALLBACK_PREFIX}s:{player.telegram_id}:{code}:{score}",
            )
        ]
        for score in (5, 4, 3, 2, 1)
    ]
    rows.append(
        [
            InlineKeyboardButton(
                t("vote.btn_dont_know"),
                callback_data=f"{CALLBACK_PREFIX}dk:{player.telegram_id}:{code}",
            ),
            InlineKeyboardButton(
                t("vote.btn_skip"), callback_data=f"{CALLBACK_PREFIX}sk:{player.telegram_id}"
            ),
        ]
    )
    return InlineKeyboardMarkup(rows)


async def _send_card(
    context: ContextTypes.DEFAULT_TYPE,
    chat_id: int,
    text: str,
    keyboard: InlineKeyboardMarkup,
    photo_file_id: str | None,
) -> None:
    """Send a fresh rating card: a photo message (photo + caption + buttons) when
    the player has a photo, else the text prompt. A stale file_id (only possible
    if the bot was recreated from scratch) falls back to the text card so voting
    never blocks on a bad photo — the admin can re-upload from data/photos/."""
    if photo_file_id is not None:
        try:
            await context.bot.send_photo(
                chat_id=chat_id,
                photo=photo_file_id,
                caption=text,
                parse_mode=ParseMode.MARKDOWN,
                reply_markup=keyboard,
            )
            return
        except BadRequest as exc:
            logger.warning("stale photo file_id in chat %s, falling back to text: %s", chat_id, exc)
    await context.bot.send_message(
        chat_id=chat_id,
        text=text,
        parse_mode=ParseMode.MARKDOWN,
        reply_markup=keyboard,
    )


async def _show_no_prompts(
    context: ContextTypes.DEFAULT_TYPE,
    chat_id: int,
    edit_message_id: int | None,
    current_is_photo: bool,
) -> None:
    """Render the terminal 'nothing left to rate' state. Editing a photo card's
    text is impossible, so a photo card is deleted then replaced with text."""
    if edit_message_id is not None:
        if current_is_photo:
            await _safe_delete(context, chat_id, edit_message_id)
        else:
            try:
                await context.bot.edit_message_text(
                    chat_id=chat_id, message_id=edit_message_id, text=t("vote.no_prompts")
                )
                return
            except BadRequest as exc:
                logger.warning("failed to edit prompt message: %s", exc)
    await context.bot.send_message(chat_id=chat_id, text=t("vote.no_prompts"))


async def _send_next_prompt(
    conn: sqlite3.Connection,
    context: ContextTypes.DEFAULT_TYPE,
    chat_id: int,
    voter_id: int,
    edit_message_id: int | None = None,
    exclude_player: int | None = None,
    current_is_photo: bool = False,
) -> None:
    target = select_next_score_target(conn, voter_id, exclude_player=exclude_player)
    if target is None:
        await _show_no_prompts(context, chat_id, edit_message_id, current_is_photo)
        return
    player = _get_player(conn, target.player_id)
    if player is None:
        logger.warning("score target references missing player %s", target.player_id)
        await context.bot.send_message(chat_id=chat_id, text=t("vote.no_prompts"))
        return
    text = _format_prompt(target, player)
    keyboard = _prompt_keyboard(target, player)
    incoming_is_photo = player.photo_file_id is not None
    if edit_message_id is not None:
        # Telegram can't convert a message between text and photo types, and
        # edit_message_text errors on a photo message. When a photo is on either
        # side of the transition, delete the old card and send a fresh one;
        # text→text still edits in place (the cheap path, no new message).
        if current_is_photo or incoming_is_photo:
            await _safe_delete(context, chat_id, edit_message_id)
        else:
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
    await _send_card(context, chat_id, text, keyboard, player.photo_file_id)


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
            await context.bot.send_message(chat_id=user_id, text=t("vote.group_dm_nudge"))
        return True
    except (Forbidden, BadRequest) as exc:
        logger.info("could not DM voter %s: %s", user_id, exc)
        return False


async def _post_transient_group_nudge(
    context: ContextTypes.DEFAULT_TYPE, chat_id: int, user: User, bot_username: str | None
) -> None:
    """Post a self-deleting group nudge (no reply quote) when the DM was blocked."""
    mention = f"@{user.username}" if user.username else user.full_name
    text = f"{mention} " + t("vote.group_blocked_nudge", bot=bot_username or "me")
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
        await message.reply_text(t("vote.not_on_roster"))
        return
    await _send_next_prompt(conn, context, chat_id=chat.id, voter_id=user.id)


async def handle_vote_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    if query is None or query.data is None or query.from_user is None or query.message is None:
        return
    chat_id = query.message.chat.id
    message_id = query.message.message_id
    voter_id = query.from_user.id
    # A photo card can't be edited into a text card (or vice versa); the advance
    # path needs to know which kind of message it's replacing. PHOTO messages
    # carry a non-empty .photo; text prompts don't.
    current_is_photo = bool(getattr(query.message, "photo", None))
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
        await query.answer(t("vote.answer_skip"))
        await _send_next_prompt(
            conn,
            context,
            chat_id=chat_id,
            voter_id=voter_id,
            edit_message_id=message_id,
            exclude_player=player_id,
            current_is_photo=current_is_photo,
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
            await query.answer(t("vote.answer_dk"))
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
            await query.answer(t("vote.answer_recorded"))
        # Prefer a different player next so the prompt visibly advances instead
        # of cycling one name across all six indicators.
        await _send_next_prompt(
            conn,
            context,
            chat_id=chat_id,
            voter_id=voter_id,
            edit_message_id=message_id,
            exclude_player=player_id,
            current_is_photo=current_is_photo,
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
        await message.reply_text(t("vote.start_dm"))
    else:
        await message.reply_text(t("vote.start_group"))


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
            t(
                "vote.nudge_template",
                name=r["display_name"],
                handle=handle,
                lifetime=r["lifetime"],
                first=first_name,
            )
        )
    return templates


@require_admin
async def handle_nudge(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    message = update.effective_message
    if message is None:
        return
    templates = _build_nudge_templates(_conn(context))
    if not templates:
        await message.reply_text(t("vote.nudge_none"))
        return
    body = "\n\n".join(templates)
    await message.reply_text(t("vote.nudge_header") + body)
