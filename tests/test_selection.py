from __future__ import annotations

import sqlite3
from datetime import date, timedelta

from toop.players import add_player
from toop.rsvp import lock_in_player, upsert_rsvp
from toop.selection import select_attendees
from toop.sessions import close_session, open_session


def _seed_players(conn: sqlite3.Connection, n: int) -> list[int]:
    ids = []
    for i in range(1, n + 1):
        add_player(conn, i, f"P{i}", f"p{i}")
        ids.append(i)
    return ids


def test_under_cap_selects_all(conn: sqlite3.Connection) -> None:
    _seed_players(conn, 10)
    sess = open_session(conn, date(2026, 5, 18))
    for i in range(1, 11):
        upsert_rsvp(conn, sess.id, i, "yes")
    res = select_attendees(conn, sess.id, max_attendees=14)
    assert len(res.selected) == 10
    assert res.cut == []


def test_over_cap_cuts_most_recently_played(conn: sqlite3.Connection) -> None:
    _seed_players(conn, 18)
    # Past sessions: players 1-7 attended last week; 8-14 attended 2 weeks ago; 15-18 never
    past_session = open_session(conn, date.today() - timedelta(days=7))
    for p in range(1, 8):
        conn.execute(
            "INSERT INTO attendance (session_id, telegram_id, was_attendee) VALUES (?, ?, 1)",
            (past_session.id, p),
        )
    conn.commit()
    close_session(conn)

    past2 = open_session(conn, date.today() - timedelta(days=14))
    for p in range(8, 15):
        conn.execute(
            "INSERT INTO attendance (session_id, telegram_id, was_attendee) VALUES (?, ?, 1)",
            (past2.id, p),
        )
    conn.commit()
    close_session(conn)

    sess = open_session(conn, date(2026, 5, 18))
    for p in range(1, 19):
        upsert_rsvp(conn, sess.id, p, "yes")

    res = select_attendees(conn, sess.id, max_attendees=14)
    assert len(res.selected) == 14
    assert len(res.cut) == 4
    # Players 1-14 each attended once recently; 15-18 attended zero.
    # 15-18 must all be selected (they have zero recent plays).
    for p in (15, 16, 17, 18):
        assert p in res.selected


def test_locked_in_always_selected(conn: sqlite3.Connection) -> None:
    _seed_players(conn, 18)
    sess = open_session(conn, date(2026, 5, 18))
    # 1-3 are locked in
    for p in (1, 2, 3):
        upsert_rsvp(conn, sess.id, p, "yes")
        lock_in_player(conn, sess.id, p)
    for p in range(4, 19):
        upsert_rsvp(conn, sess.id, p, "yes")
    res = select_attendees(conn, sess.id, max_attendees=14)
    assert {1, 2, 3}.issubset(set(res.selected))
    assert len(res.selected) == 14


def test_no_attendance_history_uses_id_tiebreaker(conn: sqlite3.Connection) -> None:
    _seed_players(conn, 16)
    sess = open_session(conn, date(2026, 5, 18))
    for p in range(1, 17):
        upsert_rsvp(conn, sess.id, p, "yes")
    res = select_attendees(conn, sess.id, max_attendees=14)
    # Everyone tied at 0 recent plays → ascending id wins; 15 and 16 are cut
    assert sorted(res.selected) == list(range(1, 15))
    assert sorted(res.cut) == [15, 16]


def test_only_yes_rsvps_considered(conn: sqlite3.Connection) -> None:
    _seed_players(conn, 6)
    sess = open_session(conn, date(2026, 5, 18))
    upsert_rsvp(conn, sess.id, 1, "yes")
    upsert_rsvp(conn, sess.id, 2, "no")
    upsert_rsvp(conn, sess.id, 3, "maybe")
    res = select_attendees(conn, sess.id, max_attendees=14)
    assert res.selected == [1]
