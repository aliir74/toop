from __future__ import annotations

import sqlite3
from datetime import date

import pytest

from toop.drift import (
    compute_drift,
    current_yes_set,
    display_names,
    drift_signature,
    get_last_drift_signature,
    set_drift_signature,
)
from toop.players import add_player
from toop.rsvp import upsert_rsvp
from toop.sessions import open_session


@pytest.fixture
def session_id(conn: sqlite3.Connection) -> int:
    return open_session(conn, date(2026, 5, 18)).id


def test_compute_drift() -> None:
    added, removed = compute_drift({1, 2, 3, 4}, {1, 2, 3, 5})
    assert added == [5]
    assert removed == [4]


def test_compute_drift_no_change() -> None:
    assert compute_drift({1, 2}, {1, 2}) == ([], [])


def test_drift_signature_stable() -> None:
    assert drift_signature([5], [4]) == drift_signature([5], [4])
    assert drift_signature([5], [4]) != drift_signature([6], [4])


def test_current_yes_set(conn: sqlite3.Connection, session_id: int) -> None:
    for i in (1, 2, 3):
        add_player(conn, i, f"P{i}", f"p{i}")
    upsert_rsvp(conn, session_id, 1, "yes")
    upsert_rsvp(conn, session_id, 2, "yes")
    upsert_rsvp(conn, session_id, 3, "no")
    assert current_yes_set(conn, session_id) == {1, 2}


def test_display_names_with_fallback(conn: sqlite3.Connection) -> None:
    add_player(conn, 1, "Alice", "alice")
    assert display_names(conn, [1, 999]) == ["Alice", "#999"]


def test_drift_signature_roundtrip(conn: sqlite3.Connection, session_id: int) -> None:
    assert get_last_drift_signature(conn, session_id) is None
    set_drift_signature(conn, session_id, "sig-a")
    assert get_last_drift_signature(conn, session_id) == "sig-a"
    set_drift_signature(conn, session_id, "sig-b")  # upsert overwrites
    assert get_last_drift_signature(conn, session_id) == "sig-b"
