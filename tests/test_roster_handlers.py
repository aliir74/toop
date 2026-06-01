from __future__ import annotations

import sqlite3
from unittest.mock import AsyncMock, MagicMock

import pytest
from telegram.error import BadRequest

from toop.contacts import upsert_contact
from toop.handlers.roster import (
    handle_add_player,
    handle_contacts,
    handle_list_players,
    handle_remove_player,
)
from toop.players import add_player, list_active_players


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


async def test_add_player_round_trip(admin_settings: None, conn: sqlite3.Connection) -> None:
    update = _admin_update('/add_player @alice "Alice Smith"')
    ctx = _context(conn, args=["@alice", '"Alice', 'Smith"'], chat_id=111)
    await handle_add_player(update, ctx)
    players = list_active_players(conn)
    assert len(players) == 1
    assert players[0].telegram_id == 111
    assert players[0].display_name == "Alice Smith"
    update.effective_message.reply_text.assert_awaited_once()


async def test_add_player_unknown_username(admin_settings: None, conn: sqlite3.Connection) -> None:
    update = _admin_update('/add_player @ghost "Ghost"')
    ctx = _context(conn, args=["@ghost", '"Ghost"'], chat_id=None)
    await handle_add_player(update, ctx)
    assert list_active_players(conn) == []
    reply = update.effective_message.reply_text.await_args.args[0]
    assert "DM me /start" in reply


async def test_add_player_bad_usage(admin_settings: None, conn: sqlite3.Connection) -> None:
    update = _admin_update("/add_player @alice")
    ctx = _context(conn, args=["@alice"])
    await handle_add_player(update, ctx)
    update.effective_message.reply_text.assert_awaited_once()
    reply = update.effective_message.reply_text.await_args.args[0]
    assert reply.startswith("Usage:")


async def test_remove_player_round_trip(admin_settings: None, conn: sqlite3.Connection) -> None:
    add_update = _admin_update('/add_player @alice "Alice"')
    add_ctx = _context(conn, args=["@alice", '"Alice"'], chat_id=111)
    await handle_add_player(add_update, add_ctx)

    remove_update = _admin_update("/remove_player @alice")
    remove_ctx = _context(conn, args=["@alice"], chat_id=111)
    await handle_remove_player(remove_update, remove_ctx)
    assert list_active_players(conn) == []


async def test_list_players_empty(admin_settings: None, conn: sqlite3.Connection) -> None:
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


# ----- branch coverage additions -----

from toop.handlers.roster import _conn, _parse_add_args  # noqa: E402


def test_conn_raises_when_missing() -> None:
    ctx = MagicMock()
    ctx.bot_data = {}
    with pytest.raises(RuntimeError, match="DB connection missing"):
        _conn(ctx)


def test_parse_add_args_unbalanced_quote() -> None:
    assert _parse_add_args('/add_player @alice "Unclosed') is None


def test_parse_add_args_too_few_tokens() -> None:
    assert _parse_add_args("/add_player @alice") is None


def test_parse_add_args_empty_username() -> None:
    assert _parse_add_args('/add_player @ "Name"') is None


async def test_add_player_no_text(admin_settings: None, conn: sqlite3.Connection) -> None:
    update = _admin_update('/add_player @a "A"')
    update.effective_message.text = None
    await handle_add_player(update, _context(conn, args=["@a"], chat_id=111))
    update.effective_message.reply_text.assert_not_called()


async def test_add_player_revives_soft_deleted(
    admin_settings: None, conn: sqlite3.Connection
) -> None:
    await handle_add_player(
        _admin_update('/add_player @alice "Alice"'),
        _context(conn, args=["@alice", '"Alice"'], chat_id=111),
    )
    await handle_remove_player(
        _admin_update("/remove_player @alice"),
        _context(conn, args=["@alice"], chat_id=111),
    )
    update = _admin_update('/add_player @alice "Alice"')
    await handle_add_player(update, _context(conn, args=["@alice", '"Alice"'], chat_id=111))
    assert "revived" in update.effective_message.reply_text.await_args.args[0]


async def test_remove_player_returns_without_message(
    admin_settings: None, conn: sqlite3.Connection
) -> None:
    update = _admin_update("/remove_player @x")
    update.effective_message = None
    await handle_remove_player(update, _context(conn, args=["@x"], chat_id=111))


async def test_remove_player_no_args(admin_settings: None, conn: sqlite3.Connection) -> None:
    update = _admin_update("/remove_player")
    await handle_remove_player(update, _context(conn, args=[], chat_id=111))
    assert update.effective_message.reply_text.await_args.args[0].startswith("Usage")


async def test_remove_player_empty_username(admin_settings: None, conn: sqlite3.Connection) -> None:
    update = _admin_update("/remove_player @")
    await handle_remove_player(update, _context(conn, args=["@"], chat_id=111))
    assert update.effective_message.reply_text.await_args.args[0].startswith("Usage")


async def test_remove_player_unknown_username(
    admin_settings: None, conn: sqlite3.Connection
) -> None:
    update = _admin_update("/remove_player @ghost")
    await handle_remove_player(update, _context(conn, args=["@ghost"], chat_id=None))
    assert "Couldn't find" in update.effective_message.reply_text.await_args.args[0]


async def test_remove_player_not_on_roster(admin_settings: None, conn: sqlite3.Connection) -> None:
    update = _admin_update("/remove_player @ghost")
    await handle_remove_player(update, _context(conn, args=["@ghost"], chat_id=222))
    assert "wasn't in the active roster" in update.effective_message.reply_text.await_args.args[0]


async def test_list_players_returns_without_message(
    admin_settings: None, conn: sqlite3.Connection
) -> None:
    update = _admin_update("/list_players")
    update.effective_message = None
    await handle_list_players(update, _context(conn, args=[]))


# ----- /contacts -----


async def test_contacts_empty(admin_settings: None, conn: sqlite3.Connection) -> None:
    update = _admin_update("/contacts")
    await handle_contacts(update, _context(conn, args=[]))
    reply = update.effective_message.reply_text.await_args.args[0]
    assert "Nobody has DM'd me yet" in reply


async def test_contacts_flags_non_roster(admin_settings: None, conn: sqlite3.Connection) -> None:
    # Bob is on the roster; Newbie has only DM'd the bot.
    add_player(conn, 111, "Bob", "bob")
    upsert_contact(conn, 111, username="bob", display_name="Bob")
    upsert_contact(conn, 222, username="newbie", display_name="New Bie")

    update = _admin_update("/contacts")
    await handle_contacts(update, _context(conn, args=[]))
    reply = update.effective_message.reply_text.await_args.args[0]
    assert "@bob" in reply
    assert "@newbie" in reply
    # Only the non-roster contact is flagged.
    assert reply.count("🆕 not on roster") == 1
    assert "available to /add_player (1" in reply


async def test_contacts_all_on_roster_no_flag(
    admin_settings: None, conn: sqlite3.Connection
) -> None:
    add_player(conn, 111, "Bob", "bob")
    upsert_contact(conn, 111, username="bob", display_name="Bob")
    update = _admin_update("/contacts")
    await handle_contacts(update, _context(conn, args=[]))
    reply = update.effective_message.reply_text.await_args.args[0]
    assert "not on roster" not in reply
    assert "available to /add_player" not in reply


async def test_contacts_returns_without_message(
    admin_settings: None, conn: sqlite3.Connection
) -> None:
    update = _admin_update("/contacts")
    update.effective_message = None
    await handle_contacts(update, _context(conn, args=[]))
