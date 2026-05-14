from __future__ import annotations

import sqlite3
from datetime import date

import pytest

from toop.players import add_player
from toop.rsvp import (
    RsvpCounts,
    count_rsvps,
    format_rsvp_message,
    is_player_on_roster,
    lock_in_player,
    upsert_rsvp,
)
from toop.sessions import open_session


@pytest.fixture
def session_id(conn: sqlite3.Connection) -> int:
    return open_session(conn, date(2026, 5, 18)).id


def test_upsert_rsvp_idempotent(conn: sqlite3.Connection, session_id: int) -> None:
    add_player(conn, 1, "Alice", "alice")
    upsert_rsvp(conn, session_id, 1, "yes")
    upsert_rsvp(conn, session_id, 1, "no")
    counts = count_rsvps(conn, session_id)
    assert counts == RsvpCounts(yes=0, no=1, maybe=0)


def test_invalid_status_raises(conn: sqlite3.Connection, session_id: int) -> None:
    add_player(conn, 1, "Alice", "alice")
    with pytest.raises(ValueError):
        upsert_rsvp(conn, session_id, 1, "perhaps")


def test_count_rsvps_groups(conn: sqlite3.Connection, session_id: int) -> None:
    for i in range(5):
        add_player(conn, i + 1, f"P{i}", f"p{i}")
        upsert_rsvp(conn, session_id, i + 1, "yes")
    for i in range(5, 7):
        add_player(conn, i + 1, f"P{i}", f"p{i}")
        upsert_rsvp(conn, session_id, i + 1, "no")
    add_player(conn, 99, "M", "m")
    upsert_rsvp(conn, session_id, 99, "maybe")
    counts = count_rsvps(conn, session_id)
    assert counts.yes == 5
    assert counts.no == 2
    assert counts.maybe == 1
    assert counts.total == 8


def test_18_yes_rsvps_persisted(conn: sqlite3.Connection, session_id: int) -> None:
    for i in range(18):
        add_player(conn, i + 1, f"P{i}", f"p{i}")
        upsert_rsvp(conn, session_id, i + 1, "yes")
    counts = count_rsvps(conn, session_id)
    assert counts.yes == 18
    rows = conn.execute(
        "SELECT COUNT(*) AS n FROM rsvps WHERE session_id=? AND status='yes'",
        (session_id,),
    ).fetchone()
    assert rows["n"] == 18


def test_lock_in_creates_yes_with_locked_flag(
    conn: sqlite3.Connection, session_id: int
) -> None:
    add_player(conn, 1, "Alice", "alice")
    assert lock_in_player(conn, session_id, 1) is True
    row = conn.execute(
        "SELECT status, locked_in FROM rsvps WHERE session_id=? AND telegram_id=?",
        (session_id, 1),
    ).fetchone()
    assert row["status"] == "yes"
    assert row["locked_in"] == 1


def test_lock_in_overrides_existing_no(conn: sqlite3.Connection, session_id: int) -> None:
    add_player(conn, 1, "Alice", "alice")
    upsert_rsvp(conn, session_id, 1, "no")
    lock_in_player(conn, session_id, 1)
    row = conn.execute(
        "SELECT status, locked_in FROM rsvps WHERE session_id=? AND telegram_id=?",
        (session_id, 1),
    ).fetchone()
    assert row["status"] == "yes"
    assert row["locked_in"] == 1


def test_lock_in_unknown_player_returns_false(
    conn: sqlite3.Connection, session_id: int
) -> None:
    assert lock_in_player(conn, session_id, 999) is False


def test_is_player_on_roster_respects_active_flag(conn: sqlite3.Connection) -> None:
    add_player(conn, 1, "Alice", "alice")
    assert is_player_on_roster(conn, 1) is True
    conn.execute("UPDATE players SET active=0 WHERE telegram_id=1")
    conn.commit()
    assert is_player_on_roster(conn, 1) is False


def test_format_rsvp_message_includes_counts() -> None:
    msg = format_rsvp_message("2026-05-18", RsvpCounts(12, 3, 2))
    assert "✅ 12" in msg
    assert "❌ 3" in msg
    assert "🤔 2" in msg
    assert "2026-05-18" in msg
