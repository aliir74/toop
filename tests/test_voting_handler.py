from __future__ import annotations

import sqlite3
from unittest.mock import AsyncMock, MagicMock

import pytest
from telegram.constants import ChatType
from telegram.error import BadRequest, Forbidden

from toop.config import settings
from toop.handlers.voting import (
    _conn,
    _send_next_prompt,
    handle_nudge,
    handle_start,
    handle_vote_callback,
    handle_vote_command,
)
from toop.i18n import t
from toop.players import add_player
from toop.voting_queue import ScoreTarget

GROUP_VOTE_DM_NUDGE = t("vote.group_dm_nudge", "en")
NO_PROMPTS_REPLY = t("vote.no_prompts", "en")
START_DM = t("vote.start_dm", "en")
START_GROUP = t("vote.start_group", "en")


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
    msg.chat = MagicMock(id=user_id)
    msg.photo = []  # text prompt: no photo (real Message.photo is an empty tuple)
    q.message = msg
    u.callback_query = q
    return u


async def test_vote_in_group_dms_roster_player_and_leaves_no_reply(
    conn: sqlite3.Connection,
) -> None:
    _seed(conn)
    update = _group_update(user_id=1)
    ctx = _ctx(conn)
    await handle_vote_command(update, ctx)
    update.effective_message.reply_text.assert_not_awaited()
    ctx.bot.send_message.assert_awaited()
    assert ctx.bot.send_message.await_args.kwargs["chat_id"] == 1
    assert "Rate" in ctx.bot.send_message.await_args.kwargs["text"]
    ctx.bot.delete_message.assert_awaited_once_with(chat_id=-100123, message_id=555)
    ctx.job_queue.run_once.assert_not_called()


async def test_vote_in_group_dms_nudge_to_non_roster_starter(conn: sqlite3.Connection) -> None:
    update = _group_update(user_id=999)
    ctx = _ctx(conn)
    await handle_vote_command(update, ctx)
    update.effective_message.reply_text.assert_not_awaited()
    ctx.bot.send_message.assert_awaited_once_with(chat_id=999, text=GROUP_VOTE_DM_NUDGE)
    ctx.bot.delete_message.assert_awaited_once()
    ctx.job_queue.run_once.assert_not_called()


def _send_message_dm_blocked() -> AsyncMock:
    async def _impl(*_args: object, chat_id: int, **_kwargs: object) -> MagicMock:
        if chat_id > 0:
            raise Forbidden("bot can't initiate conversation with a user")
        return MagicMock(message_id=777)

    return AsyncMock(side_effect=_impl)


async def test_vote_in_group_dm_forbidden_posts_self_deleting_nudge(
    conn: sqlite3.Connection,
) -> None:
    _seed(conn)
    update = _group_update(user_id=1, username="alice")
    ctx = _ctx(conn)
    ctx.bot.send_message = _send_message_dm_blocked()
    await handle_vote_command(update, ctx)
    update.effective_message.reply_text.assert_not_awaited()
    last = ctx.bot.send_message.await_args
    assert last.kwargs["chat_id"] == -100123
    assert "@alice" in last.kwargs["text"]
    assert "reply_to_message_id" not in last.kwargs
    ctx.bot.delete_message.assert_awaited()
    ctx.job_queue.run_once.assert_called_once()
    assert ctx.job_queue.run_once.call_args.kwargs["data"] == (-100123, 777)


async def test_vote_in_group_nudge_send_failure_is_swallowed(conn: sqlite3.Connection) -> None:
    _seed(conn)
    update = _group_update(user_id=1, username="alice")
    ctx = _ctx(conn)
    ctx.bot.send_message = AsyncMock(side_effect=Forbidden("can't send anywhere"))
    await handle_vote_command(update, ctx)
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
    _seed(conn)
    update = _group_update(user_id=1)
    ctx = _ctx(conn)
    ctx.bot.delete_message = AsyncMock(side_effect=BadRequest("message can't be deleted"))
    await handle_vote_command(update, ctx)
    ctx.bot.send_message.assert_awaited()


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
    await _delete_message_job(ctx)


async def test_vote_in_dm_sends_prompt(conn: sqlite3.Connection) -> None:
    _seed(conn)
    update = _dm_update(user_id=1)
    ctx = _ctx(conn)
    await handle_vote_command(update, ctx)
    ctx.bot.send_message.assert_awaited()
    kwargs = ctx.bot.send_message.await_args.kwargs
    assert "Rate" in kwargs["text"]


async def test_vote_for_non_roster_user(conn: sqlite3.Connection) -> None:
    update = _dm_update(user_id=999)
    ctx = _ctx(conn)
    await handle_vote_command(update, ctx)
    update.effective_message.reply_text.assert_awaited_once()
    reply = update.effective_message.reply_text.await_args.args[0]
    assert "not on the roster" in reply.lower()


async def test_callback_score_records_and_advances(conn: sqlite3.Connection) -> None:
    _seed(conn)
    update = _callback_update(user_id=1, data="v:s:2:atk:5")
    ctx = _ctx(conn)
    await handle_vote_callback(update, ctx)
    row = conn.execute(
        "SELECT score FROM scores WHERE voter_id=1 AND player_id=2 AND indicator='attack'"
    ).fetchone()
    assert row["score"] == 5
    update.callback_query.answer.assert_awaited()
    # Advances to a different player (Bob excluded), so the edited prompt isn't Bob.
    edited = ctx.bot.edit_message_text.await_args.kwargs["text"]
    assert "Bob" not in edited


async def test_callback_score_is_editable(conn: sqlite3.Connection) -> None:
    _seed(conn)
    await handle_vote_callback(_callback_update(1, "v:s:2:atk:5"), _ctx(conn))
    await handle_vote_callback(_callback_update(1, "v:s:2:atk:1"), _ctx(conn))
    rows = conn.execute(
        "SELECT score FROM scores WHERE voter_id=1 AND player_id=2 AND indicator='attack'"
    ).fetchall()
    assert len(rows) == 1
    assert rows[0]["score"] == 1


async def test_callback_dk_records_skip(conn: sqlite3.Connection) -> None:
    _seed(conn)
    await handle_vote_callback(_callback_update(1, "v:dk:2:atk"), _ctx(conn))
    row = conn.execute(
        "SELECT 1 FROM score_skips WHERE voter_id=1 AND player_id=2 AND indicator='attack'"
    ).fetchone()
    assert row is not None


async def test_callback_skip_advances_without_recording(conn: sqlite3.Connection) -> None:
    _seed(conn)
    ctx = _ctx(conn)
    await handle_vote_callback(_callback_update(1, "v:sk:2"), ctx)
    # Nothing recorded.
    assert conn.execute("SELECT COUNT(*) AS n FROM scores").fetchone()["n"] == 0
    assert conn.execute("SELECT COUNT(*) AS n FROM score_skips").fetchone()["n"] == 0
    # Next prompt avoids the skipped player.
    edited = ctx.bot.edit_message_text.await_args.kwargs["text"]
    assert "Bob" not in edited


async def test_callback_skip_missing_player(conn: sqlite3.Connection) -> None:
    update = _callback_update(user_id=1, data="v:sk")
    await handle_vote_callback(update, _ctx(conn))
    update.callback_query.answer.assert_awaited()


async def test_callback_skip_non_int_player(conn: sqlite3.Connection) -> None:
    update = _callback_update(user_id=1, data="v:sk:x")
    await handle_vote_callback(update, _ctx(conn))
    update.callback_query.answer.assert_awaited()


async def test_callback_score_too_few_parts(conn: sqlite3.Connection) -> None:
    update = _callback_update(user_id=1, data="v:s:2:atk")
    await handle_vote_callback(update, _ctx(conn))
    update.callback_query.answer.assert_awaited()


async def test_callback_dk_too_few_parts(conn: sqlite3.Connection) -> None:
    update = _callback_update(user_id=1, data="v:dk:2")
    await handle_vote_callback(update, _ctx(conn))
    update.callback_query.answer.assert_awaited()


async def test_callback_score_non_int_player(conn: sqlite3.Connection) -> None:
    update = _callback_update(user_id=1, data="v:s:x:atk:5")
    await handle_vote_callback(update, _ctx(conn))
    update.callback_query.answer.assert_awaited()


async def test_callback_score_bad_indicator_code(conn: sqlite3.Connection) -> None:
    update = _callback_update(user_id=1, data="v:s:2:zzz:5")
    await handle_vote_callback(update, _ctx(conn))
    update.callback_query.answer.assert_awaited()


async def test_callback_score_non_int_score(conn: sqlite3.Connection) -> None:
    update = _callback_update(user_id=1, data="v:s:2:atk:x")
    await handle_vote_callback(update, _ctx(conn))
    update.callback_query.answer.assert_awaited()


async def test_callback_score_out_of_range(conn: sqlite3.Connection) -> None:
    _seed(conn)
    update = _callback_update(user_id=1, data="v:s:2:atk:9")
    await handle_vote_callback(update, _ctx(conn))
    update.callback_query.answer.assert_awaited()
    assert conn.execute("SELECT COUNT(*) AS n FROM scores").fetchone()["n"] == 0


async def test_callback_unknown_action(conn: sqlite3.Connection) -> None:
    update = _callback_update(user_id=1, data="v:zzz")
    await handle_vote_callback(update, _ctx(conn))
    update.callback_query.answer.assert_awaited()


async def test_conn_raises_when_missing() -> None:
    ctx = MagicMock()
    ctx.bot_data = {}
    with pytest.raises(RuntimeError, match="DB connection missing"):
        _conn(ctx)


async def test_vote_command_returns_without_message(conn: sqlite3.Connection) -> None:
    u = MagicMock()
    u.effective_message = None
    u.effective_chat = MagicMock()
    u.effective_user = MagicMock(id=1)
    await handle_vote_command(u, _ctx(conn))


async def test_callback_returns_without_query(conn: sqlite3.Connection) -> None:
    u = MagicMock()
    u.callback_query = None
    await handle_vote_callback(u, _ctx(conn))


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


def _open_attendance_poll(conn: sqlite3.Connection) -> None:
    conn.execute("INSERT INTO sessions (session_date, status) VALUES ('2099-01-01', 'open')")
    conn.commit()
    sid = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
    conn.execute(
        "INSERT INTO session_polls (poll_id, session_id, kind) VALUES ('p1', ?, 'attendance')",
        (sid,),
    )
    conn.commit()


async def test_start_new_contact_with_open_poll_sends_group_notice(
    conn: sqlite3.Connection, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(settings, "GROUP_CHAT_ID", -100456)
    _open_attendance_poll(conn)
    ctx = _ctx(conn)
    await handle_start(_dm_update(user_id=77), ctx)
    ctx.bot.send_message.assert_awaited_once()
    args = ctx.bot.send_message.await_args
    assert args.kwargs["chat_id"] == -100456
    assert "User 77" in args.kwargs["text"]


async def test_start_new_contact_no_open_poll_no_group_notice(
    conn: sqlite3.Connection, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(settings, "GROUP_CHAT_ID", -100456)
    # No session / poll in DB.
    ctx = _ctx(conn)
    await handle_start(_dm_update(user_id=77), ctx)
    ctx.bot.send_message.assert_not_awaited()


async def test_start_existing_contact_no_group_notice(
    conn: sqlite3.Connection, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(settings, "GROUP_CHAT_ID", -100456)
    _open_attendance_poll(conn)
    ctx = _ctx(conn)
    # First /start registers the contact.
    await handle_start(_dm_update(user_id=77), ctx)
    ctx.bot.send_message.reset_mock()
    # Second /start: already known, no group notice.
    await handle_start(_dm_update(user_id=77), ctx)
    ctx.bot.send_message.assert_not_awaited()


async def test_notify_group_new_contact_swallows_send_failure(
    conn: sqlite3.Connection, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(settings, "GROUP_CHAT_ID", -100456)
    _open_attendance_poll(conn)
    ctx = _ctx(conn)
    ctx.bot.send_message = AsyncMock(side_effect=Exception("network error"))
    # Must not raise even if send_message fails.
    await handle_start(_dm_update(user_id=77), ctx)


def _patch_selector(monkeypatch: pytest.MonkeyPatch, target: ScoreTarget | None) -> None:
    monkeypatch.setattr("toop.handlers.voting.select_next_score_target", lambda *a, **k: target)


async def test_send_next_prompt_none_edits_message(
    conn: sqlite3.Connection, monkeypatch: pytest.MonkeyPatch
) -> None:
    _patch_selector(monkeypatch, None)
    ctx = _ctx(conn)
    await _send_next_prompt(conn, ctx, chat_id=1, voter_id=1, edit_message_id=5)
    ctx.bot.edit_message_text.assert_awaited_once()
    ctx.bot.send_message.assert_not_awaited()


async def test_send_next_prompt_none_edit_fails_falls_back_to_send(
    conn: sqlite3.Connection, monkeypatch: pytest.MonkeyPatch
) -> None:
    _patch_selector(monkeypatch, None)
    ctx = _ctx(conn)
    ctx.bot.edit_message_text = AsyncMock(side_effect=BadRequest("boom"))
    await _send_next_prompt(conn, ctx, chat_id=1, voter_id=1, edit_message_id=5)
    ctx.bot.send_message.assert_awaited_once()


async def test_send_next_prompt_none_no_edit_sends(
    conn: sqlite3.Connection, monkeypatch: pytest.MonkeyPatch
) -> None:
    _patch_selector(monkeypatch, None)
    ctx = _ctx(conn)
    await _send_next_prompt(conn, ctx, chat_id=1, voter_id=1)
    ctx.bot.send_message.assert_awaited_once()
    assert ctx.bot.send_message.await_args.kwargs["text"] == NO_PROMPTS_REPLY


async def test_send_next_prompt_missing_player(
    conn: sqlite3.Connection, monkeypatch: pytest.MonkeyPatch
) -> None:
    _patch_selector(monkeypatch, ScoreTarget(player_id=111, indicator="attack"))
    ctx = _ctx(conn)
    await _send_next_prompt(conn, ctx, chat_id=1, voter_id=1)
    assert ctx.bot.send_message.await_args.kwargs["text"] == NO_PROMPTS_REPLY


async def test_send_next_prompt_edit_with_target(
    conn: sqlite3.Connection, monkeypatch: pytest.MonkeyPatch
) -> None:
    _seed(conn)
    _patch_selector(monkeypatch, ScoreTarget(player_id=2, indicator="attack"))
    ctx = _ctx(conn)
    await _send_next_prompt(conn, ctx, chat_id=1, voter_id=1, edit_message_id=5)
    ctx.bot.edit_message_text.assert_awaited_once()


async def test_send_next_prompt_edit_fails_with_target_sends(
    conn: sqlite3.Connection, monkeypatch: pytest.MonkeyPatch
) -> None:
    _seed(conn)
    _patch_selector(monkeypatch, ScoreTarget(player_id=2, indicator="attack"))
    ctx = _ctx(conn)
    ctx.bot.edit_message_text = AsyncMock(side_effect=BadRequest("boom"))
    await _send_next_prompt(conn, ctx, chat_id=1, voter_id=1, edit_message_id=5)
    ctx.bot.send_message.assert_awaited_once()
