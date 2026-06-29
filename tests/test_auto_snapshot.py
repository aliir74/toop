from __future__ import annotations

import sqlite3
from datetime import UTC, date, datetime, timedelta
from unittest.mock import AsyncMock, MagicMock

import pytest

from toop.config import Settings
from toop.handlers.snapshot import auto_snapshot_job
from toop.pause import pause_events_until
from toop.players import add_player
from toop.rsvp import upsert_rsvp
from toop.sessions import get_active_session, open_session


@pytest.fixture(autouse=True)
def patch_settings(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "toop.handlers.snapshot.settings",
        Settings(
            _env_file=None,
            ADMIN_TELEGRAM_ID=42,
            MAX_ATTENDEES=14,
            CALIBRATION_THRESHOLD=15,
        ),
    )


def _ctx(conn: sqlite3.Connection) -> MagicMock:
    ctx = MagicMock()
    ctx.bot_data = {"conn": conn}
    ctx.bot = MagicMock()
    ctx.bot.send_message = AsyncMock()
    return ctx


async def test_auto_snapshot_dms_admin(conn: sqlite3.Connection) -> None:
    sess = open_session(conn, date(2026, 5, 18))
    for i in range(1, 17):
        add_player(conn, i, f"P{i}", f"p{i}")
        upsert_rsvp(conn, sess.id, i, "yes")
    ctx = _ctx(conn)
    await auto_snapshot_job(ctx)
    ctx.bot.send_message.assert_awaited_once()
    kwargs = ctx.bot.send_message.await_args.kwargs
    assert kwargs["chat_id"] == 42
    assert "Auto-snapshot" in kwargs["text"]
    # The DM must include the proposed-teams preview, not just a confirmation.
    assert "proposed teams" in kwargs["text"]
    assert kwargs["parse_mode"] == "Markdown"
    active = get_active_session(conn)
    assert active is not None and active.status == "snapshotted"


async def test_auto_snapshot_no_active_session_skips(conn: sqlite3.Connection) -> None:
    ctx = _ctx(conn)
    await auto_snapshot_job(ctx)
    ctx.bot.send_message.assert_not_called()


async def test_auto_snapshot_no_rsvps_skips(conn: sqlite3.Connection) -> None:
    open_session(conn, date(2026, 5, 18))
    ctx = _ctx(conn)
    await auto_snapshot_job(ctx)
    ctx.bot.send_message.assert_not_called()


async def test_auto_snapshot_skips_when_events_paused(conn: sqlite3.Connection) -> None:
    # A full, snapshot-ready session must NOT be snapshotted while paused.
    sess = open_session(conn, date(2026, 5, 18))
    for i in range(1, 17):
        add_player(conn, i, f"P{i}", f"p{i}")
        upsert_rsvp(conn, sess.id, i, "yes")
    pause_events_until(conn, datetime.now(UTC) + timedelta(days=7))
    ctx = _ctx(conn)
    await auto_snapshot_job(ctx)
    ctx.bot.send_message.assert_not_called()
    active = get_active_session(conn)
    assert active is not None and active.status == "open"  # untouched
