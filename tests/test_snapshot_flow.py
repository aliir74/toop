from __future__ import annotations

import sqlite3
from datetime import date
from unittest.mock import AsyncMock, MagicMock

import pytest

from toop.handlers.snapshot import (
    handle_publish,
    handle_snapshot,
    handle_swap,
    handle_teams,
)
from toop.players import add_player
from toop.rsvp import upsert_rsvp
from toop.sessions import get_active_session, open_session
from toop.snapshots import get_snapshot


@pytest.fixture(autouse=True)
def patch_admin(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("toop.admin.settings", MagicMock(ADMIN_TELEGRAM_ID=42))
    monkeypatch.setattr(
        "toop.handlers.snapshot.settings",
        MagicMock(
            ADMIN_TELEGRAM_ID=42,
            MAX_ATTENDEES=14,
            CALIBRATION_THRESHOLD=15,
            WEIGHT_ATTACK=0.4,
            WEIGHT_DEFENSE=0.4,
            WEIGHT_SETTING=0.2,
            GROUP_CHAT_ID=-100123,
        ),
    )


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
    ctx.bot = MagicMock()
    ctx.bot.send_message = AsyncMock()
    return ctx


def _seed_session_with_16_rsvps(conn: sqlite3.Connection) -> int:
    sess = open_session(conn, date(2026, 5, 18))
    for i in range(1, 17):
        add_player(conn, i, f"P{i}", f"p{i}")
        upsert_rsvp(conn, sess.id, i, "yes")
    return sess.id


async def test_snapshot_writes_snapshot_row(conn: sqlite3.Connection) -> None:
    sess_id = _seed_session_with_16_rsvps(conn)
    await handle_snapshot(_admin_update(), _ctx(conn))
    snap = get_snapshot(conn, sess_id)
    assert snap is not None
    assert len(snap.team_a) + len(snap.team_b) == 14
    assert len(snap.cut) == 2
    active = get_active_session(conn)
    assert active is not None and active.status == "snapshotted"


async def test_snapshot_with_no_session_errors(conn: sqlite3.Connection) -> None:
    update = _admin_update()
    await handle_snapshot(update, _ctx(conn))
    reply = update.effective_message.reply_text.await_args.args[0]
    assert "No active session" in reply


async def test_teams_renders_after_snapshot(conn: sqlite3.Connection) -> None:
    _seed_session_with_16_rsvps(conn)
    await handle_snapshot(_admin_update(), _ctx(conn))
    teams_update = _admin_update()
    await handle_teams(teams_update, _ctx(conn))
    text = teams_update.effective_message.reply_text.await_args.args[0]
    assert "Team A" in text and "Team B" in text


async def test_swap_persists_new_assignment(conn: sqlite3.Connection) -> None:
    sess_id = _seed_session_with_16_rsvps(conn)
    await handle_snapshot(_admin_update(), _ctx(conn))
    snap = get_snapshot(conn, sess_id)
    assert snap is not None
    a_player_id = snap.team_a[0]
    b_player_id = snap.team_b[0]
    a_username = f"p{a_player_id}"
    b_username = f"p{b_player_id}"

    await handle_swap(_admin_update(), _ctx(conn, args=[f"@{a_username}", f"@{b_username}"]))
    new_snap = get_snapshot(conn, sess_id)
    assert new_snap is not None
    assert a_player_id in new_snap.team_b
    assert b_player_id in new_snap.team_a


async def test_full_lifecycle_open_rsvp_snapshot_swap_publish(conn: sqlite3.Connection) -> None:
    sess_id = _seed_session_with_16_rsvps(conn)
    ctx = _ctx(conn)
    await handle_snapshot(_admin_update(), ctx)
    snap_before = get_snapshot(conn, sess_id)
    assert snap_before is not None
    a0 = snap_before.team_a[0]
    b0 = snap_before.team_b[0]
    await handle_swap(_admin_update(), _ctx(conn, args=[f"@p{a0}", f"@p{b0}"]))
    publish_ctx = _ctx(conn)
    await handle_publish(_admin_update(), publish_ctx)

    publish_ctx.bot.send_message.assert_awaited_once()
    active = get_active_session(conn)
    assert active is not None and active.status == "published"
    attendance_rows = conn.execute(
        "SELECT COUNT(*) AS n FROM attendance WHERE session_id=? AND was_attendee=1",
        (sess_id,),
    ).fetchone()["n"]
    assert attendance_rows == 14


async def test_publish_without_snapshot_errors(conn: sqlite3.Connection) -> None:
    open_session(conn, date(2026, 5, 18))
    update = _admin_update()
    await handle_publish(update, _ctx(conn))
    reply = update.effective_message.reply_text.await_args.args[0]
    assert "No snapshot" in reply


# ----- branch coverage additions -----

from telegram.error import TelegramError  # noqa: E402

from toop.handlers.snapshot import (  # noqa: E402
    _conn,
    _fetch_player,
    _fetch_player_by_username,
    auto_snapshot_job,
)


def _snap_settings(**overrides: object) -> MagicMock:
    base: dict = dict(
        ADMIN_TELEGRAM_ID=42,
        MAX_ATTENDEES=14,
        CALIBRATION_THRESHOLD=15,
        WEIGHT_ATTACK=0.4,
        WEIGHT_DEFENSE=0.4,
        WEIGHT_SETTING=0.2,
        GROUP_CHAT_ID=-100123,
    )
    base.update(overrides)
    return MagicMock(**base)


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


def test_fetch_player_missing_returns_none(conn: sqlite3.Connection) -> None:
    assert _fetch_player(conn, 999) is None


def test_fetch_player_by_username_missing_returns_none(conn: sqlite3.Connection) -> None:
    assert _fetch_player_by_username(conn, "@ghost") is None


async def test_snapshot_returns_without_message(conn: sqlite3.Connection) -> None:
    await handle_snapshot(_admin_update_no_msg(), _ctx(conn))


async def test_snapshot_no_yes_rsvps(conn: sqlite3.Connection) -> None:
    open_session(conn, date(2026, 5, 18))
    update = _admin_update()
    await handle_snapshot(update, _ctx(conn))
    reply = update.effective_message.reply_text.await_args.args[0]
    assert "nothing to snapshot" in reply.lower()


async def test_auto_snapshot_dms_admin(conn: sqlite3.Connection) -> None:
    _seed_session_with_16_rsvps(conn)
    ctx = _ctx(conn)
    await auto_snapshot_job(ctx)
    ctx.bot.send_message.assert_awaited_once()


async def test_auto_snapshot_no_result_skips(conn: sqlite3.Connection) -> None:
    ctx = _ctx(conn)
    await auto_snapshot_job(ctx)
    ctx.bot.send_message.assert_not_awaited()


async def test_auto_snapshot_admin_unset_skips(
    conn: sqlite3.Connection, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("toop.handlers.snapshot.settings", _snap_settings(ADMIN_TELEGRAM_ID=0))
    _seed_session_with_16_rsvps(conn)
    ctx = _ctx(conn)
    await auto_snapshot_job(ctx)
    ctx.bot.send_message.assert_not_awaited()


async def test_auto_snapshot_handles_telegram_error(conn: sqlite3.Connection) -> None:
    _seed_session_with_16_rsvps(conn)
    ctx = _ctx(conn)
    ctx.bot.send_message = AsyncMock(side_effect=TelegramError("boom"))
    # Must not raise — the error is logged and swallowed.
    await auto_snapshot_job(ctx)


async def test_teams_returns_without_message(conn: sqlite3.Connection) -> None:
    await handle_teams(_admin_update_no_msg(), _ctx(conn))


async def test_teams_no_session(conn: sqlite3.Connection) -> None:
    update = _admin_update()
    await handle_teams(update, _ctx(conn))
    assert "No active session" in update.effective_message.reply_text.await_args.args[0]


async def test_teams_no_snapshot(conn: sqlite3.Connection) -> None:
    open_session(conn, date(2026, 5, 18))
    update = _admin_update()
    await handle_teams(update, _ctx(conn))
    assert "No snapshot" in update.effective_message.reply_text.await_args.args[0]


async def test_swap_returns_without_message(conn: sqlite3.Connection) -> None:
    await handle_swap(_admin_update_no_msg(), _ctx(conn, args=["@a", "@b"]))


async def test_swap_too_few_args(conn: sqlite3.Connection) -> None:
    update = _admin_update()
    await handle_swap(update, _ctx(conn, args=["@only"]))
    assert "Usage" in update.effective_message.reply_text.await_args.args[0]


async def test_swap_no_session(conn: sqlite3.Connection) -> None:
    update = _admin_update()
    await handle_swap(update, _ctx(conn, args=["@a", "@b"]))
    assert "No active session" in update.effective_message.reply_text.await_args.args[0]


async def test_swap_no_snapshot(conn: sqlite3.Connection) -> None:
    open_session(conn, date(2026, 5, 18))
    update = _admin_update()
    await handle_swap(update, _ctx(conn, args=["@a", "@b"]))
    assert "No snapshot" in update.effective_message.reply_text.await_args.args[0]


async def test_swap_unknown_usernames(conn: sqlite3.Connection) -> None:
    _seed_session_with_16_rsvps(conn)
    await handle_snapshot(_admin_update(), _ctx(conn))
    update = _admin_update()
    await handle_swap(update, _ctx(conn, args=["@ghost1", "@ghost2"]))
    assert "aren't on the roster" in update.effective_message.reply_text.await_args.args[0]


async def test_swap_same_team_rejected(conn: sqlite3.Connection) -> None:
    sess_id = _seed_session_with_16_rsvps(conn)
    await handle_snapshot(_admin_update(), _ctx(conn))
    snap = get_snapshot(conn, sess_id)
    assert snap is not None
    a0, a1 = snap.team_a[0], snap.team_a[1]
    update = _admin_update()
    await handle_swap(update, _ctx(conn, args=[f"@p{a0}", f"@p{a1}"]))
    assert "opposite teams" in update.effective_message.reply_text.await_args.args[0]


async def test_publish_returns_without_message(conn: sqlite3.Connection) -> None:
    await handle_publish(_admin_update_no_msg(), _ctx(conn))


async def test_publish_no_session(conn: sqlite3.Connection) -> None:
    update = _admin_update()
    await handle_publish(update, _ctx(conn))
    assert "No active session" in update.effective_message.reply_text.await_args.args[0]


async def test_publish_group_chat_unset(
    conn: sqlite3.Connection, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("toop.handlers.snapshot.settings", _snap_settings(GROUP_CHAT_ID=0))
    _seed_session_with_16_rsvps(conn)
    await handle_snapshot(_admin_update(), _ctx(conn))
    update = _admin_update()
    await handle_publish(update, _ctx(conn))
    assert "GROUP_CHAT_ID is unset" in update.effective_message.reply_text.await_args.args[0]


async def test_publish_handles_telegram_error(conn: sqlite3.Connection) -> None:
    _seed_session_with_16_rsvps(conn)
    await handle_snapshot(_admin_update(), _ctx(conn))
    update = _admin_update()
    ctx = _ctx(conn)
    ctx.bot.send_message = AsyncMock(side_effect=TelegramError("nope"))
    await handle_publish(update, ctx)
    assert "Failed to publish" in update.effective_message.reply_text.await_args.args[0]
