from __future__ import annotations

import sqlite3
from datetime import date

import pytest

from toop.players import add_player
from toop.poll import (
    get_poll,
    record_attendance_answer,
    record_poll,
)
from toop.rsvp import count_rsvps
from toop.sessions import open_session


@pytest.fixture
def session_id(conn: sqlite3.Connection) -> int:
    return open_session(conn, date(2026, 5, 18)).id


def test_record_and_get_poll(conn: sqlite3.Connection, session_id: int) -> None:
    record_poll(conn, session_id=session_id, poll_id="p1", kind="attendance", message_id=99)
    poll = get_poll(conn, "p1")
    assert poll is not None
    assert poll.session_id == session_id
    assert poll.kind == "attendance"
    assert poll.message_id == 99
    assert poll.closed is False
    assert poll.quorum_announced is False
    assert poll.cap_closed is False


def test_record_poll_upserts(conn: sqlite3.Connection, session_id: int) -> None:
    record_poll(conn, session_id=session_id, poll_id="p1", kind="attendance", message_id=1)
    record_poll(conn, session_id=session_id, poll_id="p1", kind="reservation", message_id=2)
    poll = get_poll(conn, "p1")
    assert poll is not None and poll.kind == "reservation" and poll.message_id == 2


def test_record_poll_rejects_bad_kind(conn: sqlite3.Connection, session_id: int) -> None:
    with pytest.raises(ValueError, match="kind must be one of"):
        record_poll(conn, session_id=session_id, poll_id="p1", kind="bogus", message_id=None)


def test_get_poll_missing_returns_none(conn: sqlite3.Connection) -> None:
    assert get_poll(conn, "nope") is None


def test_attendance_answer_yes(conn: sqlite3.Connection, session_id: int) -> None:
    add_player(conn, 1, "Alice", "alice")
    assert record_attendance_answer(conn, session_id, 1, [0]) is True
    assert count_rsvps(conn, session_id).yes == 1


def test_attendance_answer_no(conn: sqlite3.Connection, session_id: int) -> None:
    add_player(conn, 1, "Alice", "alice")
    record_attendance_answer(conn, session_id, 1, [1])
    counts = count_rsvps(conn, session_id)
    assert counts.yes == 0 and counts.no == 1


def test_attendance_answer_retract_removes_row(conn: sqlite3.Connection, session_id: int) -> None:
    add_player(conn, 1, "Alice", "alice")
    record_attendance_answer(conn, session_id, 1, [0])
    assert record_attendance_answer(conn, session_id, 1, []) is True
    counts = count_rsvps(conn, session_id)
    assert counts.total == 0


def test_attendance_answer_off_roster_is_noop(conn: sqlite3.Connection, session_id: int) -> None:
    assert record_attendance_answer(conn, session_id, 999, [0]) is False
    assert count_rsvps(conn, session_id).total == 0
