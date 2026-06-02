from __future__ import annotations

import sqlite3
from unittest.mock import AsyncMock, MagicMock

import pytest
from telegram.constants import ChatType
from telegram.error import BadRequest, Forbidden

from toop.handlers.voting import (
    GROUP_VOTE_DM_NUDGE,
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
    u.effective_user = MagicMock(id=user_id, username=f"user{user_id}", full_name=f"User {user_id}")
    chat = MagicMock(id=user_id)
    chat.type = ChatType.PRIVATE
    u.effective_chat = chat
    msg = MagicMock()
    msg.reply_text = AsyncMock()
    u.effective_message = msg
    return u


def _group_update(user_id: int, *, username: str | None = None, message_id: int = 555) -> MagicMock:
    u = MagicMock()
    u.effective_user = MagicMock(id=user_id, username=username, full_name=f"User {user_id}")
    chat = MagicMock(id=-100123)
    chat.type = ChatType.GROUP
    u.effective_chat = chat
    msg = MagicMock(message_id=message_id)
    msg.reply_text = AsyncMock()
    u.effective_message = msg
    return u


def _ctx(conn: sqlite3.Connection) -> MagicMock:
    ctx = MagicMock()
    ctx.bot_data = {"conn": conn}
    ctx.bot = MagicMock()
    ctx.bot.username = "toop_bot_bot"
    ctx.bot.send_message = AsyncMock()
    ctx.bot.edit_message_text = AsyncMock()
    ctx.bot.delete_message = AsyncMock()
    ctx.job_queue = MagicMock()
    ctx.job_queue.run_once = MagicMock()
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


async def test_vote_in_group_dms_roster_player_and_leaves_no_reply(
    conn: sqlite3.Connection,
) -> None:
    """Group /vote from a roster player: prompt goes to their DM, group stays clean."""
    _seed(conn)
    update = _group_update(user_id=1)
    ctx = _ctx(conn)
    await handle_vote_command(update, ctx)
    # Never quote/reply in the group.
    update.effective_message.reply_text.assert_not_awaited()
    # Prompt was DM'd to the sender's private chat.
    ctx.bot.send_message.assert_awaited()
    assert ctx.bot.send_message.await_args.kwargs["chat_id"] == 1
    assert "stronger" in ctx.bot.send_message.await_args.kwargs["text"]
    # The /vote command is removed from the group.
    ctx.bot.delete_message.assert_awaited_once_with(chat_id=-100123, message_id=555)
    # No transient group nudge was needed, so nothing is scheduled.
    ctx.job_queue.run_once.assert_not_called()


async def test_vote_in_group_dms_nudge_to_non_roster_starter(conn: sqlite3.Connection) -> None:
    """Sender not on roster but has started the bot: gets a DM nudge, no group reply."""
    update = _group_update(user_id=999)
    ctx = _ctx(conn)
    await handle_vote_command(update, ctx)
    update.effective_message.reply_text.assert_not_awaited()
    ctx.bot.send_message.assert_awaited_once_with(chat_id=999, text=GROUP_VOTE_DM_NUDGE)
    ctx.bot.delete_message.assert_awaited_once()
    ctx.job_queue.run_once.assert_not_called()


def _send_message_dm_blocked() -> AsyncMock:
    """send_message that raises Forbidden for DMs (positive chat_id) but works in groups."""

    async def _impl(*_args: object, chat_id: int, **_kwargs: object) -> MagicMock:
        if chat_id > 0:
            raise Forbidden("bot can't initiate conversation with a user")
        return MagicMock(message_id=777)

    return AsyncMock(side_effect=_impl)


async def test_vote_in_group_dm_forbidden_posts_self_deleting_nudge(
    conn: sqlite3.Connection,
) -> None:
    """When the bot can't DM the sender, it posts a transient group nudge that self-deletes."""
    _seed(conn)
    update = _group_update(user_id=1, username="alice")
    ctx = _ctx(conn)
    ctx.bot.send_message = _send_message_dm_blocked()
    await handle_vote_command(update, ctx)
    # No standing reply quoting the command.
    update.effective_message.reply_text.assert_not_awaited()
    # The last send_message call is the group nudge: targets the group, mentions sender,
    # and is NOT a quoting reply.
    last = ctx.bot.send_message.await_args
    assert last.kwargs["chat_id"] == -100123
    assert "@alice" in last.kwargs["text"]
    assert "reply_to_message_id" not in last.kwargs
    # Deletion of the /vote command was attempted.
    ctx.bot.delete_message.assert_awaited()
    # The transient nudge is scheduled for deletion.
    ctx.job_queue.run_once.assert_called_once()
    assert ctx.job_queue.run_once.call_args.kwargs["data"] == (-100123, 777)


async def test_vote_in_group_nudge_send_failure_is_swallowed(
    conn: sqlite3.Connection,
) -> None:
    """If even the transient group nudge can't be posted, don't raise or schedule a delete."""
    _seed(conn)
    update = _group_update(user_id=1, username="alice")
    ctx = _ctx(conn)
    # Every send_message fails — both the DM attempt and the group nudge.
    ctx.bot.send_message = AsyncMock(side_effect=Forbidden("can't send anywhere"))
    await handle_vote_command(update, ctx)  # no exception
    update.effective_message.reply_text.assert_not_awaited()
    ctx.job_queue.run_once.assert_not_called()


async def test_vote_in_group_dm_forbidden_uses_full_name_without_username(
    conn: sqlite3.Connection,
) -> None:
    update = _group_update(user_id=2)  # username=None
    ctx = _ctx(conn)
    ctx.bot.send_message = _send_message_dm_blocked()
    await handle_vote_command(update, ctx)
    last = ctx.bot.send_message.await_args
    assert "User 2" in last.kwargs["text"]


async def test_vote_in_group_swallows_delete_failure(conn: sqlite3.Connection) -> None:
    """Lacking delete permission must not raise — the group fix still works."""
    _seed(conn)
    update = _group_update(user_id=1)
    ctx = _ctx(conn)
    ctx.bot.delete_message = AsyncMock(side_effect=BadRequest("message can't be deleted"))
    await handle_vote_command(update, ctx)  # no exception
    ctx.bot.send_message.assert_awaited()  # DM still sent


async def test_delete_message_job_deletes_target(conn: sqlite3.Connection) -> None:
    from toop.handlers.voting import _delete_message_job

    ctx = _ctx(conn)
    ctx.job = MagicMock(data=(-100123, 555))
    await _delete_message_job(ctx)
    ctx.bot.delete_message.assert_awaited_once_with(chat_id=-100123, message_id=555)


async def test_delete_message_job_no_job_is_noop(conn: sqlite3.Connection) -> None:
    from toop.handlers.voting import _delete_message_job

    ctx = _ctx(conn)
    ctx.job = None
    await _delete_message_job(ctx)
    ctx.bot.delete_message.assert_not_awaited()


async def test_delete_message_job_swallows_failure(conn: sqlite3.Connection) -> None:
    from toop.handlers.voting import _delete_message_job

    ctx = _ctx(conn)
    ctx.job = MagicMock(data=(-100123, 555))
    ctx.bot.delete_message = AsyncMock(side_effect=Forbidden("no rights"))
    await _delete_message_job(ctx)  # no exception


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


async def test_callback_dk_counts_dont_know_no_winner(conn: sqlite3.Connection) -> None:
    _seed(conn)
    update = _callback_update(user_id=1, data="v:dk:2:3:attack")
    await handle_vote_callback(update, _ctx(conn))
    row = conn.execute(
        "SELECT a_wins, b_wins, dont_know FROM vote_aggregates "
        "WHERE player_a=2 AND player_b=3 AND axis='attack'"
    ).fetchone()
    assert (row["a_wins"], row["b_wins"], row["dont_know"]) == (0, 0, 1)
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


async def test_vote_callback_advances_to_different_pair(conn: sqlite3.Connection) -> None:
    """After a vote, the edited prompt shows a *different* pair when one exists.

    A bootstrapped pair (Bob vs Carol, 3 axes at top priority) plus filler pairs
    are queued. Voting Bob-vs-Carol-attack must surface a non-(Bob,Carol) pair
    next instead of re-showing the same two names with a new axis word.
    """
    from toop.voting_queue import insert_priority_prompt, refill_queue

    _seed(conn)  # players 1..4
    voter = 1
    for axis in ("attack", "defense", "setting"):
        insert_priority_prompt(conn, voter_id=voter, player_a=2, player_b=3, axis=axis)
    refill_queue(conn, voter, queue_depth=8)

    ctx = _ctx(conn)
    await handle_vote_callback(_callback_update(voter, "v:a:2:3:attack"), ctx)

    edited = ctx.bot.edit_message_text.await_args.kwargs["text"]
    # The next prompt must not be the just-answered Bob vs Carol pair.
    assert not ("Bob" in edited and "Carol" in edited)


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


async def test_start_in_dm_records_contact(conn: sqlite3.Connection) -> None:
    await handle_start(_dm_update(user_id=7), _ctx(conn))
    row = conn.execute("SELECT username, display_name FROM contacts WHERE telegram_id=7").fetchone()
    assert row is not None
    assert row["username"] == "user7"
    assert row["display_name"] == "User 7"


async def test_start_in_dm_repeat_keeps_single_contact(conn: sqlite3.Connection) -> None:
    await handle_start(_dm_update(user_id=7), _ctx(conn))
    await handle_start(_dm_update(user_id=7), _ctx(conn))
    count = conn.execute("SELECT COUNT(*) AS n FROM contacts WHERE telegram_id=7").fetchone()["n"]
    assert count == 1


async def test_start_in_group_records_no_contact(conn: sqlite3.Connection) -> None:
    await handle_start(_group_update(user_id=8), _ctx(conn))
    count = conn.execute("SELECT COUNT(*) AS n FROM contacts").fetchone()["n"]
    assert count == 0


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
