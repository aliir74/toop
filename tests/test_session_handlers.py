from __future__ import annotations

import sqlite3
from datetime import date
from unittest.mock import AsyncMock, MagicMock

import pytest

from toop.handlers.sessions import (
    handle_close_session,
    handle_list_sessions,
    handle_open_session,
)
from toop.sessions import get_active_session, open_session


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


async def test_open_session_when_one_exists_errors(
    admin_settings: None, conn: sqlite3.Connection
) -> None:
    open_session(conn, date(2026, 5, 18))
    update = _admin_update()
    ctx = _ctx(conn, ["2026-05-25"])
    await handle_open_session(update, ctx)
    reply = update.effective_message.reply_text.await_args.args[0]
    assert "active" in reply.lower() or "still" in reply.lower()


async def test_close_session_marks_done(
    admin_settings: None, conn: sqlite3.Connection
) -> None:
    open_session(conn, date(2026, 5, 18))
    update = _admin_update()
    ctx = _ctx(conn, [])
    await handle_close_session(update, ctx)
    assert get_active_session(conn) is None


async def test_open_session_posts_rsvp_message(
    admin_settings: None, conn: sqlite3.Connection, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        "toop.handlers.sessions.settings",
        MagicMock(SESSION_WEEKDAY="monday", GROUP_CHAT_ID=-100123),
    )
    update = _admin_update()
    ctx = _ctx(conn, ["2026-05-18"])
    ctx.bot.send_message = AsyncMock()
    await handle_open_session(update, ctx)
    ctx.bot.send_message.assert_awaited_once()
    kwargs = ctx.bot.send_message.await_args.kwargs
    assert kwargs["chat_id"] == -100123
    assert "✅ 0" in kwargs["text"]


async def test_list_sessions_empty(
    admin_settings: None, conn: sqlite3.Connection
) -> None:
    update = _admin_update()
    ctx = _ctx(conn, [])
    await handle_list_sessions(update, ctx)
    reply = update.effective_message.reply_text.await_args.args[0]
    assert "No sessions" in reply
