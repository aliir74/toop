from __future__ import annotations

import sqlite3
from unittest.mock import AsyncMock, MagicMock

import pytest
from telegram.constants import ChatType
from telegram.error import BadRequest

from toop.handlers.voting import (
    GROUP_REPLY,
    NO_PROMPTS_REPLY,
    START_DM,
    START_GROUP,
    _conn,
    _send_next_prompt,
    handle_nudge,
    handle_start,
    handle_vote_callback,
    handle_vote_command,
)
from toop.players import add_player
from toop.voting_queue import Prompt


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
    pending = conn.execute("SELECT COUNT(*) AS n FROM pending_prompts WHERE voter_id=1").fetchone()[
        "n"
    ]
    assert pending >= 1


async def test_privacy_voter_and_outcome_not_joinable(conn: sqlite3.Connection) -> None:
    """answered_prompts stores no outcome; vote_aggregates stores no voter."""
    _seed(conn)
    await handle_vote_callback(_callback_update(1, "v:a:2:3:attack"), _ctx(conn))
    ap_columns = [c[1] for c in conn.execute("PRAGMA table_info(answered_prompts)").fetchall()]
    va_columns = [c[1] for c in conn.execute("PRAGMA table_info(vote_aggregates)").fetchall()]
    assert "a_wins" not in ap_columns and "b_wins" not in ap_columns
    assert "voter_id" not in va_columns


def _admin_update(user_id: int = 42, with_message: bool = True) -> MagicMock:
    u = MagicMock()
    u.effective_user = MagicMock(id=user_id)
    if with_message:
        msg = MagicMock()
        msg.reply_text = AsyncMock()
        u.effective_message = msg
    else:
        u.effective_message = None
    return u


def test_conn_raises_when_missing() -> None:
    ctx = MagicMock()
    ctx.bot_data = {}
    with pytest.raises(RuntimeError, match="DB connection missing"):
        _conn(ctx)


async def test_vote_command_returns_without_message(conn: sqlite3.Connection) -> None:
    u = MagicMock()
    u.effective_message = None
    u.effective_chat = MagicMock()
    u.effective_user = MagicMock(id=1)
    # No message → silent return, no DB access needed.
    await handle_vote_command(u, _ctx(conn))


async def test_callback_returns_without_query(conn: sqlite3.Connection) -> None:
    u = MagicMock()
    u.callback_query = None
    await handle_vote_callback(u, _ctx(conn))


async def test_callback_snooze_missing_axis(conn: sqlite3.Connection) -> None:
    update = _callback_update(user_id=1, data="v:sn")
    await handle_vote_callback(update, _ctx(conn))
    update.callback_query.answer.assert_awaited()


async def test_callback_snooze_invalid_axis(conn: sqlite3.Connection) -> None:
    update = _callback_update(user_id=1, data="v:sn:bogus")
    await handle_vote_callback(update, _ctx(conn))
    update.callback_query.answer.assert_awaited()


async def test_callback_vote_too_few_parts(conn: sqlite3.Connection) -> None:
    update = _callback_update(user_id=1, data="v:a:1:2")
    await handle_vote_callback(update, _ctx(conn))
    update.callback_query.answer.assert_awaited()


async def test_callback_vote_non_int_players(conn: sqlite3.Connection) -> None:
    update = _callback_update(user_id=1, data="v:a:x:y:attack")
    await handle_vote_callback(update, _ctx(conn))
    update.callback_query.answer.assert_awaited()


async def test_callback_vote_invalid_axis(conn: sqlite3.Connection) -> None:
    update = _callback_update(user_id=1, data="v:a:1:2:bogus")
    await handle_vote_callback(update, _ctx(conn))
    update.callback_query.answer.assert_awaited()


async def test_callback_unknown_action(conn: sqlite3.Connection) -> None:
    update = _callback_update(user_id=1, data="v:zzz")
    await handle_vote_callback(update, _ctx(conn))
    update.callback_query.answer.assert_awaited()


async def test_start_in_dm(conn: sqlite3.Connection) -> None:
    update = _dm_update(user_id=1)
    await handle_start(update, _ctx(conn))
    update.effective_message.reply_text.assert_awaited_once_with(START_DM)


async def test_start_in_group(conn: sqlite3.Connection) -> None:
    update = _group_update(user_id=1)
    await handle_start(update, _ctx(conn))
    update.effective_message.reply_text.assert_awaited_once_with(START_GROUP)


async def test_start_returns_without_message(conn: sqlite3.Connection) -> None:
    u = MagicMock()
    u.effective_message = None
    u.effective_chat = MagicMock()
    await handle_start(u, _ctx(conn))


async def test_nudge_returns_without_message(
    conn: sqlite3.Connection, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("toop.admin.settings", MagicMock(ADMIN_TELEGRAM_ID=42))
    await handle_nudge(_admin_update(with_message=False), _ctx(conn))


async def test_nudge_empty_roster(
    conn: sqlite3.Connection, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("toop.admin.settings", MagicMock(ADMIN_TELEGRAM_ID=42))
    update = _admin_update()
    await handle_nudge(update, _ctx(conn))
    reply = update.effective_message.reply_text.await_args.args[0]
    assert "No players" in reply


async def test_nudge_with_players(
    conn: sqlite3.Connection, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("toop.admin.settings", MagicMock(ADMIN_TELEGRAM_ID=42))
    _seed(conn)
    update = _admin_update()
    await handle_nudge(update, _ctx(conn))
    reply = update.effective_message.reply_text.await_args.args[0]
    assert "nudge" in reply.lower()
    assert "Alice" in reply


def _prompt(player_a: int, player_b: int) -> Prompt:
    return Prompt(voter_id=1, player_a=player_a, player_b=player_b, axis="attack", info_gain=1.0)


def _patch_queue(monkeypatch: pytest.MonkeyPatch, prompt: Prompt | None) -> None:
    monkeypatch.setattr("toop.handlers.voting.refill_queue", lambda *a, **k: None)
    monkeypatch.setattr("toop.handlers.voting.peek_next_prompt", lambda *a, **k: prompt)


async def test_send_next_prompt_none_edits_message(
    conn: sqlite3.Connection, monkeypatch: pytest.MonkeyPatch
) -> None:
    _patch_queue(monkeypatch, None)
    ctx = _ctx(conn)
    await _send_next_prompt(conn, ctx, chat_id=1, voter_id=1, edit_message_id=5)
    ctx.bot.edit_message_text.assert_awaited_once()
    ctx.bot.send_message.assert_not_awaited()


async def test_send_next_prompt_none_edit_fails_falls_back_to_send(
    conn: sqlite3.Connection, monkeypatch: pytest.MonkeyPatch
) -> None:
    _patch_queue(monkeypatch, None)
    ctx = _ctx(conn)
    ctx.bot.edit_message_text = AsyncMock(side_effect=BadRequest("boom"))
    await _send_next_prompt(conn, ctx, chat_id=1, voter_id=1, edit_message_id=5)
    ctx.bot.send_message.assert_awaited_once()


async def test_send_next_prompt_none_no_edit_sends(
    conn: sqlite3.Connection, monkeypatch: pytest.MonkeyPatch
) -> None:
    _patch_queue(monkeypatch, None)
    ctx = _ctx(conn)
    await _send_next_prompt(conn, ctx, chat_id=1, voter_id=1)
    ctx.bot.send_message.assert_awaited_once()
    assert ctx.bot.send_message.await_args.kwargs["text"] == NO_PROMPTS_REPLY


async def test_send_next_prompt_missing_players(
    conn: sqlite3.Connection, monkeypatch: pytest.MonkeyPatch
) -> None:
    _patch_queue(monkeypatch, _prompt(111, 222))  # players not on roster
    ctx = _ctx(conn)
    await _send_next_prompt(conn, ctx, chat_id=1, voter_id=1)
    assert ctx.bot.send_message.await_args.kwargs["text"] == NO_PROMPTS_REPLY


async def test_send_next_prompt_edit_with_prompt(
    conn: sqlite3.Connection, monkeypatch: pytest.MonkeyPatch
) -> None:
    _seed(conn)
    _patch_queue(monkeypatch, _prompt(1, 2))
    ctx = _ctx(conn)
    await _send_next_prompt(conn, ctx, chat_id=1, voter_id=1, edit_message_id=5)
    ctx.bot.edit_message_text.assert_awaited_once()


async def test_send_next_prompt_edit_fails_with_prompt_sends(
    conn: sqlite3.Connection, monkeypatch: pytest.MonkeyPatch
) -> None:
    _seed(conn)
    _patch_queue(monkeypatch, _prompt(1, 2))
    ctx = _ctx(conn)
    ctx.bot.edit_message_text = AsyncMock(side_effect=BadRequest("boom"))
    await _send_next_prompt(conn, ctx, chat_id=1, voter_id=1, edit_message_id=5)
    ctx.bot.send_message.assert_awaited_once()
