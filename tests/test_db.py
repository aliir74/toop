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


def _columns(conn: sqlite3.Connection, table: str) -> set[str]:
    rows = conn.execute(f"PRAGMA table_info({table})").fetchall()
    return {r["name"] for r in rows}


def test_schema_creates_all_tables(conn: sqlite3.Connection) -> None:
    assert EXPECTED_TABLES.issubset(_tables(conn))


def test_fresh_db_has_pool_and_ghost_columns(conn: sqlite3.Connection) -> None:
    assert {"in_pool", "pool_paused_until", "is_ghost"}.issubset(_columns(conn, "players"))
    assert "dont_know" in _columns(conn, "vote_aggregates")


def test_migration_adds_columns_to_legacy_db(db_path: Path) -> None:
    # Simulate a DB created before the pool/ghost columns existed.
    legacy = get_connection(db_path)
    legacy.executescript(
        """
        CREATE TABLE players (
            telegram_id INTEGER PRIMARY KEY,
            username TEXT,
            display_name TEXT NOT NULL,
            joined_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
            active INTEGER NOT NULL DEFAULT 1,
            is_calibrating INTEGER NOT NULL DEFAULT 1
        );
        CREATE TABLE vote_aggregates (
            player_a INTEGER NOT NULL,
            player_b INTEGER NOT NULL,
            axis TEXT NOT NULL,
            a_wins INTEGER NOT NULL DEFAULT 0,
            b_wins INTEGER NOT NULL DEFAULT 0,
            updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
            PRIMARY KEY (player_a, player_b, axis)
        );
        """
    )
    legacy.execute(
        "INSERT INTO players (telegram_id, display_name) VALUES (?, ?)", (7, "Legacy")
    )
    legacy.execute(
        "INSERT INTO vote_aggregates (player_a, player_b, axis, a_wins, b_wins) "
        "VALUES (?, ?, ?, ?, ?)",
        (7, 8, "attack", 2, 1),
    )
    legacy.commit()
    legacy.close()

    migrated = get_connection(db_path)
    init_db(migrated)
    assert {"in_pool", "pool_paused_until", "is_ghost"}.issubset(_columns(migrated, "players"))
    assert "dont_know" in _columns(migrated, "vote_aggregates")
    # Existing rows keep sane defaults.
    row = migrated.execute(
        "SELECT in_pool, pool_paused_until, is_ghost FROM players WHERE telegram_id=7"
    ).fetchone()
    assert row["in_pool"] == 1
    assert row["pool_paused_until"] is None
    assert row["is_ghost"] == 0
    agg = migrated.execute(
        "SELECT dont_know FROM vote_aggregates WHERE player_a=7 AND player_b=8 AND axis='attack'"
    ).fetchone()
    assert agg["dont_know"] == 0
    migrated.close()


def test_migration_is_idempotent(db_path: Path) -> None:
    c = get_connection(db_path)
    init_db(c)
    init_db(c)  # second run must not raise on already-present columns
    assert {"in_pool", "pool_paused_until", "is_ghost"}.issubset(_columns(c, "players"))
    c.close()


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
