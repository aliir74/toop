from __future__ import annotations

import sqlite3
from datetime import date
from unittest.mock import AsyncMock, MagicMock

import pytest
from telegram.constants import ChatType

from toop.config import settings
from toop.handlers.change_player import (
    _conn,
    handle_change_player,
    handle_change_promote_callback,
    handle_change_remove_callback,
)
from toop.handlers.snapshot import _weights, take_snapshot
from toop.players import add_player
from toop.rsvp import upsert_rsvp
from toop.selection import select_attendees
from toop.sessions import Session, get_active_session, open_session


def _join_waitlist(
    monkeypatch: pytest.MonkeyPatch, conn: sqlite3.Connection, sess: Session, pid: int
) -> None:
    """Cap at the 6 seeded attendees, then vote yes past it: pid lands on the waitlist."""
    monkeypatch.setattr(settings, "MAX_ATTENDEES", 6)
    upsert_rsvp(conn, sess.id, pid, "yes")
    assert select_attendees(conn, sess.id, 6).cut == [pid]


@pytest.fixture
def admin_settings(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("toop.admin.settings", MagicMock(ADMIN_TELEGRAM_ID=42))


def _update(private: bool = True) -> MagicMock:
    u = MagicMock()
    u.effective_user = MagicMock(id=42)
    msg = MagicMock()
    msg.reply_text = AsyncMock()
    u.effective_message = msg
    u.effective_chat = MagicMock(type=ChatType.PRIVATE if private else ChatType.GROUP)
    return u


def _cb(data: str) -> MagicMock:
    u = MagicMock()
    u.effective_user = MagicMock(id=42)
    q = MagicMock()
    q.data = data
    q.answer = AsyncMock()
    q.edit_message_text = AsyncMock()
    u.callback_query = q
    return u


def _ctx(conn: sqlite3.Connection, args: list[str] | None = None) -> MagicMock:
    ctx = MagicMock()
    ctx.bot_data = {"conn": conn}
    ctx.args = args or []
    return ctx


def _seed_snapshot(conn: sqlite3.Connection, n: int = 6) -> Session:
    sess = open_session(conn, date(2026, 5, 18))
    for i in range(1, n + 1):
        add_player(conn, i, f"P{i}", f"p{i}")
        upsert_rsvp(conn, sess.id, i, "yes")
    take_snapshot(conn, _weights(), settings.MAX_ATTENDEES, settings.CALIBRATION_THRESHOLD)
    return sess


def _last_reply(update: MagicMock) -> str:
    return update.effective_message.reply_text.await_args.args[0]


def test_conn_raises_when_missing() -> None:
    ctx = MagicMock()
    ctx.bot_data = {}
    with pytest.raises(RuntimeError, match="DB connection missing"):
        _conn(ctx)


async def test_returns_without_message(admin_settings: None, conn: sqlite3.Connection) -> None:
    u = _update()
    u.effective_message = None
    await handle_change_player(u, _ctx(conn, ["+@p1"]))  # silent


async def test_rejects_group_chat(admin_settings: None, conn: sqlite3.Connection) -> None:
    await handle_change_player(_update(private=False), _ctx(conn, ["+@p1"]))


async def test_no_active_session(admin_settings: None, conn: sqlite3.Connection) -> None:
    u = _update()
    await handle_change_player(u, _ctx(conn, ["+@p1"]))
    assert "No active session" in _last_reply(u)


async def test_no_snapshot(admin_settings: None, conn: sqlite3.Connection) -> None:
    open_session(conn, date(2026, 5, 18))
    u = _update()
    await handle_change_player(u, _ctx(conn, ["+@p1"]))
    assert "No snapshot yet" in _last_reply(u)


async def test_no_args_lists_buttons(
    admin_settings: None, monkeypatch: pytest.MonkeyPatch, conn: sqlite3.Connection
) -> None:
    sess = _seed_snapshot(conn)
    add_player(conn, 50, "Waiter", "waiter")
    _join_waitlist(monkeypatch, conn, sess, 50)
    u = _update()
    await handle_change_player(u, _ctx(conn, []))
    kb = u.effective_message.reply_text.await_args.kwargs["reply_markup"].inline_keyboard
    data = [b.callback_data for row in kb for b in row]
    assert any(d.startswith("cprm:") for d in data)
    assert "cpadd:50" in data


async def test_bad_token(admin_settings: None, conn: sqlite3.Connection) -> None:
    _seed_snapshot(conn)
    u = _update()
    await handle_change_player(u, _ctx(conn, ["p1"]))
    assert "Usage" in _last_reply(u)


async def test_target_not_found(admin_settings: None, conn: sqlite3.Connection) -> None:
    _seed_snapshot(conn)
    u = _update()
    await handle_change_player(u, _ctx(conn, ["+@ghost"]))
    assert "Couldn't find" in _last_reply(u)


async def test_empty_username_not_found(admin_settings: None, conn: sqlite3.Connection) -> None:
    _seed_snapshot(conn)
    u = _update()
    await handle_change_player(u, _ctx(conn, ["+@"]))
    assert "Couldn't find" in _last_reply(u)


async def test_add_by_username_rebalances(admin_settings: None, conn: sqlite3.Connection) -> None:
    sess = _seed_snapshot(conn)
    add_player(conn, 99, "New", "p99")
    u = _update()
    await handle_change_player(u, _ctx(conn, ["+@p99"]))
    row = conn.execute(
        "SELECT status, locked_in FROM rsvps WHERE session_id=? AND telegram_id=99", (sess.id,)
    ).fetchone()
    assert row["status"] == "yes" and row["locked_in"] == 1
    assert "Team A" in _last_reply(u)


async def test_add_by_id_drops_from_waitlist(
    admin_settings: None, monkeypatch: pytest.MonkeyPatch, conn: sqlite3.Connection
) -> None:
    sess = _seed_snapshot(conn)
    add_player(conn, 99, "New", "p99")
    _join_waitlist(monkeypatch, conn, sess, 99)
    u = _update()
    await handle_change_player(u, _ctx(conn, ["+99"]))
    assert 99 in select_attendees(conn, sess.id, 6).selected


async def test_remove_rebalances(admin_settings: None, conn: sqlite3.Connection) -> None:
    sess = _seed_snapshot(conn)
    u = _update()
    await handle_change_player(u, _ctx(conn, ["-@p1"]))
    row = conn.execute(
        "SELECT status FROM rsvps WHERE session_id=? AND telegram_id=1", (sess.id,)
    ).fetchone()
    assert row["status"] == "no"


async def test_remove_last_attendee_reports_empty(
    admin_settings: None, conn: sqlite3.Connection
) -> None:
    _seed_snapshot(conn, n=1)
    u = _update()
    await handle_change_player(u, _ctx(conn, ["-@p1"]))
    assert "No attendees left" in _last_reply(u)
    assert get_active_session(conn) is not None  # session untouched


# ----- callbacks -----


async def test_remove_callback(admin_settings: None, conn: sqlite3.Connection) -> None:
    _seed_snapshot(conn)
    u = _cb("cprm:1")
    await handle_change_remove_callback(u, _ctx(conn))
    u.callback_query.answer.assert_awaited()
    u.callback_query.edit_message_text.assert_awaited()


async def test_promote_callback(
    admin_settings: None, monkeypatch: pytest.MonkeyPatch, conn: sqlite3.Connection
) -> None:
    sess = _seed_snapshot(conn)
    add_player(conn, 99, "New", "p99")
    _join_waitlist(monkeypatch, conn, sess, 99)
    u = _cb("cpadd:99")
    await handle_change_promote_callback(u, _ctx(conn))
    row = conn.execute(
        "SELECT status FROM rsvps WHERE session_id=? AND telegram_id=99", (sess.id,)
    ).fetchone()
    assert row["status"] == "yes"
    assert 99 in select_attendees(conn, sess.id, 6).selected


async def test_callback_no_session_alerts(admin_settings: None, conn: sqlite3.Connection) -> None:
    u = _cb("cprm:1")
    await handle_change_remove_callback(u, _ctx(conn))
    assert "No active session" in u.callback_query.answer.await_args.args[0]


async def test_callback_bad_int_returns(admin_settings: None, conn: sqlite3.Connection) -> None:
    _seed_snapshot(conn)
    u = _cb("cprm:abc")
    await handle_change_remove_callback(u, _ctx(conn))
    u.callback_query.answer.assert_awaited()
    u.callback_query.edit_message_text.assert_not_called()


async def test_callback_no_query_returns(admin_settings: None, conn: sqlite3.Connection) -> None:
    u = MagicMock()
    u.effective_user = MagicMock(id=42)
    u.callback_query = None
    await handle_change_remove_callback(u, _ctx(conn))


async def test_callback_remove_last_reports_empty(
    admin_settings: None, conn: sqlite3.Connection
) -> None:
    _seed_snapshot(conn, n=1)
    u = _cb("cprm:1")
    await handle_change_remove_callback(u, _ctx(conn))
    assert "No attendees left" in u.callback_query.edit_message_text.await_args.args[0]
