from __future__ import annotations

import sqlite3
from datetime import date
from unittest.mock import AsyncMock, MagicMock

import pytest
from telegram.error import TelegramError

from toop.config import Settings
from toop.handlers.poll import (
    _conn,
    handle_poll_answer,
    post_attendance_poll,
    weekly_attendance_job,
)
from toop.players import add_player
from toop.poll import get_poll, record_poll
from toop.rsvp import count_rsvps
from toop.sessions import get_active_session, open_session


def _settings(**kw: object) -> Settings:
    base: dict[str, object] = {"_env_file": None, "SESSION_WEEKDAY": "monday"}
    base.update(kw)
    return Settings(**base)  # type: ignore[arg-type]


@pytest.fixture
def group_settings(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("toop.handlers.poll.settings", _settings(GROUP_CHAT_ID=-100123))


def _ctx(conn: sqlite3.Connection) -> MagicMock:
    ctx = MagicMock()
    ctx.bot_data = {"conn": conn}
    return ctx


def _poll_message(poll_id: str = "p1", message_id: int = 10) -> MagicMock:
    msg = MagicMock()
    msg.poll = MagicMock(id=poll_id)
    msg.message_id = message_id
    return msg


def test_conn_raises_when_missing() -> None:
    ctx = MagicMock()
    ctx.bot_data = {}
    with pytest.raises(RuntimeError, match="DB connection missing"):
        _conn(ctx)


async def test_post_attendance_poll_records(group_settings: None, conn: sqlite3.Connection) -> None:
    sess = open_session(conn, date(2026, 5, 18))
    ctx = _ctx(conn)
    ctx.bot.send_poll = AsyncMock(return_value=_poll_message())
    await post_attendance_poll(ctx, conn, sess)
    ctx.bot.send_poll.assert_awaited_once()
    poll = get_poll(conn, "p1")
    assert poll is not None and poll.kind == "attendance" and poll.session_id == sess.id


async def test_post_attendance_poll_skips_without_group(
    monkeypatch: pytest.MonkeyPatch, conn: sqlite3.Connection
) -> None:
    monkeypatch.setattr("toop.handlers.poll.settings", _settings(GROUP_CHAT_ID=0))
    sess = open_session(conn, date(2026, 5, 18))
    ctx = _ctx(conn)
    ctx.bot.send_poll = AsyncMock()
    await post_attendance_poll(ctx, conn, sess)
    ctx.bot.send_poll.assert_not_called()


async def test_post_attendance_poll_telegram_error(
    group_settings: None, conn: sqlite3.Connection
) -> None:
    sess = open_session(conn, date(2026, 5, 18))
    ctx = _ctx(conn)
    ctx.bot.send_poll = AsyncMock(side_effect=TelegramError("down"))
    await post_attendance_poll(ctx, conn, sess)  # must not raise
    assert get_poll(conn, "p1") is None


async def test_post_attendance_poll_no_poll_on_message(
    group_settings: None, conn: sqlite3.Connection
) -> None:
    sess = open_session(conn, date(2026, 5, 18))
    ctx = _ctx(conn)
    msg = MagicMock()
    msg.poll = None
    ctx.bot.send_poll = AsyncMock(return_value=msg)
    await post_attendance_poll(ctx, conn, sess)
    assert conn.execute("SELECT COUNT(*) AS n FROM session_polls").fetchone()["n"] == 0


async def test_weekly_job_opens_and_posts(group_settings: None, conn: sqlite3.Connection) -> None:
    ctx = _ctx(conn)
    ctx.bot.send_poll = AsyncMock(return_value=_poll_message())
    await weekly_attendance_job(ctx)
    assert get_active_session(conn) is not None
    ctx.bot.send_poll.assert_awaited_once()


async def test_weekly_job_skips_when_session_active(
    group_settings: None, conn: sqlite3.Connection
) -> None:
    open_session(conn, date(2026, 5, 18))
    ctx = _ctx(conn)
    ctx.bot.send_poll = AsyncMock()
    await weekly_attendance_job(ctx)
    ctx.bot.send_poll.assert_not_called()


def _answer_update(poll_id: str, option_ids: tuple[int, ...], user_id: int | None) -> MagicMock:
    u = MagicMock()
    ans = MagicMock()
    ans.poll_id = poll_id
    ans.option_ids = option_ids
    ans.user = MagicMock(id=user_id) if user_id is not None else None
    u.poll_answer = ans
    return u


async def test_poll_answer_records_yes(conn: sqlite3.Connection) -> None:
    sess = open_session(conn, date(2026, 5, 18))
    add_player(conn, 1, "Alice", "alice")
    record_poll(conn, session_id=sess.id, poll_id="p1", kind="attendance", message_id=1)
    await handle_poll_answer(_answer_update("p1", (0,), 1), _ctx(conn))
    assert count_rsvps(conn, sess.id).yes == 1


async def test_poll_answer_unknown_poll_noop(conn: sqlite3.Connection) -> None:
    await handle_poll_answer(_answer_update("zzz", (0,), 1), _ctx(conn))  # no crash


async def test_poll_answer_non_attendance_kind_noop(conn: sqlite3.Connection) -> None:
    sess = open_session(conn, date(2026, 5, 18))
    add_player(conn, 1, "Alice", "alice")
    record_poll(conn, session_id=sess.id, poll_id="r1", kind="reservation", message_id=1)
    await handle_poll_answer(_answer_update("r1", (0,), 1), _ctx(conn))
    assert count_rsvps(conn, sess.id).yes == 0


async def test_poll_answer_none_returns(conn: sqlite3.Connection) -> None:
    u = MagicMock()
    u.poll_answer = None
    await handle_poll_answer(u, _ctx(conn))


async def test_poll_answer_no_user_returns(conn: sqlite3.Connection) -> None:
    sess = open_session(conn, date(2026, 5, 18))
    record_poll(conn, session_id=sess.id, poll_id="p1", kind="attendance", message_id=1)
    await handle_poll_answer(_answer_update("p1", (0,), None), _ctx(conn))
    assert count_rsvps(conn, sess.id).total == 0
