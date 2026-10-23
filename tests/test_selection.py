from __future__ import annotations

import sqlite3
from datetime import date

from toop.players import add_player
from toop.rsvp import lock_in_player, upsert_rsvp
from toop.selection import select_attendees
from toop.sessions import open_session


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


def test_over_cap_cuts_latest_voters(conn: sqlite3.Connection) -> None:
    _seed_players(conn, 16)
    sess = open_session(conn, date(2026, 5, 18))
    for p in range(1, 17):
        upsert_rsvp(conn, sess.id, p, "yes")
    # Vote order is created_at; make 1 and 2 the two latest voters.
    conn.execute(
        "UPDATE rsvps SET created_at=datetime('now', '+1 minute') WHERE telegram_id IN (1, 2)"
    )
    conn.commit()
    res = select_attendees(conn, sess.id, max_attendees=14)
    assert res.selected == list(range(3, 17))
    assert res.cut == [1, 2]


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


def test_same_second_votes_use_id_tiebreaker(conn: sqlite3.Connection) -> None:
    _seed_players(conn, 16)
    sess = open_session(conn, date(2026, 5, 18))
    for p in range(1, 17):
        upsert_rsvp(conn, sess.id, p, "yes")
    res = select_attendees(conn, sess.id, max_attendees=14)
    # Same-second votes → ascending id wins; 15 and 16 are cut
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
