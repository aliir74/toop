from __future__ import annotations

import sqlite3
from datetime import date
from unittest.mock import AsyncMock, MagicMock

import pytest

from toop.handlers.sessions import (
    handle_list_sessions,
    handle_open_session,
)
from toop.sessions import get_active_session, list_recent_sessions, open_session


@pytest.fixture
def admin_settings(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("toop.admin.settings", MagicMock(ADMIN_TELEGRAM_ID=42))


def _admin_update() -> MagicMock:
    u = MagicMock()
    u.effective_user = MagicMock(id=42)
    msg = MagicMock()
    msg.reply_text = AsyncMock()
    u.effective_message = msg
    return u


def _ctx(conn: sqlite3.Connection, args: list[str]) -> MagicMock:
    ctx = MagicMock()
    ctx.bot_data = {"conn": conn}
    ctx.args = args
    return ctx


async def test_open_session_with_explicit_date(
    admin_settings: None, conn: sqlite3.Connection
) -> None:
    update = _admin_update()
    ctx = _ctx(conn, ["2026-05-18"])
    await handle_open_session(update, ctx)
    active = get_active_session(conn)
    assert active is not None and active.session_date == date(2026, 5, 18)


async def test_open_session_uses_default_weekday(
    admin_settings: None, conn: sqlite3.Connection, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        "toop.handlers.sessions.settings",
        MagicMock(SESSION_WEEKDAY="monday", GROUP_CHAT_ID=0),
    )
    update = _admin_update()
    ctx = _ctx(conn, [])
    await handle_open_session(update, ctx)
    assert get_active_session(conn) is not None


async def test_open_session_reopens_when_one_active(
    admin_settings: None, conn: sqlite3.Connection
) -> None:
    # With /close_session gone, opening auto-closes the prior active session.
    first = open_session(conn, date(2026, 5, 18))
    update = _admin_update()
    await handle_open_session(update, _ctx(conn, ["2026-05-25"]))
    active = get_active_session(conn)
    assert active is not None and active.session_date == date(2026, 5, 25)
    statuses = {s.id: s.status for s in list_recent_sessions(conn)}
    assert statuses[first.id] == "done"


async def test_list_sessions_empty(admin_settings: None, conn: sqlite3.Connection) -> None:
    update = _admin_update()
    ctx = _ctx(conn, [])
    await handle_list_sessions(update, ctx)
    reply = update.effective_message.reply_text.await_args.args[0]
    assert "No sessions" in reply


# ----- branch coverage additions -----

from toop.handlers.sessions import _conn  # noqa: E402


def _admin_update_no_msg() -> MagicMock:
    u = MagicMock()
    u.effective_user = MagicMock(id=42)
    u.effective_message = None
    return u


def test_conn_raises_when_missing() -> None:
    ctx = MagicMock()
    ctx.bot_data = {}
    with pytest.raises(RuntimeError, match="DB connection missing"):
        _conn(ctx)


async def test_open_session_returns_without_message(
    admin_settings: None, conn: sqlite3.Connection
) -> None:
    await handle_open_session(_admin_update_no_msg(), _ctx(conn, []))


async def test_open_session_invalid_date(admin_settings: None, conn: sqlite3.Connection) -> None:
    update = _admin_update()
    await handle_open_session(update, _ctx(conn, ["not-a-date"]))
    assert "Usage" in update.effective_message.reply_text.await_args.args[0]


async def test_list_sessions_returns_without_message(
    admin_settings: None, conn: sqlite3.Connection
) -> None:
    await handle_list_sessions(_admin_update_no_msg(), _ctx(conn, []))


async def test_list_sessions_with_entries(admin_settings: None, conn: sqlite3.Connection) -> None:
    open_session(conn, date(2026, 5, 18))
    update = _admin_update()
    await handle_list_sessions(update, _ctx(conn, []))
    assert "Recent sessions" in update.effective_message.reply_text.await_args.args[0]
