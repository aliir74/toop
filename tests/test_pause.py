from __future__ import annotations

import sqlite3
from datetime import UTC, datetime, timedelta

from toop.pause import (
    clear_events_pause,
    events_are_paused,
    events_paused_until,
    pause_events_until,
)


def test_no_pause_set(conn: sqlite3.Connection) -> None:
    assert events_paused_until(conn) is None
    assert events_are_paused(conn, datetime.now(UTC)) is False


def test_pause_then_read_back(conn: sqlite3.Connection) -> None:
    until = datetime(2026, 7, 1, 12, 0, tzinfo=UTC)
    pause_events_until(conn, until)
    assert events_paused_until(conn) == until


def test_events_are_paused_future_vs_past(conn: sqlite3.Connection) -> None:
    now = datetime.now(UTC)
    pause_events_until(conn, now + timedelta(days=3))
    assert events_are_paused(conn, now) is True
    # Same stored value, but evaluated after it has elapsed.
    assert events_are_paused(conn, now + timedelta(days=4)) is False


def test_pause_is_upserted(conn: sqlite3.Connection) -> None:
    first = datetime.now(UTC) + timedelta(days=1)
    second = datetime.now(UTC) + timedelta(days=14)
    pause_events_until(conn, first)
    pause_events_until(conn, second)
    assert events_paused_until(conn) == second
    # Exactly one row — the second call moved the window, didn't add another.
    assert conn.execute("SELECT COUNT(*) AS n FROM bot_state").fetchone()["n"] == 1


def test_clear_events_pause(conn: sqlite3.Connection) -> None:
    pause_events_until(conn, datetime.now(UTC) + timedelta(days=7))
    clear_events_pause(conn)
    assert events_paused_until(conn) is None
    assert events_are_paused(conn, datetime.now(UTC)) is False


def test_clear_when_nothing_set_is_noop(conn: sqlite3.Connection) -> None:
    clear_events_pause(conn)  # must not raise
    assert events_paused_until(conn) is None
