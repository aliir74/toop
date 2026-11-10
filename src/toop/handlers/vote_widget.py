"""Rate a whole player in one Bot API 10.4 HTML widget.

The widget's code (vote_widget.html) is fixed once sent; only its state changes.
The state names the player on screen, so one card cycles through the queue:
the bot answers each tap with the next state (the fast channel, delivered at
once) and also writes it with editMessageWidgetData (the durable copy, which
Telegram can take tens of seconds to apply). `q` orders states, so the widget
ignores an edit that lands after a newer answer.

python-telegram-bot 22.x predates Bot API 10.4, so every widget method goes
through Bot.do_api_request and widget_query updates arrive in Update.api_kwargs.
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
import sqlite3
import time
from dataclasses import dataclass, field
from functools import cache
from pathlib import Path
from typing import Any

from telegram import Update
from telegram.error import BadRequest, TelegramError, TimedOut
from telegram.ext import ContextTypes

from toop.i18n import indicator_label, score_word, t
from toop.rating import INDICATORS
from toop.sessions import get_active_session
from toop.voting_queue import record_score, record_skip, select_next_score_target

logger = logging.getLogger(__name__)

TEMPLATE_PATH = Path(__file__).resolve().parent.parent / "vote_widget.html"
STATE_VERSION = 1
MAX_NAME_CHARS = 64
# sendWidget has been slow since launch (one send took about a minute). Waiting
# that long stalls every other update, and a timed-out send may still land, so
# give up waiting early and never follow a timeout with a second card.
SEND_READ_TIMEOUT = 20.0
EDIT_READ_TIMEOUT = 90.0

# Request format v1: "1r:<player>:<six 1-5 digits>" submits, "1k:<player>" is
# ندیدمش, "1n:<player>" moves on. The leading version digit lets a later format
# coexist with cards already sitting in chats, whose code can never change.
_QUERY_RE = re.compile(r"1([rkn]):(\d{1,20})(?::([1-5]{6}))?")


@cache
def build_widget_html(lang: str) -> str:
    """The widget's HTML in one language. Labels come from the i18n catalog;
    player names never do (they travel in the state and render as text only)."""
    rtl = lang == "fa"
    config = {
        "S": [indicator_label(ind, lang) for ind in INDICATORS],
        "L": [score_word(n, lang) for n in range(1, 6)],
        "rtl": rtl,
        "t": {
            "title": t("vote.widget_title", lang),
            "submit": t("vote.widget_submit", lang),
            "dk": t("vote.btn_dont_know", lang),
            "skip": t("vote.btn_skip", lang),
            "saved": t("vote.widget_saved", lang),
            "next": t("vote.widget_next", lang),
            "saving": t("vote.widget_saving", lang),
            "slow": t("vote.widget_slow", lang),
            "fast": t("vote.widget_fast", lang),
            "err": t("vote.widget_error", lang),
            "end": t("vote.no_prompts", lang),
        },
    }
    # The sandbox ignores CSS `direction`, so right-to-left is laid out by hand.
    cfg_js = json.dumps(config, ensure_ascii=False).replace("</", "<\\/")
    return (
        TEMPLATE_PATH.read_text(encoding="utf-8")
        .replace("__CFG__", cfg_js)
        .replace("__ROW__", "row-reverse" if rtl else "row")
        .replace("__ALIGN__", "right" if rtl else "left")
    )


def _seq() -> int:
    return time.time_ns() // 1_000_000


def rating_state(player_id: int, name: str) -> dict[str, Any]:
    return {"v": STATE_VERSION, "q": _seq(), "p": player_id, "n": name[:MAX_NAME_CHARS]}


def saved_state(player_id: int, name: str, digits: str) -> dict[str, Any]:
    return {**rating_state(player_id, name), "s": digits}


def end_state() -> dict[str, Any]:
    return {"v": STATE_VERSION, "q": _seq(), "e": 1}


def dump_state(state: dict[str, Any]) -> str:
    return json.dumps(state, ensure_ascii=False)


def _player_name(conn: sqlite3.Connection, telegram_id: int) -> str | None:
    row = conn.execute(
        "SELECT display_name FROM players WHERE telegram_id=?", (telegram_id,)
    ).fetchone()
    return None if row is None else row["display_name"]


def next_state(
    conn: sqlite3.Connection, voter_id: int, exclude: int, *, require_other: bool
) -> dict[str, Any]:
    """The card for whoever the queue offers next, or the end card.

    require_other mirrors the button flow's ⏭: moving on from the only player
    left must not hand back the same player."""
    target = select_next_score_target(conn, voter_id, exclude_player=exclude)
    if target is not None and require_other and target.player_id == exclude:
        target = None
    if target is None:
        return end_state()
    name = _player_name(conn, target.player_id)
    if name is None:
        return end_state()
    return rating_state(target.player_id, name)


def apply_query(conn: sqlite3.Connection, voter_id: int, data: str) -> dict[str, Any] | None:
    """Record one widget request and return the state the card shows next, or
    None for a request to ignore (not a voter, malformed, or about themselves).
    `data` is untrusted: the voter comes only from Telegram's `from`."""
    if _player_name(conn, voter_id) is None:
        return None
    match = _QUERY_RE.fullmatch(data)
    if match is None:
        return None
    action, player_id, digits = match.group(1), int(match.group(2)), match.group(3)
    if player_id == voter_id:
        return None
    name = _player_name(conn, player_id)
    if name is None:
        # The player left the roster while their card was on screen.
        return next_state(conn, voter_id, player_id, require_other=True)
    if action == "r":
        if digits is None:
            return None
        for indicator, digit in zip(INDICATORS, digits, strict=True):
            record_score(conn, voter_id, player_id, indicator, int(digit))
        return saved_state(player_id, name, digits)
    if digits is not None:
        return None
    if action == "k":
        active = get_active_session(conn)
        # One skip row, as the button flow records: it hides the whole player
        # for SKIP_COOLDOWN_DAYS and keeps the don't-know rate's units unchanged.
        record_skip(
            conn, voter_id, player_id, INDICATORS[0], session_id=active.id if active else None
        )
        return next_state(conn, voter_id, player_id, require_other=False)
    return next_state(conn, voter_id, player_id, require_other=True)


async def send_widget_card(
    context: ContextTypes.DEFAULT_TYPE, chat_id: int, player_id: int, name: str, lang: str
) -> bool:
    """Send a rating card for one player. False means Telegram refused it, so
    the caller falls back to the button card. A timeout counts as sent: the
    widget may still land, and a fallback would stack a second card under it.
    Forbidden (the voter never started the bot) propagates to the caller."""
    try:
        await context.bot.do_api_request(
            "sendWidget",
            api_kwargs={
                "chat_id": chat_id,
                "html": build_widget_html(lang),
                "allow_queries": True,
                "chat_data": dump_state(rating_state(player_id, name)),
                "caption": t("vote.widget_caption", lang),
            },
            read_timeout=SEND_READ_TIMEOUT,
        )
    except TimedOut:
        logger.warning("sendWidget to %s timed out; assuming it landed", chat_id)
        return True
    except BadRequest as exc:
        logger.warning("sendWidget to %s refused, using button card: %s", chat_id, exc)
        return False
    return True


@dataclass
class _CardEdits:
    """Orders the background edits of one card. Telegram applies edits to the
    same message in whatever order they finish, and one can take most of a
    minute, so an older state could land last and come back when the card is
    reopened. Edits run one at a time, and one that a newer state has already
    replaced is skipped."""

    lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    latest: int = 0


def _card_edits(context: ContextTypes.DEFAULT_TYPE, chat_id: int, message_id: int) -> _CardEdits:
    cards: dict[tuple[int, int], _CardEdits] = context.bot_data.setdefault("widget_edits", {})
    return cards.setdefault((chat_id, message_id), _CardEdits())


async def _persist_state(
    context: ContextTypes.DEFAULT_TYPE,
    chat_id: int,
    message_id: int,
    state: dict[str, Any],
    edits: _CardEdits,
) -> None:
    """Write the state into the message, so it survives the widget being
    reopened. Runs as a background task: the edit can take most of a minute."""
    async with edits.lock:
        if state["q"] != edits.latest:
            return
        try:
            await context.bot.do_api_request(
                "editMessageWidgetData",
                api_kwargs={
                    "chat_id": chat_id,
                    "message_id": message_id,
                    "chat_data": dump_state(state),
                },
                read_timeout=EDIT_READ_TIMEOUT,
            )
        except BadRequest as exc:
            if "not modified" not in str(exc).lower():
                logger.warning("editMessageWidgetData %s/%s failed: %s", chat_id, message_id, exc)
        except TelegramError as exc:
            logger.warning("editMessageWidgetData %s/%s failed: %s", chat_id, message_id, exc)


async def handle_widget_query(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Answer a tap on a rating widget. Registered as a TypeHandler in its own
    group, so it sees every update and returns at once for anything else."""
    raw = (update.api_kwargs or {}).get("widget_query")
    if not isinstance(raw, dict) or not raw.get("id"):
        return
    conn: sqlite3.Connection = context.bot_data["conn"]
    voter_id = (raw.get("from") or {}).get("id")
    state = None
    if isinstance(voter_id, int):
        state = apply_query(conn, voter_id, str(raw.get("data", "")))
    # Every query gets an answer within 15 seconds, or the widget shows a timeout.
    answer: dict[str, Any] = {"widget_query_id": raw["id"]}
    if state is not None:
        answer["data"] = dump_state(state)
    try:
        await context.bot.do_api_request("answerWidgetQuery", api_kwargs=answer)
    except TelegramError as exc:
        logger.warning("answerWidgetQuery failed: %s", exc)
    chat_id = (raw.get("chat") or {}).get("id")
    message_id = raw.get("message_id")
    if state is not None and isinstance(chat_id, int) and isinstance(message_id, int):
        edits = _card_edits(context, chat_id, message_id)
        edits.latest = state["q"]
        context.application.create_task(
            _persist_state(context, chat_id, message_id, state, edits), update=update
        )
