from __future__ import annotations

import sqlite3
from datetime import date
from unittest.mock import AsyncMock, MagicMock

import pytest
from telegram.error import TelegramError

from toop.config import Settings
from toop.drift import get_last_drift_signature
from toop.handlers.poll import (
    _conn,
    _maybe_notify_drift,
    handle_poll_answer,
    post_attendance_poll,
    weekly_attendance_job,
)
from toop.players import add_player
from toop.poll import add_to_waitlist, get_poll, list_waitlist, record_poll, set_quorum_announced
from toop.rsvp import count_rsvps, upsert_rsvp
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


async def test_poll_answer_reservation_adds_to_waitlist(conn: sqlite3.Connection) -> None:
    sess = open_session(conn, date(2026, 5, 18))
    add_player(conn, 1, "Alice", "alice")
    record_poll(conn, session_id=sess.id, poll_id="r1", kind="reservation", message_id=1)
    await handle_poll_answer(_answer_update("r1", (0,), 1), _ctx(conn))
    assert list_waitlist(conn, sess.id) == [1]
    assert count_rsvps(conn, sess.id).yes == 0  # reservation never touches attendance


async def test_poll_answer_reservation_removes_on_other_option(conn: sqlite3.Connection) -> None:
    sess = open_session(conn, date(2026, 5, 18))
    add_player(conn, 1, "Alice", "alice")
    record_poll(conn, session_id=sess.id, poll_id="r1", kind="reservation", message_id=1)
    await handle_poll_answer(_answer_update("r1", (0,), 1), _ctx(conn))
    await handle_poll_answer(_answer_update("r1", (1,), 1), _ctx(conn))
    assert list_waitlist(conn, sess.id) == []


async def test_poll_answer_none_returns(conn: sqlite3.Connection) -> None:
    u = MagicMock()
    u.poll_answer = None
    await handle_poll_answer(u, _ctx(conn))


async def test_poll_answer_no_user_returns(conn: sqlite3.Connection) -> None:
    sess = open_session(conn, date(2026, 5, 18))
    record_poll(conn, session_id=sess.id, poll_id="p1", kind="attendance", message_id=1)
    await handle_poll_answer(_answer_update("p1", (0,), None), _ctx(conn))
    assert count_rsvps(conn, sess.id).total == 0


# ----- threshold engine -----


def _bot_ctx(conn: sqlite3.Connection) -> MagicMock:
    ctx = _ctx(conn)
    ctx.bot.send_message = AsyncMock()
    ctx.bot.stop_poll = AsyncMock()
    ctx.bot.send_poll = AsyncMock(return_value=_poll_message("r1", 7))
    return ctx


def _seed_yes(conn: sqlite3.Connection, session_id: int, n: int, start: int = 100) -> None:
    for i in range(start, start + n):
        add_player(conn, i, f"P{i}", f"p{i}")
        upsert_rsvp(conn, session_id, i, "yes")


async def test_quorum_fires_once(group_settings: None, conn: sqlite3.Connection) -> None:
    sess = open_session(conn, date(2026, 5, 18))
    record_poll(conn, session_id=sess.id, poll_id="p1", kind="attendance", message_id=5)
    _seed_yes(conn, sess.id, 12)  # exactly at threshold; not yet over
    add_player(conn, 200, "Quorum", "q")
    ctx = _bot_ctx(conn)
    await handle_poll_answer(_answer_update("p1", (0,), 200), ctx)  # yes -> 13 > 12
    ctx.bot.send_message.assert_awaited_once()
    assert "والیبال برگزار می‌شود" in ctx.bot.send_message.await_args.kwargs["text"]
    ctx.bot.stop_poll.assert_not_called()
    poll = get_poll(conn, "p1")
    assert poll is not None and poll.quorum_announced is True
    # Re-voting the same player keeps yes at 13: quorum must not re-announce.
    await handle_poll_answer(_answer_update("p1", (0,), 200), ctx)
    ctx.bot.send_message.assert_awaited_once()


async def test_cap_closes_poll(group_settings: None, conn: sqlite3.Connection) -> None:
    sess = open_session(conn, date(2026, 5, 18))
    record_poll(conn, session_id=sess.id, poll_id="p1", kind="attendance", message_id=5)
    set_quorum_announced(conn, "p1")  # isolate the cap branch
    _seed_yes(conn, sess.id, 13)
    add_player(conn, 200, "Cap", "c")
    ctx = _bot_ctx(conn)
    await handle_poll_answer(_answer_update("p1", (0,), 200), ctx)  # yes -> 14 == cap
    ctx.bot.stop_poll.assert_awaited_once_with(-100123, 5)
    assert ctx.bot.send_message.await_args.kwargs["text"] == "ظرفیت تکمیل شد."
    poll = get_poll(conn, "p1")
    assert poll is not None and poll.cap_closed is True and poll.closed is True
    # Capping opens the reservation/waitlist poll.
    ctx.bot.send_poll.assert_awaited_once()
    res = get_poll(conn, "r1")
    assert res is not None and res.kind == "reservation"


async def test_quorum_and_cap_same_call(group_settings: None, conn: sqlite3.Connection) -> None:
    sess = open_session(conn, date(2026, 5, 18))
    record_poll(conn, session_id=sess.id, poll_id="p1", kind="attendance", message_id=5)
    _seed_yes(conn, sess.id, 13)  # neither latch set
    add_player(conn, 200, "Both", "b")
    ctx = _bot_ctx(conn)
    await handle_poll_answer(_answer_update("p1", (0,), 200), ctx)  # yes -> 14
    assert ctx.bot.send_message.await_count == 2  # quorum + capacity
    ctx.bot.stop_poll.assert_awaited_once()
    poll = get_poll(conn, "p1")
    assert poll is not None and poll.quorum_announced and poll.cap_closed


async def test_cap_without_message_id_skips_stop(
    group_settings: None, conn: sqlite3.Connection
) -> None:
    sess = open_session(conn, date(2026, 5, 18))
    record_poll(conn, session_id=sess.id, poll_id="p1", kind="attendance", message_id=None)
    set_quorum_announced(conn, "p1")
    _seed_yes(conn, sess.id, 13)
    add_player(conn, 200, "Cap", "c")
    ctx = _bot_ctx(conn)
    await handle_poll_answer(_answer_update("p1", (0,), 200), ctx)
    ctx.bot.stop_poll.assert_not_called()
    assert get_poll(conn, "p1").cap_closed is True  # type: ignore[union-attr]


async def test_quorum_send_error_is_swallowed(
    group_settings: None, conn: sqlite3.Connection
) -> None:
    sess = open_session(conn, date(2026, 5, 18))
    record_poll(conn, session_id=sess.id, poll_id="p1", kind="attendance", message_id=5)
    _seed_yes(conn, sess.id, 12)
    add_player(conn, 200, "Q", "q")
    ctx = _bot_ctx(conn)
    ctx.bot.send_message = AsyncMock(side_effect=TelegramError("down"))
    await handle_poll_answer(_answer_update("p1", (0,), 200), ctx)  # must not raise
    assert get_poll(conn, "p1").quorum_announced is True  # type: ignore[union-attr]


async def test_cap_stop_poll_error_is_swallowed(
    group_settings: None, conn: sqlite3.Connection
) -> None:
    sess = open_session(conn, date(2026, 5, 18))
    record_poll(conn, session_id=sess.id, poll_id="p1", kind="attendance", message_id=5)
    set_quorum_announced(conn, "p1")
    _seed_yes(conn, sess.id, 13)
    add_player(conn, 200, "Cap", "c")
    ctx = _bot_ctx(conn)
    ctx.bot.stop_poll = AsyncMock(side_effect=TelegramError("boom"))
    await handle_poll_answer(_answer_update("p1", (0,), 200), ctx)  # must not raise
    assert get_poll(conn, "p1").cap_closed is True  # type: ignore[union-attr]


# ----- attendance-drift DM -----


def _admin_group(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "toop.handlers.poll.settings",
        _settings(GROUP_CHAT_ID=-100123, ADMIN_TELEGRAM_ID=42),
    )


def _patch_snapshot(
    monkeypatch: pytest.MonkeyPatch, team_a: list[int], team_b: list[int], cut: list[int]
) -> None:
    snap = MagicMock(team_a=team_a, team_b=team_b, cut=cut)
    monkeypatch.setattr("toop.handlers.poll.get_snapshot", lambda _conn, _sid: snap)


async def test_drift_no_admin_returns(conn: sqlite3.Connection) -> None:
    # Real settings → ADMIN_TELEGRAM_ID is 0; no DM regardless of state.
    ctx = _bot_ctx(conn)
    await _maybe_notify_drift(ctx, conn, 1)
    ctx.bot.send_message.assert_not_called()


async def test_drift_no_snapshot_returns(
    monkeypatch: pytest.MonkeyPatch, conn: sqlite3.Connection
) -> None:
    _admin_group(monkeypatch)
    monkeypatch.setattr("toop.handlers.poll.get_snapshot", lambda _conn, _sid: None)
    ctx = _bot_ctx(conn)
    await _maybe_notify_drift(ctx, conn, 1)
    ctx.bot.send_message.assert_not_called()


async def test_drift_unchanged_returns(
    monkeypatch: pytest.MonkeyPatch, conn: sqlite3.Connection
) -> None:
    _admin_group(monkeypatch)
    sess = open_session(conn, date(2026, 5, 18))
    for i in (1, 2):
        add_player(conn, i, f"P{i}", f"p{i}")
        upsert_rsvp(conn, sess.id, i, "yes")
    _patch_snapshot(monkeypatch, [1], [2], [])
    ctx = _bot_ctx(conn)
    await _maybe_notify_drift(ctx, conn, sess.id)
    ctx.bot.send_message.assert_not_called()


async def test_drift_dms_admin_and_dedupes(
    monkeypatch: pytest.MonkeyPatch, conn: sqlite3.Connection
) -> None:
    _admin_group(monkeypatch)
    sess = open_session(conn, date(2026, 5, 18))
    for i in (1, 2, 3, 4, 5, 6):
        add_player(conn, i, f"P{i}", f"p{i}")
    # Snapshot was built on {1,2,3,4}; now 4 dropped, 5 joined.
    for i in (1, 2, 3, 5):
        upsert_rsvp(conn, sess.id, i, "yes")
    add_to_waitlist(conn, sess.id, 6)
    _patch_snapshot(monkeypatch, [1, 2], [3, 4], [])
    ctx = _bot_ctx(conn)
    await _maybe_notify_drift(ctx, conn, sess.id)
    ctx.bot.send_message.assert_awaited_once()
    text = ctx.bot.send_message.await_args.kwargs["text"]
    assert "Added: P5" in text
    assert "Dropped: P4" in text
    assert "Waitlist: P6" in text
    assert "/change_player" in text
    assert get_last_drift_signature(conn, sess.id) is not None
    # Same drift state must not re-ping.
    await _maybe_notify_drift(ctx, conn, sess.id)
    ctx.bot.send_message.assert_awaited_once()


async def test_drift_send_error_is_swallowed(
    monkeypatch: pytest.MonkeyPatch, conn: sqlite3.Connection
) -> None:
    _admin_group(monkeypatch)
    sess = open_session(conn, date(2026, 5, 18))
    for i in (1, 2, 3):
        add_player(conn, i, f"P{i}", f"p{i}")
    for i in (1, 2):
        upsert_rsvp(conn, sess.id, i, "yes")  # 3 dropped vs snapshot
    _patch_snapshot(monkeypatch, [1, 2], [3], [])
    ctx = _bot_ctx(conn)
    ctx.bot.send_message = AsyncMock(side_effect=TelegramError("down"))
    await _maybe_notify_drift(ctx, conn, sess.id)  # must not raise
    assert get_last_drift_signature(conn, sess.id) is not None
