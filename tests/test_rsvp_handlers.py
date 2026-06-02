from __future__ import annotations

import sqlite3
from datetime import date
from unittest.mock import AsyncMock, MagicMock

import pytest

from toop.handlers.rsvp import handle_lock_in, handle_rsvp_callback
from toop.players import add_player
from toop.rsvp import count_rsvps
from toop.sessions import open_session


@pytest.fixture
def admin_settings(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("toop.admin.settings", MagicMock(ADMIN_TELEGRAM_ID=42))


def _callback_update(user_id: int, data: str) -> MagicMock:
    u = MagicMock()
    q = MagicMock()
    q.from_user = MagicMock(id=user_id)
    q.data = data
    q.answer = AsyncMock()
    q.edit_message_text = AsyncMock()
    q.message = MagicMock()
    u.callback_query = q
    return u


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


async def test_callback_persists_and_edits_message(conn: sqlite3.Connection) -> None:
    sess = open_session(conn, date(2026, 5, 18))
    add_player(conn, 1, "Alice", "alice")
    update = _callback_update(user_id=1, data="rsvp:yes")
    await handle_rsvp_callback(update, _ctx(conn))
    counts = count_rsvps(conn, sess.id)
    assert counts.yes == 1
    update.callback_query.edit_message_text.assert_awaited_once()
    edited_text = update.callback_query.edit_message_text.await_args.kwargs["text"]
    assert "✅ 1" in edited_text


async def test_callback_idempotent(conn: sqlite3.Connection) -> None:
    sess = open_session(conn, date(2026, 5, 18))
    add_player(conn, 1, "Alice", "alice")
    for status in ("yes", "no", "yes"):
        await handle_rsvp_callback(_callback_update(1, f"rsvp:{status}"), _ctx(conn))
    counts = count_rsvps(conn, sess.id)
    assert counts == count_rsvps(conn, sess.id)
    assert counts.yes == 1
    assert counts.no == 0


async def test_non_roster_rejected(conn: sqlite3.Connection) -> None:
    open_session(conn, date(2026, 5, 18))
    update = _callback_update(user_id=999, data="rsvp:yes")
    await handle_rsvp_callback(update, _ctx(conn))
    update.callback_query.answer.assert_awaited_once()
    alert_text = update.callback_query.answer.await_args.args[0]
    assert "not on the roster" in alert_text.lower()
    update.callback_query.edit_message_text.assert_not_called()


async def test_no_active_session_rejects(conn: sqlite3.Connection) -> None:
    add_player(conn, 1, "Alice", "alice")
    update = _callback_update(user_id=1, data="rsvp:yes")
    await handle_rsvp_callback(update, _ctx(conn))
    alert_text = update.callback_query.answer.await_args.args[0]
    assert "no active session" in alert_text.lower()


async def test_18_yes_rsvps_via_callback(conn: sqlite3.Connection) -> None:
    sess = open_session(conn, date(2026, 5, 18))
    for i in range(18):
        add_player(conn, i + 1, f"P{i}", f"p{i}")
    for i in range(18):
        await handle_rsvp_callback(_callback_update(i + 1, "rsvp:yes"), _ctx(conn))
    counts = count_rsvps(conn, sess.id)
    assert counts.yes == 18


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

from telegram.error import BadRequest  # noqa: E402

from toop.handlers.rsvp import _conn  # noqa: E402


def test_conn_raises_when_missing() -> None:
    ctx = MagicMock()
    ctx.bot_data = {}
    with pytest.raises(RuntimeError, match="DB connection missing"):
        _conn(ctx)


async def test_callback_returns_without_query(conn: sqlite3.Connection) -> None:
    u = MagicMock()
    u.callback_query = None
    await handle_rsvp_callback(u, _ctx(conn))


async def test_callback_invalid_status(conn: sqlite3.Connection) -> None:
    open_session(conn, date(2026, 5, 18))
    add_player(conn, 1, "Alice", "alice")
    update = _callback_update(1, "rsvp:bogus")
    await handle_rsvp_callback(update, _ctx(conn))
    update.callback_query.answer.assert_awaited_once()
    update.callback_query.edit_message_text.assert_not_called()


async def test_callback_edit_not_modified_is_swallowed(conn: sqlite3.Connection) -> None:
    open_session(conn, date(2026, 5, 18))
    add_player(conn, 1, "Alice", "alice")
    update = _callback_update(1, "rsvp:yes")
    update.callback_query.edit_message_text = AsyncMock(
        side_effect=BadRequest("Message is not modified")
    )
    await handle_rsvp_callback(update, _ctx(conn))  # must not raise


async def test_callback_edit_other_error_is_logged(conn: sqlite3.Connection) -> None:
    open_session(conn, date(2026, 5, 18))
    add_player(conn, 1, "Alice", "alice")
    update = _callback_update(1, "rsvp:yes")
    update.callback_query.edit_message_text = AsyncMock(side_effect=BadRequest("boom"))
    await handle_rsvp_callback(update, _ctx(conn))  # must not raise


async def test_lock_in_returns_without_message(
    admin_settings: None, conn: sqlite3.Connection
) -> None:
    u = _admin_update()
    u.effective_message = None
    await handle_lock_in(u, _ctx(conn, args=["@x"]))


async def test_lock_in_no_args(admin_settings: None, conn: sqlite3.Connection) -> None:
    update = _admin_update()
    await handle_lock_in(update, _ctx(conn, args=[]))
    assert "Usage" in update.effective_message.reply_text.await_args.args[0]


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
