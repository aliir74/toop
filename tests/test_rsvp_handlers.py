from __future__ import annotations

import sqlite3
from datetime import date
from unittest.mock import AsyncMock, MagicMock

import pytest

from toop.handlers.rsvp import handle_lock_in
from toop.players import add_player
from toop.sessions import open_session


@pytest.fixture
def admin_settings(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("toop.admin.settings", MagicMock(ADMIN_TELEGRAM_ID=42))


def _ctx(conn: sqlite3.Connection, args: list[str] | None = None) -> MagicMock:
    ctx = MagicMock()
    ctx.bot_data = {"conn": conn}
    ctx.args = args or []
    return ctx


def _admin_update() -> MagicMock:
    u = MagicMock()
    u.effective_user = MagicMock(id=42)
    msg = MagicMock()
    msg.reply_text = AsyncMock()
    u.effective_message = msg
    return u


async def test_lock_in_admin(admin_settings: None, conn: sqlite3.Connection) -> None:
    sess = open_session(conn, date(2026, 5, 18))
    add_player(conn, 7, "Mehdi", "mehdi")
    update = _admin_update()
    await handle_lock_in(update, _ctx(conn, args=["@mehdi"]))
    row = conn.execute(
        "SELECT locked_in FROM rsvps WHERE session_id=? AND telegram_id=?",
        (sess.id, 7),
    ).fetchone()
    assert row is not None and row["locked_in"] == 1


async def test_lock_in_by_id_success(admin_settings: None, conn: sqlite3.Connection) -> None:
    sess = open_session(conn, date(2026, 5, 18))
    # No-username player — only reachable by numeric id.
    add_player(conn, 5299711301, "Hamzeh Hosseini", None)
    update = _admin_update()
    await handle_lock_in(update, _ctx(conn, args=["5299711301"]))
    row = conn.execute(
        "SELECT locked_in FROM rsvps WHERE session_id=? AND telegram_id=?",
        (sess.id, 5299711301),
    ).fetchone()
    assert row is not None and row["locked_in"] == 1
    reply = update.effective_message.reply_text.await_args.args[0]
    assert "Hamzeh Hosseini" in reply


async def test_lock_in_by_id_not_on_roster(admin_settings: None, conn: sqlite3.Connection) -> None:
    open_session(conn, date(2026, 5, 18))
    update = _admin_update()
    await handle_lock_in(update, _ctx(conn, args=["7290468940"]))
    reply = update.effective_message.reply_text.await_args.args[0]
    assert "isn't on the roster" in reply
    assert "/add_player" in reply


async def test_lock_in_unknown_username(admin_settings: None, conn: sqlite3.Connection) -> None:
    open_session(conn, date(2026, 5, 18))
    update = _admin_update()
    await handle_lock_in(update, _ctx(conn, args=["@ghost"]))
    reply = update.effective_message.reply_text.await_args.args[0]
    assert "isn't on the roster" in reply


async def test_lock_in_no_active_session(admin_settings: None, conn: sqlite3.Connection) -> None:
    add_player(conn, 7, "Mehdi", "mehdi")
    update = _admin_update()
    await handle_lock_in(update, _ctx(conn, args=["@mehdi"]))
    reply = update.effective_message.reply_text.await_args.args[0]
    assert "No active session" in reply


# ----- branch coverage additions -----

from toop.handlers.rsvp import _conn  # noqa: E402


def test_conn_raises_when_missing() -> None:
    ctx = MagicMock()
    ctx.bot_data = {}
    with pytest.raises(RuntimeError, match="DB connection missing"):
        _conn(ctx)


async def test_lock_in_returns_without_message(
    admin_settings: None, conn: sqlite3.Connection
) -> None:
    u = _admin_update()
    u.effective_message = None
    await handle_lock_in(u, _ctx(conn, args=["@x"]))


async def test_lock_in_no_args_no_session(admin_settings: None, conn: sqlite3.Connection) -> None:
    # No args + no active session → the button flow has nothing to lock into.
    update = _admin_update()
    await handle_lock_in(update, _ctx(conn, args=[]))
    assert "No active session" in update.effective_message.reply_text.await_args.args[0]


async def test_lock_in_empty_username(admin_settings: None, conn: sqlite3.Connection) -> None:
    update = _admin_update()
    await handle_lock_in(update, _ctx(conn, args=["@"]))
    assert "Usage" in update.effective_message.reply_text.await_args.args[0]


async def test_lock_in_failure_branch(
    admin_settings: None, conn: sqlite3.Connection, monkeypatch: pytest.MonkeyPatch
) -> None:
    open_session(conn, date(2026, 5, 18))
    add_player(conn, 7, "Mehdi", "mehdi")
    monkeypatch.setattr("toop.handlers.rsvp.lock_in_player", lambda *a, **k: False)
    update = _admin_update()
    await handle_lock_in(update, _ctx(conn, args=["@mehdi"]))
    assert "Couldn't lock" in update.effective_message.reply_text.await_args.args[0]


# ----- /lock_in buttons -----

from toop.handlers.rsvp import handle_lock_in_callback  # noqa: E402


def _admin_callback_update(data: str) -> MagicMock:
    u = MagicMock()
    u.effective_user = MagicMock(id=42)
    q = MagicMock()
    q.data = data
    q.answer = AsyncMock()
    q.edit_message_text = AsyncMock()
    u.callback_query = q
    return u


async def test_lock_in_no_args_lists_buttons(
    admin_settings: None, conn: sqlite3.Connection
) -> None:
    open_session(conn, date(2026, 5, 18))
    add_player(conn, 7, "Mehdi", "mehdi")
    update = _admin_update()
    await handle_lock_in(update, _ctx(conn, args=[]))
    kb = update.effective_message.reply_text.await_args.kwargs["reply_markup"].inline_keyboard
    assert "lockpick:7" in [b.callback_data for row in kb for b in row]


async def test_lock_in_no_args_empty_roster(admin_settings: None, conn: sqlite3.Connection) -> None:
    open_session(conn, date(2026, 5, 18))
    update = _admin_update()
    await handle_lock_in(update, _ctx(conn, args=[]))
    assert "Roster is empty" in update.effective_message.reply_text.await_args.args[0]


async def test_lock_in_callback_locks_and_edits(
    admin_settings: None, conn: sqlite3.Connection
) -> None:
    sess = open_session(conn, date(2026, 5, 18))
    add_player(conn, 7, "Mehdi", "mehdi")
    update = _admin_callback_update("lockpick:7")
    await handle_lock_in_callback(update, _ctx(conn))
    row = conn.execute(
        "SELECT locked_in FROM rsvps WHERE session_id=? AND telegram_id=7", (sess.id,)
    ).fetchone()
    assert row is not None and row["locked_in"] == 1
    update.callback_query.answer.assert_awaited()
    assert "locked into session" in update.callback_query.edit_message_text.await_args.args[0]


async def test_lock_in_callback_no_session_alerts(
    admin_settings: None, conn: sqlite3.Connection
) -> None:
    add_player(conn, 7, "Mehdi", "mehdi")
    update = _admin_callback_update("lockpick:7")
    await handle_lock_in_callback(update, _ctx(conn))
    assert "no active session" in update.callback_query.answer.await_args.args[0].lower()
    update.callback_query.edit_message_text.assert_not_called()


async def test_lock_in_callback_gone_player_alerts(
    admin_settings: None, conn: sqlite3.Connection
) -> None:
    open_session(conn, date(2026, 5, 18))
    update = _admin_callback_update("lockpick:999")
    await handle_lock_in_callback(update, _ctx(conn))
    assert "no longer" in update.callback_query.answer.await_args.args[0].lower()


async def test_lock_in_callback_bad_int_returns(
    admin_settings: None, conn: sqlite3.Connection
) -> None:
    open_session(conn, date(2026, 5, 18))
    update = _admin_callback_update("lockpick:abc")
    await handle_lock_in_callback(update, _ctx(conn))
    update.callback_query.answer.assert_awaited()
    update.callback_query.edit_message_text.assert_not_called()


async def test_lock_in_callback_no_query_returns(
    admin_settings: None, conn: sqlite3.Connection
) -> None:
    u = MagicMock()
    u.effective_user = MagicMock(id=42)
    u.callback_query = None
    await handle_lock_in_callback(u, _ctx(conn))  # silent
