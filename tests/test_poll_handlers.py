from __future__ import annotations

import sqlite3
from datetime import UTC, date, datetime, timedelta
from unittest.mock import AsyncMock, MagicMock

import pytest
from telegram.error import BadRequest, TelegramError

from toop.config import Settings
from toop.drift import get_last_drift_signature
from toop.handlers.poll import (
    ROSTER_EDIT_DELAY_SECONDS,
    _conn,
    _maybe_notify_drift,
    _roster_edit_job,
    handle_poll_answer,
    post_attendance_poll,
    refresh_roster,
    weekly_attendance_job,
)
from toop.pause import pause_events_until
from toop.players import add_player
from toop.poll import get_poll, record_poll, set_quorum_announced, set_roster_message_id
from toop.rsvp import count_rsvps, is_player_on_roster, upsert_rsvp
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
    ctx.job_queue = None  # roster edits run inline unless a test opts into the queue
    ctx.bot.send_message = AsyncMock(return_value=MagicMock(message_id=11))
    ctx.bot.edit_message_text = AsyncMock()
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
    assert ctx.bot.send_poll.await_args.kwargs["api_kwargs"] == {"allows_revoting": True}
    poll = get_poll(conn, "p1")
    assert poll is not None and poll.kind == "attendance" and poll.session_id == sess.id
    # The live voter list goes out as a reply under the poll and is remembered.
    roster_kwargs = ctx.bot.send_message.await_args.kwargs
    assert roster_kwargs["reply_parameters"].message_id == 10
    assert roster_kwargs["text"].startswith("📋 Attending (0/")
    assert poll.roster_message_id == 11


async def test_post_attendance_poll_roster_error(
    group_settings: None, conn: sqlite3.Connection
) -> None:
    sess = open_session(conn, date(2026, 5, 18))
    ctx = _ctx(conn)
    ctx.bot.send_poll = AsyncMock(return_value=_poll_message())
    ctx.bot.send_message = AsyncMock(side_effect=TelegramError("down"))
    await post_attendance_poll(ctx, conn, sess)  # must not raise
    poll = get_poll(conn, "p1")
    assert poll is not None and poll.roster_message_id is None


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


async def test_weekly_job_skips_when_events_paused(
    group_settings: None, conn: sqlite3.Connection
) -> None:
    pause_events_until(conn, datetime.now(UTC) + timedelta(days=7))
    ctx = _ctx(conn)
    ctx.bot.send_poll = AsyncMock(return_value=_poll_message())
    await weekly_attendance_job(ctx)
    assert get_active_session(conn) is None  # no session opened
    ctx.bot.send_poll.assert_not_called()


async def test_weekly_job_runs_when_pause_expired(
    group_settings: None, conn: sqlite3.Connection
) -> None:
    # A pause whose window already elapsed must not block the job.
    pause_events_until(conn, datetime.now(UTC) - timedelta(days=1))
    ctx = _ctx(conn)
    ctx.bot.send_poll = AsyncMock(return_value=_poll_message())
    await weekly_attendance_job(ctx)
    assert get_active_session(conn) is not None
    ctx.bot.send_poll.assert_awaited_once()


async def test_weekly_job_closes_prior_then_opens(
    group_settings: None, conn: sqlite3.Connection
) -> None:
    prior = open_session(conn, date(2026, 5, 18))
    ctx = _ctx(conn)
    ctx.bot.send_poll = AsyncMock(return_value=_poll_message())
    await weekly_attendance_job(ctx)
    active = get_active_session(conn)
    assert active is not None and active.id != prior.id
    prior_status = conn.execute("SELECT status FROM sessions WHERE id=?", (prior.id,)).fetchone()[
        "status"
    ]
    assert prior_status == "done"
    ctx.bot.send_poll.assert_awaited_once()


def _answer_update(poll_id: str, option_ids: tuple[int, ...], user_id: int | None) -> MagicMock:
    u = MagicMock()
    ans = MagicMock()
    ans.poll_id = poll_id
    ans.option_ids = option_ids
    ans.user = (
        MagicMock(id=user_id, full_name=f"User {user_id}", username=None)
        if user_id is not None
        else None
    )
    u.poll_answer = ans
    return u


async def test_poll_answer_records_yes(conn: sqlite3.Connection) -> None:
    sess = open_session(conn, date(2026, 5, 18))
    add_player(conn, 1, "Alice", "alice")
    record_poll(conn, session_id=sess.id, poll_id="p1", kind="attendance", message_id=1)
    await handle_poll_answer(_answer_update("p1", (0,), 1), _ctx(conn))
    assert count_rsvps(conn, sess.id).yes == 1


async def test_poll_answer_auto_registers_unknown_voter(conn: sqlite3.Connection) -> None:
    # A group member who isn't on the roster yet still counts: the handler adds
    # them so their vote lands in rsvps (the old roster gate dropped it).
    sess = open_session(conn, date(2026, 5, 18))
    record_poll(conn, session_id=sess.id, poll_id="p1", kind="attendance", message_id=1)
    assert is_player_on_roster(conn, 999) is False
    await handle_poll_answer(_answer_update("p1", (0,), 999), _ctx(conn))
    assert is_player_on_roster(conn, 999) is True
    assert count_rsvps(conn, sess.id).yes == 1


async def test_poll_answer_unknown_poll_noop(conn: sqlite3.Connection) -> None:
    await handle_poll_answer(_answer_update("zzz", (0,), 1), _ctx(conn))  # no crash


async def test_poll_answer_retired_reservation_poll_ignored(conn: sqlite3.Connection) -> None:
    sess = open_session(conn, date(2026, 5, 18))
    add_player(conn, 1, "Alice", "alice")
    record_poll(conn, session_id=sess.id, poll_id="r1", kind="reservation", message_id=1)
    await handle_poll_answer(_answer_update("r1", (0,), 1), _ctx(conn))
    assert count_rsvps(conn, sess.id).total == 0


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
    ctx.bot.send_poll = AsyncMock()
    return ctx


def _seed_yes(conn: sqlite3.Connection, session_id: int, n: int, start: int = 100) -> None:
    for i in range(start, start + n):
        add_player(conn, i, f"P{i}", f"p{i}")
        upsert_rsvp(conn, session_id, i, "yes")


async def test_quorum_fires_once(group_settings: None, conn: sqlite3.Connection) -> None:
    sess = open_session(conn, date(2026, 5, 18))
    record_poll(conn, session_id=sess.id, poll_id="p1", kind="attendance", message_id=5)
    set_roster_message_id(conn, "p1", 11)  # voter list already posted
    _seed_yes(conn, sess.id, 9)  # one short of the 10-yes quorum
    add_player(conn, 200, "Quorum", "q")
    ctx = _bot_ctx(conn)
    await handle_poll_answer(_answer_update("p1", (0,), 200), ctx)  # yes -> 10 >= 10
    ctx.bot.send_message.assert_awaited_once()
    assert "Volleyball is on" in ctx.bot.send_message.await_args.kwargs["text"]
    ctx.bot.stop_poll.assert_not_called()
    poll = get_poll(conn, "p1")
    assert poll is not None and poll.quorum_announced is True
    # Re-voting the same player keeps yes at 10: quorum must not re-announce.
    await handle_poll_answer(_answer_update("p1", (0,), 200), ctx)
    ctx.bot.send_message.assert_awaited_once()


async def test_cap_announces_and_keeps_poll_open(
    group_settings: None, conn: sqlite3.Connection
) -> None:
    sess = open_session(conn, date(2026, 5, 18))
    record_poll(conn, session_id=sess.id, poll_id="p1", kind="attendance", message_id=5)
    set_roster_message_id(conn, "p1", 11)  # voter list already posted
    set_quorum_announced(conn, "p1")  # isolate the cap branch
    _seed_yes(conn, sess.id, 13)
    add_player(conn, 200, "Cap", "c")
    ctx = _bot_ctx(conn)
    await handle_poll_answer(_answer_update("p1", (0,), 200), ctx)  # yes -> 14 == cap
    assert ctx.bot.send_message.await_args.kwargs["text"].startswith("Capacity reached.")
    # The poll stays open so voters can retract, and no reservation poll goes out.
    ctx.bot.stop_poll.assert_not_called()
    ctx.bot.send_poll.assert_not_called()
    poll = get_poll(conn, "p1")
    assert poll is not None and poll.cap_announced is True and poll.closed is False
    # A yes past the cap joins the waitlist without re-announcing.
    add_player(conn, 201, "Late", "l")
    await handle_poll_answer(_answer_update("p1", (0,), 201), ctx)
    ctx.bot.send_message.assert_awaited_once()


async def test_quorum_and_cap_same_call(group_settings: None, conn: sqlite3.Connection) -> None:
    sess = open_session(conn, date(2026, 5, 18))
    record_poll(conn, session_id=sess.id, poll_id="p1", kind="attendance", message_id=5)
    set_roster_message_id(conn, "p1", 11)  # voter list already posted
    _seed_yes(conn, sess.id, 13)  # neither latch set
    add_player(conn, 200, "Both", "b")
    ctx = _bot_ctx(conn)
    await handle_poll_answer(_answer_update("p1", (0,), 200), ctx)  # yes -> 14
    assert ctx.bot.send_message.await_count == 2  # quorum + capacity
    poll = get_poll(conn, "p1")
    assert poll is not None and poll.quorum_announced and poll.cap_announced


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


# ----- live voter list -----


def _roster_poll(conn: sqlite3.Connection) -> int:
    sess = open_session(conn, date(2026, 5, 18))
    record_poll(conn, session_id=sess.id, poll_id="p1", kind="attendance", message_id=5)
    set_roster_message_id(conn, "p1", 11)
    add_player(conn, 1, "Alice", "alice")
    add_player(conn, 2, "Bob", "bob")
    return sess.id


async def test_vote_edits_roster(group_settings: None, conn: sqlite3.Connection) -> None:
    _roster_poll(conn)
    ctx = _ctx(conn)
    await handle_poll_answer(_answer_update("p1", (0,), 1), ctx)
    await handle_poll_answer(_answer_update("p1", (1,), 2), ctx)
    kwargs = ctx.bot.edit_message_text.await_args.kwargs
    assert kwargs["chat_id"] == -100123 and kwargs["message_id"] == 11
    assert "1. Alice" in kwargs["text"] and "No (1): Bob" in kwargs["text"]


async def test_retraction_edits_roster(group_settings: None, conn: sqlite3.Connection) -> None:
    _roster_poll(conn)
    ctx = _ctx(conn)
    await handle_poll_answer(_answer_update("p1", (0,), 1), ctx)
    await handle_poll_answer(_answer_update("p1", (), 1), ctx)
    assert "Nobody has said yes yet." in ctx.bot.edit_message_text.await_args.kwargs["text"]


async def test_vote_reposts_missing_roster(group_settings: None, conn: sqlite3.Connection) -> None:
    # The voter list's first post failed: the next vote posts it instead.
    sess = open_session(conn, date(2026, 5, 18))
    record_poll(conn, session_id=sess.id, poll_id="p1", kind="attendance", message_id=5)
    record_poll(conn, session_id=sess.id, poll_id="p0", kind="attendance", message_id=None)
    ctx = _ctx(conn)
    await handle_poll_answer(_answer_update("p0", (0,), 1), ctx)  # no poll message to reply to
    ctx.bot.send_message.assert_not_called()
    await handle_poll_answer(_answer_update("p1", (0,), 1), ctx)
    assert ctx.bot.send_message.await_args.kwargs["reply_parameters"].message_id == 5
    assert get_poll(conn, "p1").roster_message_id == 11  # type: ignore[union-attr]


async def test_refresh_roster(group_settings: None, conn: sqlite3.Connection) -> None:
    sid = _roster_poll(conn)
    ctx = _ctx(conn)
    await refresh_roster(ctx, conn, sid + 1)  # session without a poll
    ctx.bot.edit_message_text.assert_not_called()
    upsert_rsvp(conn, sid, 1, "no")
    await refresh_roster(ctx, conn, sid)
    assert "No (1): Alice" in ctx.bot.edit_message_text.await_args.kwargs["text"]


async def test_vote_without_group_skips_roster(conn: sqlite3.Connection) -> None:
    sess = open_session(conn, date(2026, 5, 18))
    record_poll(conn, session_id=sess.id, poll_id="p1", kind="attendance", message_id=5)
    ctx = _ctx(conn)
    ctx.job_queue = MagicMock()
    await handle_poll_answer(_answer_update("p1", (0,), 1), ctx)
    ctx.job_queue.run_once.assert_not_called()
    ctx.bot.edit_message_text.assert_not_called()


async def test_roster_edit_is_debounced(group_settings: None, conn: sqlite3.Connection) -> None:
    _roster_poll(conn)
    ctx = _ctx(conn)
    ctx.job_queue = MagicMock()
    ctx.job_queue.get_jobs_by_name = MagicMock(return_value=[])
    await handle_poll_answer(_answer_update("p1", (0,), 1), ctx)
    ctx.job_queue.run_once.assert_called_once_with(
        _roster_edit_job, ROSTER_EDIT_DELAY_SECONDS, data="p1", name="roster:p1"
    )
    # A second vote while that edit is pending rides along instead of queueing.
    ctx.job_queue.get_jobs_by_name = MagicMock(return_value=[MagicMock()])
    await handle_poll_answer(_answer_update("p1", (0,), 2), ctx)
    ctx.job_queue.run_once.assert_called_once()
    ctx.bot.edit_message_text.assert_not_called()


async def test_roster_edit_job_renders_current_votes(
    group_settings: None, conn: sqlite3.Connection
) -> None:
    sid = _roster_poll(conn)
    upsert_rsvp(conn, sid, 1, "yes")
    upsert_rsvp(conn, sid, 2, "yes")
    ctx = _ctx(conn)
    ctx.job = MagicMock(data="p1")
    await _roster_edit_job(ctx)
    assert "1. Alice\n2. Bob" in ctx.bot.edit_message_text.await_args.kwargs["text"]


async def test_roster_edit_job_noops(conn: sqlite3.Connection) -> None:
    ctx = _ctx(conn)
    ctx.job = None
    await _roster_edit_job(ctx)
    ctx.job = MagicMock(data="gone")  # poll row no longer exists
    await _roster_edit_job(ctx)
    sess = open_session(conn, date(2026, 5, 18))
    record_poll(conn, session_id=sess.id, poll_id="p1", kind="attendance", message_id=5)
    ctx.job = MagicMock(data="p1")  # poll posted before the voter list existed
    await _roster_edit_job(ctx)
    ctx.bot.edit_message_text.assert_not_called()


@pytest.mark.parametrize(
    "error", [BadRequest("Message is not modified: same content"), TelegramError("down")]
)
async def test_roster_edit_error_is_swallowed(
    group_settings: None, conn: sqlite3.Connection, error: TelegramError
) -> None:
    _roster_poll(conn)
    ctx = _ctx(conn)
    ctx.bot.edit_message_text = AsyncMock(side_effect=error)
    await handle_poll_answer(_answer_update("p1", (0,), 1), ctx)  # must not raise
    assert count_rsvps(conn, 1).yes == 1


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
    monkeypatch.setattr(
        "toop.handlers.poll.settings",
        _settings(GROUP_CHAT_ID=-100123, ADMIN_TELEGRAM_ID=42, MAX_ATTENDEES=4),
    )
    for i in (1, 2, 3, 4, 5, 6):
        add_player(conn, i, f"P{i}", f"p{i}")
    # Snapshot was built on {1,2,3,4}; 4 dropped, so 5 moved up and 6 waits.
    for i in (1, 2, 3, 5, 6):
        upsert_rsvp(conn, sess.id, i, "yes")
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


async def test_drift_ignores_waitlist_joiner(
    monkeypatch: pytest.MonkeyPatch, conn: sqlite3.Connection
) -> None:
    monkeypatch.setattr(
        "toop.handlers.poll.settings",
        _settings(GROUP_CHAT_ID=-100123, ADMIN_TELEGRAM_ID=42, MAX_ATTENDEES=2),
    )
    sess = open_session(conn, date(2026, 5, 18))
    for i in (1, 2, 3):
        add_player(conn, i, f"P{i}", f"p{i}")
        upsert_rsvp(conn, sess.id, i, "yes")  # 3 is past the cap: waitlist, not drift
    _patch_snapshot(monkeypatch, [1], [2], [])
    ctx = _bot_ctx(conn)
    await _maybe_notify_drift(ctx, conn, sess.id)
    ctx.bot.send_message.assert_not_called()
