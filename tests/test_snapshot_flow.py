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

    await handle_swap(
        _admin_update(), _ctx(conn, args=[f"@{a_username}", f"@{b_username}"])
    )
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
    await handle_swap(
        _admin_update(), _ctx(conn, args=[f"@p{a0}", f"@p{b0}"])
    )
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
