from __future__ import annotations

import sqlite3
from unittest.mock import AsyncMock, MagicMock

import pytest
from telegram.constants import ChatType

from toop.handlers.voting import (
    GROUP_REPLY,
    handle_vote_callback,
    handle_vote_command,
)
from toop.players import add_player


@pytest.fixture(autouse=True)
def patch_queue_depth(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("toop.handlers.voting.settings", MagicMock(QUEUE_DEPTH=5))


def _seed(conn: sqlite3.Connection) -> None:
    add_player(conn, 1, "Alice", "alice")
    add_player(conn, 2, "Bob", "bob")
    add_player(conn, 3, "Carol", "carol")
    add_player(conn, 4, "Dan", "dan")


def _dm_update(user_id: int) -> MagicMock:
    u = MagicMock()
    u.effective_user = MagicMock(id=user_id)
    chat = MagicMock(id=user_id)
    chat.type = ChatType.PRIVATE
    u.effective_chat = chat
    msg = MagicMock()
    msg.reply_text = AsyncMock()
    u.effective_message = msg
    return u


def _group_update(user_id: int) -> MagicMock:
    u = MagicMock()
    u.effective_user = MagicMock(id=user_id)
    chat = MagicMock(id=-100123)
    chat.type = ChatType.GROUP
    u.effective_chat = chat
    msg = MagicMock()
    msg.reply_text = AsyncMock()
    u.effective_message = msg
    return u


def _ctx(conn: sqlite3.Connection) -> MagicMock:
    ctx = MagicMock()
    ctx.bot_data = {"conn": conn}
    ctx.bot = MagicMock()
    ctx.bot.send_message = AsyncMock()
    ctx.bot.edit_message_text = AsyncMock()
    return ctx


def _callback_update(user_id: int, data: str, message_id: int = 999) -> MagicMock:
    u = MagicMock()
    q = MagicMock()
    q.from_user = MagicMock(id=user_id)
    q.data = data
    q.answer = AsyncMock()
    q.edit_message_text = AsyncMock()
    msg = MagicMock(chat_id=user_id, message_id=message_id)
    q.message = msg
    u.callback_query = q
    return u


async def test_vote_in_group_redirects_to_dm(conn: sqlite3.Connection) -> None:
    _seed(conn)
    update = _group_update(user_id=1)
    await handle_vote_command(update, _ctx(conn))
    update.effective_message.reply_text.assert_awaited_once_with(GROUP_REPLY)


async def test_vote_in_dm_sends_prompt(conn: sqlite3.Connection) -> None:
    _seed(conn)
    update = _dm_update(user_id=1)
    ctx = _ctx(conn)
    await handle_vote_command(update, ctx)
    ctx.bot.send_message.assert_awaited()
    kwargs = ctx.bot.send_message.await_args.kwargs
    assert "stronger" in kwargs["text"]


async def test_vote_for_non_roster_user(conn: sqlite3.Connection) -> None:
    update = _dm_update(user_id=999)
    ctx = _ctx(conn)
    await handle_vote_command(update, ctx)
    update.effective_message.reply_text.assert_awaited_once()
    reply = update.effective_message.reply_text.await_args.args[0]
    assert "not on the roster" in reply.lower()


async def test_callback_a_increments_a_wins(conn: sqlite3.Connection) -> None:
    _seed(conn)
    update = _callback_update(user_id=1, data="v:a:2:3:attack")
    ctx = _ctx(conn)
    await handle_vote_callback(update, ctx)
    row = conn.execute(
        "SELECT a_wins, b_wins FROM vote_aggregates "
        "WHERE player_a=2 AND player_b=3 AND axis='attack'"
    ).fetchone()
    assert row["a_wins"] == 1
    assert row["b_wins"] == 0
    answered = conn.execute(
        "SELECT COUNT(*) AS n FROM answered_prompts WHERE voter_id=1"
    ).fetchone()["n"]
    assert answered == 1


async def test_callback_dk_records_dedupe_only(conn: sqlite3.Connection) -> None:
    _seed(conn)
    update = _callback_update(user_id=1, data="v:dk:2:3:attack")
    await handle_vote_callback(update, _ctx(conn))
    row = conn.execute(
        "SELECT 1 FROM vote_aggregates WHERE player_a=2 AND player_b=3 AND axis='attack'"
    ).fetchone()
    assert row is None
    answered = conn.execute(
        "SELECT 1 FROM answered_prompts "
        "WHERE voter_id=1 AND player_a=2 AND player_b=3 AND axis='attack'"
    ).fetchone()
    assert answered is not None


async def test_callback_snooze_disables_axis(conn: sqlite3.Connection) -> None:
    _seed(conn)
    update = _callback_update(user_id=1, data="v:sn:setting")
    await handle_vote_callback(update, _ctx(conn))
    row = conn.execute(
        "SELECT 1 FROM snoozes WHERE voter_id=1 AND axis='setting' "
        "AND snoozed_until > CURRENT_TIMESTAMP"
    ).fetchone()
    assert row is not None


async def test_three_votes_three_answered(conn: sqlite3.Connection) -> None:
    _seed(conn)
    ctx = _ctx(conn)
    # Prime queue then answer 3 prompts
    await handle_vote_command(_dm_update(user_id=1), ctx)
    for _ in range(3):
        prompt = conn.execute(
            "SELECT player_a, player_b, axis FROM pending_prompts WHERE voter_id=1 "
            "ORDER BY info_gain DESC LIMIT 1"
        ).fetchone()
        if prompt is None:
            break
        cb_data = f"v:a:{prompt['player_a']}:{prompt['player_b']}:{prompt['axis']}"
        await handle_vote_callback(_callback_update(1, cb_data), ctx)
    answered_count = conn.execute(
        "SELECT COUNT(*) AS n FROM answered_prompts WHERE voter_id=1"
    ).fetchone()["n"]
    assert answered_count == 3
    aggregate_total = conn.execute(
        "SELECT COALESCE(SUM(a_wins + b_wins), 0) AS n FROM vote_aggregates"
    ).fetchone()["n"]
    assert aggregate_total == 3
    # Queue refilled back toward full depth
    pending = conn.execute(
        "SELECT COUNT(*) AS n FROM pending_prompts WHERE voter_id=1"
    ).fetchone()["n"]
    assert pending >= 1


async def test_privacy_voter_and_outcome_not_joinable(conn: sqlite3.Connection) -> None:
    """answered_prompts stores no outcome; vote_aggregates stores no voter."""
    _seed(conn)
    await handle_vote_callback(_callback_update(1, "v:a:2:3:attack"), _ctx(conn))
    ap_columns = [c[1] for c in conn.execute("PRAGMA table_info(answered_prompts)").fetchall()]
    va_columns = [c[1] for c in conn.execute("PRAGMA table_info(vote_aggregates)").fetchall()]
    assert "a_wins" not in ap_columns and "b_wins" not in ap_columns
    assert "voter_id" not in va_columns
