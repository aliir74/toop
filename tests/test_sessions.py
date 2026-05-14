from __future__ import annotations

import sqlite3
from datetime import date

import pytest

from toop.sessions import (
    SessionStateError,
    close_session,
    get_active_session,
    list_recent_sessions,
    next_weekday,
    open_session,
)


def test_next_weekday_skips_today() -> None:
    monday = date(2026, 5, 11)
    nxt = next_weekday("monday", today=monday)
    assert nxt == date(2026, 5, 18)
    nxt_fri = next_weekday("friday", today=monday)
    assert nxt_fri == date(2026, 5, 15)


def test_open_session_creates_open_row(conn: sqlite3.Connection) -> None:
    s = open_session(conn, date(2026, 5, 18))
    assert s.status == "open"
    assert s.session_date == date(2026, 5, 18)


def test_one_session_open_invariant(conn: sqlite3.Connection) -> None:
    open_session(conn, date(2026, 5, 18))
    with pytest.raises(SessionStateError, match="Session"):
        open_session(conn, date(2026, 5, 25))


def test_close_session_allows_reopen(conn: sqlite3.Connection) -> None:
    open_session(conn, date(2026, 5, 18))
    closed = close_session(conn)
    assert closed.status == "done"
    next_s = open_session(conn, date(2026, 5, 25))
    assert next_s.status == "open"


def test_close_with_no_active_raises(conn: sqlite3.Connection) -> None:
    with pytest.raises(SessionStateError):
        close_session(conn)


def test_list_recent_sorts_desc(conn: sqlite3.Connection) -> None:
    s1 = open_session(conn, date(2026, 5, 18))
    close_session(conn)
    s2 = open_session(conn, date(2026, 5, 25))
    recent = list_recent_sessions(conn)
    assert [r.id for r in recent] == [s2.id, s1.id]


def test_get_active(conn: sqlite3.Connection) -> None:
    assert get_active_session(conn) is None
    s = open_session(conn, date(2026, 5, 18))
    active = get_active_session(conn)
    assert active is not None and active.id == s.id
    close_session(conn)
    assert get_active_session(conn) is None
