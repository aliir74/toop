from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from toop.db import get_connection, init_db

EXPECTED_TABLES = {
    "players",
    "sessions",
    "rsvps",
    "attendance",
    "vote_aggregates",
    "pending_prompts",
    "answered_prompts",
    "snoozes",
}


def _tables(conn: sqlite3.Connection) -> set[str]:
    rows = conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
    return {r["name"] for r in rows}


def test_schema_creates_all_tables(conn: sqlite3.Connection) -> None:
    assert EXPECTED_TABLES.issubset(_tables(conn))


def test_schema_is_idempotent(db_path: Path) -> None:
    c1 = get_connection(db_path)
    init_db(c1)
    init_db(c1)
    c1.close()
    c2 = get_connection(db_path)
    init_db(c2)
    assert EXPECTED_TABLES.issubset(_tables(c2))
    c2.close()


def test_foreign_keys_enforced(conn: sqlite3.Connection) -> None:
    pragma = conn.execute("PRAGMA foreign_keys").fetchone()[0]
    assert pragma == 1
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(
            "INSERT INTO rsvps (session_id, telegram_id, status) VALUES (?, ?, ?)",
            (999, 111, "yes"),
        )
        conn.commit()


def test_vote_aggregates_pair_invariant(conn: sqlite3.Connection) -> None:
    for tid in (1, 2, 4, 5):
        conn.execute(
            "INSERT INTO players (telegram_id, display_name) VALUES (?, ?)",
            (tid, f"P{tid}"),
        )
    conn.execute(
        "INSERT INTO vote_aggregates (player_a, player_b, axis, a_wins, b_wins) "
        "VALUES (?, ?, ?, ?, ?)",
        (1, 2, "attack", 0, 0),
    )
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(
            "INSERT INTO vote_aggregates (player_a, player_b, axis, a_wins, b_wins) "
            "VALUES (?, ?, ?, ?, ?)",
            (5, 4, "attack", 0, 0),
        )
        conn.commit()
