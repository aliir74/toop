from __future__ import annotations

import sqlite3
from unittest.mock import AsyncMock, MagicMock

import pytest
from telegram.constants import ChatType

from toop.handlers.roster import (
    PENDING_RENAME_KEY,
    RENAME_EMPTY_ROSTER,
    _parse_rename_args,
    _player_label,
    handle_rename,
    handle_rename_callback,
    handle_rename_text,
)
from toop.players import add_player, list_active_players, soft_remove_player


@pytest.fixture
def admin_settings(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("toop.admin.settings", MagicMock(ADMIN_TELEGRAM_ID=42))


def _admin_update(text: str, chat_type: str = ChatType.PRIVATE) -> MagicMock:
    u = MagicMock()
    u.effective_user = MagicMock(id=42)
    chat = MagicMock()
    chat.type = chat_type
    u.effective_chat = chat
    msg = MagicMock()
    msg.text = text
    msg.reply_text = AsyncMock()
    u.effective_message = msg
    return u


def _callback_update(data: str) -> MagicMock:
    u = MagicMock()
    u.effective_user = MagicMock(id=42)
    msg = MagicMock()
    msg.reply_text = AsyncMock()
    u.effective_message = msg
    q = MagicMock()
    q.data = data
    q.answer = AsyncMock()
    q.edit_message_text = AsyncMock()
    u.callback_query = q
    return u


def _ctx(conn: sqlite3.Connection) -> MagicMock:
    ctx = MagicMock()
    ctx.bot_data = {"conn": conn}
    ctx.user_data = {}
    return ctx


# ----- helpers -----


def test_player_label_with_and_without_username() -> None:
    assert _player_label("Alice", "alice") == "Alice (@alice)"
    assert _player_label("SHH", None) == "SHH"


def test_parse_rename_args_username() -> None:
    assert _parse_rename_args('/rename @alice "Alice Smith"') == ("alice", "Alice Smith")


def test_parse_rename_args_numeric_id() -> None:
    assert _parse_rename_args('/rename 111 "New Name"') == (111, "New Name")


def test_parse_rename_args_too_few_tokens() -> None:
    assert _parse_rename_args("/rename @alice") is None


def test_parse_rename_args_unbalanced_quote() -> None:
    assert _parse_rename_args('/rename @alice "Unclosed') is None


def test_parse_rename_args_empty_name() -> None:
    assert _parse_rename_args('/rename @alice ""') is None


def test_parse_rename_args_empty_username() -> None:
    assert _parse_rename_args('/rename @ "Name"') is None


# ----- /rename (interactive list) -----


async def test_rename_lists_active_players(admin_settings: None, conn: sqlite3.Connection) -> None:
    add_player(conn, 1, "Alice", "alice")
    add_player(conn, 2, "SHH", None)
    update = _admin_update("/rename")
    await handle_rename(update, _ctx(conn))
    kwargs = update.effective_message.reply_text.await_args.kwargs
    keyboard = kwargs["reply_markup"].inline_keyboard
    labels = [btn.text for row in keyboard for btn in row]
    callbacks = [btn.callback_data for row in keyboard for btn in row]
    assert "Alice (@alice)" in labels
    assert "SHH" in labels
    assert "rename:1" in callbacks
    assert "rename:2" in callbacks


async def test_rename_empty_roster(admin_settings: None, conn: sqlite3.Connection) -> None:
    update = _admin_update("/rename")
    await handle_rename(update, _ctx(conn))
    assert update.effective_message.reply_text.await_args.args[0] == RENAME_EMPTY_ROSTER


async def test_rename_in_group_redirects_to_dm(
    admin_settings: None, conn: sqlite3.Connection
) -> None:
    add_player(conn, 1, "Alice", "alice")
    update = _admin_update("/rename", chat_type=ChatType.GROUP)
    await handle_rename(update, _ctx(conn))
    assert "DM me" in update.effective_message.reply_text.await_args.args[0]
    # No accidental rename / listing happened.
    assert list_active_players(conn)[0].display_name == "Alice"


async def test_rename_non_admin_rejected(
    monkeypatch: pytest.MonkeyPatch, conn: sqlite3.Connection
) -> None:
    monkeypatch.setattr("toop.admin.settings", MagicMock(ADMIN_TELEGRAM_ID=42))
    add_player(conn, 1, "Alice", "alice")
    update = _admin_update("/rename")
    update.effective_user = MagicMock(id=99)
    await handle_rename(update, _ctx(conn))
    reply = update.effective_message.reply_text.await_args.args[0]
    assert "admin-only" in reply


async def test_rename_no_message_returns(admin_settings: None, conn: sqlite3.Connection) -> None:
    update = _admin_update("/rename")
    update.effective_message = None
    await handle_rename(update, _ctx(conn))  # no crash, no DB change


# ----- callback: pick a player -----


async def test_rename_callback_sets_pending(admin_settings: None, conn: sqlite3.Connection) -> None:
    add_player(conn, 111, "H P", "hp")
    update = _callback_update("rename:111")
    ctx = _ctx(conn)
    await handle_rename_callback(update, ctx)
    assert ctx.user_data[PENDING_RENAME_KEY] == 111
    update.callback_query.answer.assert_awaited()
    prompt = update.callback_query.edit_message_text.await_args.args[0]
    assert "H P" in prompt


async def test_rename_callback_invalid_id(admin_settings: None, conn: sqlite3.Connection) -> None:
    update = _callback_update("rename:notanint")
    ctx = _ctx(conn)
    await handle_rename_callback(update, ctx)
    assert PENDING_RENAME_KEY not in ctx.user_data
    update.callback_query.answer.assert_awaited()


async def test_rename_callback_player_removed(
    admin_settings: None, conn: sqlite3.Connection
) -> None:
    update = _callback_update("rename:999")
    ctx = _ctx(conn)
    await handle_rename_callback(update, ctx)
    assert PENDING_RENAME_KEY not in ctx.user_data
    update.callback_query.answer.assert_awaited()


async def test_rename_callback_returns_without_query(
    admin_settings: None, conn: sqlite3.Connection
) -> None:
    update = MagicMock()
    update.effective_user = MagicMock(id=42)
    update.callback_query = None
    await handle_rename_callback(update, _ctx(conn))  # silent return


# ----- text: apply the new name -----


async def test_rename_text_applies_and_clears(
    admin_settings: None, conn: sqlite3.Connection
) -> None:
    add_player(conn, 111, "H P", "hp")
    update = _admin_update("Hamed Pour")
    ctx = _ctx(conn)
    ctx.user_data[PENDING_RENAME_KEY] = 111
    await handle_rename_text(update, ctx)
    assert list_active_players(conn)[0].display_name == "Hamed Pour"
    assert PENDING_RENAME_KEY not in ctx.user_data
    assert "Renamed H P → Hamed Pour" in update.effective_message.reply_text.await_args.args[0]


async def test_rename_text_no_pending_is_ignored(
    admin_settings: None, conn: sqlite3.Connection
) -> None:
    add_player(conn, 111, "H P", "hp")
    update = _admin_update("some random chatter")
    await handle_rename_text(update, _ctx(conn))
    # Roster untouched and nothing was replied — normal messages aren't swallowed.
    assert list_active_players(conn)[0].display_name == "H P"
    update.effective_message.reply_text.assert_not_called()


async def test_rename_text_command_while_pending_cancels(
    admin_settings: None, conn: sqlite3.Connection
) -> None:
    add_player(conn, 111, "H P", "hp")
    update = _admin_update("/list_players")
    ctx = _ctx(conn)
    ctx.user_data[PENDING_RENAME_KEY] = 111
    await handle_rename_text(update, ctx)
    # Command not consumed as a name; pending cleared; roster unchanged.
    assert list_active_players(conn)[0].display_name == "H P"
    assert PENDING_RENAME_KEY not in ctx.user_data
    assert "cancelled" in update.effective_message.reply_text.await_args.args[0].lower()


async def test_rename_text_empty_keeps_pending(
    admin_settings: None, conn: sqlite3.Connection
) -> None:
    add_player(conn, 111, "H P", "hp")
    update = _admin_update("   ")
    ctx = _ctx(conn)
    ctx.user_data[PENDING_RENAME_KEY] = 111
    await handle_rename_text(update, ctx)
    assert ctx.user_data[PENDING_RENAME_KEY] == 111  # still pending
    assert list_active_players(conn)[0].display_name == "H P"
    assert "empty" in update.effective_message.reply_text.await_args.args[0].lower()


async def test_rename_text_player_removed_meanwhile(
    admin_settings: None, conn: sqlite3.Connection
) -> None:
    add_player(conn, 111, "H P", "hp")
    soft_remove_player(conn, 111)
    update = _admin_update("Hamed Pour")
    ctx = _ctx(conn)
    ctx.user_data[PENDING_RENAME_KEY] = 111
    await handle_rename_text(update, ctx)
    assert PENDING_RENAME_KEY not in ctx.user_data
    assert "no longer on the roster" in update.effective_message.reply_text.await_args.args[0]


async def test_rename_text_no_message_returns(
    admin_settings: None, conn: sqlite3.Connection
) -> None:
    update = _admin_update("x")
    update.effective_message = None
    await handle_rename_text(update, _ctx(conn))  # silent return


# ----- one-shot shortcut -----


async def test_rename_one_shot_by_username(admin_settings: None, conn: sqlite3.Connection) -> None:
    add_player(conn, 1, "Alice", "alice")
    update = _admin_update('/rename @alice "Alice Smith"')
    await handle_rename(update, _ctx(conn))
    assert list_active_players(conn)[0].display_name == "Alice Smith"
    assert "Renamed Alice → Alice Smith" in update.effective_message.reply_text.await_args.args[0]


async def test_rename_one_shot_by_id(admin_settings: None, conn: sqlite3.Connection) -> None:
    add_player(conn, 5299711301, "SHH", None)
    update = _admin_update('/rename 5299711301 "Shahin"')
    await handle_rename(update, _ctx(conn))
    assert list_active_players(conn)[0].display_name == "Shahin"


async def test_rename_one_shot_unknown_username(
    admin_settings: None, conn: sqlite3.Connection
) -> None:
    update = _admin_update('/rename @ghost "Ghost"')
    await handle_rename(update, _ctx(conn))
    assert "isn't on the active roster" in update.effective_message.reply_text.await_args.args[0]


async def test_rename_one_shot_unknown_id(admin_settings: None, conn: sqlite3.Connection) -> None:
    update = _admin_update('/rename 999 "Ghost"')
    await handle_rename(update, _ctx(conn))
    assert "No active player with id 999" in update.effective_message.reply_text.await_args.args[0]


async def test_rename_one_shot_bad_usage(admin_settings: None, conn: sqlite3.Connection) -> None:
    update = _admin_update('/rename "Unclosed')
    await handle_rename(update, _ctx(conn))
    assert update.effective_message.reply_text.await_args.args[0].startswith("Usage:")
