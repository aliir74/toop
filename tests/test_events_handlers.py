from __future__ import annotations

import sqlite3
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, MagicMock

import pytest

from toop.handlers.events import (
    EVPAUSEDUR_PREFIX,
    _conn,
    handle_pause_events,
    handle_pause_events_dur_callback,
    handle_resume_events,
)
from toop.pause import events_are_paused, events_paused_until, pause_events_until


@pytest.fixture(autouse=True)
def admin_settings(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("toop.admin.settings", MagicMock(ADMIN_TELEGRAM_ID=42))


def _admin_update() -> MagicMock:
    u = MagicMock()
    u.effective_user = MagicMock(id=42)
    msg = MagicMock()
    msg.reply_text = AsyncMock()
    u.effective_message = msg
    return u


def _ctx(conn: sqlite3.Connection, args: list[str] | None = None) -> MagicMock:
    ctx = MagicMock()
    ctx.bot_data = {"conn": conn}
    ctx.args = args or []
    return ctx


def _callback_update(data: str | None) -> MagicMock:
    u = MagicMock()
    u.effective_user = MagicMock(id=42)
    query = MagicMock()
    query.data = data
    query.answer = AsyncMock()
    query.edit_message_text = AsyncMock()
    u.callback_query = query
    return u


def test_conn_raises_when_missing() -> None:
    ctx = MagicMock()
    ctx.bot_data = {}
    with pytest.raises(RuntimeError, match="DB connection missing"):
        _conn(ctx)


# ----- /pause_events -----


async def test_pause_with_duration_arg(conn: sqlite3.Connection) -> None:
    update = _admin_update()
    await handle_pause_events(update, _ctx(conn, ["2w"]))
    assert events_are_paused(conn, datetime.now(UTC)) is True
    until = events_paused_until(conn)
    assert until is not None and until > datetime.now(UTC) + timedelta(days=13)
    assert "paused until" in update.effective_message.reply_text.await_args.args[0]


async def test_pause_bad_duration_shows_usage(conn: sqlite3.Connection) -> None:
    update = _admin_update()
    await handle_pause_events(update, _ctx(conn, ["soon"]))
    assert events_paused_until(conn) is None  # nothing set
    assert "Usage" in update.effective_message.reply_text.await_args.args[0]


async def test_pause_no_args_offers_buttons(conn: sqlite3.Connection) -> None:
    update = _admin_update()
    await handle_pause_events(update, _ctx(conn, []))
    call = update.effective_message.reply_text.await_args
    assert "How long" in call.args[0]
    assert call.kwargs["reply_markup"] is not None  # duration keyboard
    assert events_paused_until(conn) is None  # buttons only; nothing applied yet


async def test_pause_no_args_shows_current_status(conn: sqlite3.Connection) -> None:
    pause_events_until(conn, datetime.now(UTC) + timedelta(days=5))
    update = _admin_update()
    await handle_pause_events(update, _ctx(conn, []))
    text = update.effective_message.reply_text.await_args.args[0]
    assert "is paused until" in text  # current-status line
    assert "How long" in text  # plus the re-prompt


async def test_pause_no_message_returns(conn: sqlite3.Connection) -> None:
    u = MagicMock()
    u.effective_user = MagicMock(id=42)
    u.effective_message = None
    await handle_pause_events(u, _ctx(conn, ["2w"]))  # must not raise
    assert events_paused_until(conn) is None


# ----- /pause_events duration-button callback -----


async def test_dur_callback_applies_pause(conn: sqlite3.Connection) -> None:
    update = _callback_update(f"{EVPAUSEDUR_PREFIX}1m")
    await handle_pause_events_dur_callback(update, _ctx(conn))
    assert events_are_paused(conn, datetime.now(UTC)) is True
    update.callback_query.answer.assert_awaited_once()
    assert "paused until" in update.callback_query.edit_message_text.await_args.args[0]


async def test_dur_callback_bad_token_noop(conn: sqlite3.Connection) -> None:
    update = _callback_update(f"{EVPAUSEDUR_PREFIX}nope")
    await handle_pause_events_dur_callback(update, _ctx(conn))
    assert events_paused_until(conn) is None
    update.callback_query.answer.assert_awaited_once()
    update.callback_query.edit_message_text.assert_not_called()


async def test_dur_callback_no_query_returns(conn: sqlite3.Connection) -> None:
    u = MagicMock()
    u.effective_user = MagicMock(id=42)
    u.callback_query = None
    await handle_pause_events_dur_callback(u, _ctx(conn))  # must not raise


async def test_dur_callback_no_data_returns(conn: sqlite3.Connection) -> None:
    update = _callback_update(None)
    await handle_pause_events_dur_callback(update, _ctx(conn))
    update.callback_query.answer.assert_not_called()


# ----- /resume_events -----


async def test_resume_lifts_pause(conn: sqlite3.Connection) -> None:
    pause_events_until(conn, datetime.now(UTC) + timedelta(days=7))
    update = _admin_update()
    await handle_resume_events(update, _ctx(conn))
    assert events_paused_until(conn) is None
    assert "resumed" in update.effective_message.reply_text.await_args.args[0]


async def test_resume_when_not_paused(conn: sqlite3.Connection) -> None:
    update = _admin_update()
    await handle_resume_events(update, _ctx(conn))
    assert "isn't paused" in update.effective_message.reply_text.await_args.args[0]


async def test_resume_no_message_returns(conn: sqlite3.Connection) -> None:
    u = MagicMock()
    u.effective_user = MagicMock(id=42)
    u.effective_message = None
    await handle_resume_events(u, _ctx(conn))  # must not raise
