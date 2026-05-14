from __future__ import annotations

import sqlite3
from unittest.mock import AsyncMock, MagicMock

import pytest
from telegram.error import BadRequest

from toop.handlers.roster import (
    handle_add_player,
    handle_list_players,
    handle_remove_player,
)
from toop.players import list_active_players


@pytest.fixture
def admin_settings(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("toop.admin.settings", MagicMock(ADMIN_TELEGRAM_ID=42))


def _admin_update(text: str) -> MagicMock:
    u = MagicMock()
    u.effective_user = MagicMock(id=42)
    msg = MagicMock()
    msg.text = text
    msg.reply_text = AsyncMock()
    u.effective_message = msg
    return u


def _context(conn: sqlite3.Connection, args: list[str], chat_id: int | None = None) -> MagicMock:
    ctx = MagicMock()
    ctx.bot_data = {"conn": conn}
    ctx.args = args
    bot = MagicMock()
    if chat_id is None:
        bot.get_chat = AsyncMock(side_effect=BadRequest("chat not found"))
    else:
        bot.get_chat = AsyncMock(return_value=MagicMock(id=chat_id))
    ctx.bot = bot
    return ctx


async def test_add_player_round_trip(
    admin_settings: None, conn: sqlite3.Connection
) -> None:
    update = _admin_update('/add_player @alice "Alice Smith"')
    ctx = _context(conn, args=["@alice", '"Alice', 'Smith"'], chat_id=111)
    await handle_add_player(update, ctx)
    players = list_active_players(conn)
    assert len(players) == 1
    assert players[0].telegram_id == 111
    assert players[0].display_name == "Alice Smith"
    update.effective_message.reply_text.assert_awaited_once()


async def test_add_player_unknown_username(
    admin_settings: None, conn: sqlite3.Connection
) -> None:
    update = _admin_update('/add_player @ghost "Ghost"')
    ctx = _context(conn, args=["@ghost", '"Ghost"'], chat_id=None)
    await handle_add_player(update, ctx)
    assert list_active_players(conn) == []
    reply = update.effective_message.reply_text.await_args.args[0]
    assert "DM me /start" in reply


async def test_add_player_bad_usage(
    admin_settings: None, conn: sqlite3.Connection
) -> None:
    update = _admin_update("/add_player @alice")
    ctx = _context(conn, args=["@alice"])
    await handle_add_player(update, ctx)
    update.effective_message.reply_text.assert_awaited_once()
    reply = update.effective_message.reply_text.await_args.args[0]
    assert reply.startswith("Usage:")


async def test_remove_player_round_trip(
    admin_settings: None, conn: sqlite3.Connection
) -> None:
    add_update = _admin_update('/add_player @alice "Alice"')
    add_ctx = _context(conn, args=["@alice", '"Alice"'], chat_id=111)
    await handle_add_player(add_update, add_ctx)

    remove_update = _admin_update("/remove_player @alice")
    remove_ctx = _context(conn, args=["@alice"], chat_id=111)
    await handle_remove_player(remove_update, remove_ctx)
    assert list_active_players(conn) == []


async def test_list_players_empty(
    admin_settings: None, conn: sqlite3.Connection
) -> None:
    update = _admin_update("/list_players")
    ctx = _context(conn, args=[])
    await handle_list_players(update, ctx)
    reply = update.effective_message.reply_text.await_args.args[0]
    assert "empty" in reply.lower()


async def test_list_players_with_calibration_marker(
    admin_settings: None, conn: sqlite3.Connection
) -> None:
    add_update = _admin_update('/add_player @alice "Alice"')
    add_ctx = _context(conn, args=["@alice", '"Alice"'], chat_id=111)
    await handle_add_player(add_update, add_ctx)

    list_update = _admin_update("/list_players")
    list_ctx = _context(conn, args=[])
    await handle_list_players(list_update, list_ctx)
    reply = list_update.effective_message.reply_text.await_args.args[0]
    assert "Alice" in reply
    assert "calibrating" in reply


async def test_non_admin_blocked(monkeypatch: pytest.MonkeyPatch, conn: sqlite3.Connection) -> None:
    monkeypatch.setattr("toop.admin.settings", MagicMock(ADMIN_TELEGRAM_ID=42))
    update = _admin_update('/add_player @alice "Alice"')
    update.effective_user = MagicMock(id=99)
    ctx = _context(conn, args=["@alice", '"Alice"'], chat_id=111)
    await handle_add_player(update, ctx)
    assert list_active_players(conn) == []
