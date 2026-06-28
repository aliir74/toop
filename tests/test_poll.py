from __future__ import annotations

import sqlite3
from datetime import date

import pytest

from toop.players import add_player
from toop.poll import (
    add_to_waitlist,
    get_poll,
    list_waitlist,
    quorum_message,
    record_attendance_answer,
    record_poll,
    record_reservation_answer,
    remove_from_waitlist,
    set_cap_closed,
    set_quorum_announced,
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


def test_quorum_message_with_payment() -> None:
    msg = quorum_message("7.5", "pay@example.com", "https://sheet")
    assert "Volleyball is on" in msg
    assert "7.5" in msg
    assert "pay@example.com" in msg
    assert "https://sheet" in msg


def test_quorum_message_without_payment() -> None:
    msg = quorum_message("7.5", "", "")
    assert "Volleyball is on" in msg
    assert "7.5" not in msg
    assert "Accounting sheet" not in msg


def test_set_quorum_announced(conn: sqlite3.Connection, session_id: int) -> None:
    record_poll(conn, session_id=session_id, poll_id="p1", kind="attendance", message_id=1)
    set_quorum_announced(conn, "p1")
    poll = get_poll(conn, "p1")
    assert poll is not None and poll.quorum_announced is True and poll.cap_closed is False


def test_set_cap_closed(conn: sqlite3.Connection, session_id: int) -> None:
    record_poll(conn, session_id=session_id, poll_id="p1", kind="attendance", message_id=1)
    set_cap_closed(conn, "p1")
    poll = get_poll(conn, "p1")
    assert poll is not None and poll.cap_closed is True and poll.closed is True


def test_waitlist_add_idempotent_and_list(conn: sqlite3.Connection, session_id: int) -> None:
    for i in (1, 2, 3):
        add_player(conn, i, f"P{i}", f"p{i}")
    add_to_waitlist(conn, session_id, 2)
    add_to_waitlist(conn, session_id, 1)
    add_to_waitlist(conn, session_id, 1)  # idempotent
    add_to_waitlist(conn, session_id, 3)
    # Same-second inserts tie-break by telegram_id, so order is deterministic.
    assert list_waitlist(conn, session_id) == [1, 2, 3]


def test_remove_from_waitlist(conn: sqlite3.Connection, session_id: int) -> None:
    add_player(conn, 1, "Alice", "alice")
    add_to_waitlist(conn, session_id, 1)
    remove_from_waitlist(conn, session_id, 1)
    assert list_waitlist(conn, session_id) == []


def test_reservation_answer_adds(conn: sqlite3.Connection, session_id: int) -> None:
    add_player(conn, 1, "Alice", "alice")
    assert record_reservation_answer(conn, session_id, 1, [0]) is True
    assert list_waitlist(conn, session_id) == [1]


def test_reservation_answer_other_option_removes(conn: sqlite3.Connection, session_id: int) -> None:
    add_player(conn, 1, "Alice", "alice")
    add_to_waitlist(conn, session_id, 1)
    record_reservation_answer(conn, session_id, 1, [1])
    assert list_waitlist(conn, session_id) == []


def test_reservation_answer_retract_removes(conn: sqlite3.Connection, session_id: int) -> None:
    add_player(conn, 1, "Alice", "alice")
    add_to_waitlist(conn, session_id, 1)
    record_reservation_answer(conn, session_id, 1, [])
    assert list_waitlist(conn, session_id) == []
