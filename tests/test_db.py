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
    "scores",
    "score_skips",
    "player_ratings",
    "snapshots",
}


def _tables(conn: sqlite3.Connection) -> set[str]:
    rows = conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
    return {r["name"] for r in rows}


def _columns(conn: sqlite3.Connection, table: str) -> set[str]:
    rows = conn.execute(f"PRAGMA table_info({table})").fetchall()
    return {r["name"] for r in rows}


def test_schema_creates_all_tables(conn: sqlite3.Connection) -> None:
    assert EXPECTED_TABLES.issubset(_tables(conn))


def test_fresh_db_has_pool_ghost_and_indicator_columns(conn: sqlite3.Connection) -> None:
    assert {"in_pool", "pool_paused_until", "is_ghost"}.issubset(_columns(conn, "players"))
    assert "indicator" in _columns(conn, "player_ratings")
    assert "axis" not in _columns(conn, "player_ratings")
    assert {"voter_id", "player_id", "score"}.issubset(_columns(conn, "scores"))


def test_fresh_db_has_photo_file_id_column(conn: sqlite3.Connection) -> None:
    assert "photo_file_id" in _columns(conn, "players")


def test_photo_file_id_added_to_preexisting_players_table(db_path: Path) -> None:
    # A players table created before the photo column existed gets it via ALTER.
    pre = get_connection(db_path)
    pre.execute(
        "CREATE TABLE players ("
        "telegram_id INTEGER PRIMARY KEY, username TEXT, display_name TEXT NOT NULL, "
        "active INTEGER NOT NULL DEFAULT 1, is_calibrating INTEGER NOT NULL DEFAULT 1)"
    )
    pre.commit()
    pre.close()
    migrated = get_connection(db_path)
    init_db(migrated)
    assert "photo_file_id" in _columns(migrated, "players")
    migrated.close()


def _build_legacy_db(db_path: Path) -> None:
    """A DB on the old pairwise schema: axis-based player_ratings + pairwise tables."""
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
        CREATE TABLE player_ratings (
            telegram_id INTEGER NOT NULL,
            axis TEXT NOT NULL,
            score REAL NOT NULL,
            vote_count INTEGER NOT NULL DEFAULT 0,
            calibrated INTEGER NOT NULL DEFAULT 0,
            computed_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
            PRIMARY KEY (telegram_id, axis)
        );
        CREATE TABLE vote_aggregates (
            player_a INTEGER NOT NULL, player_b INTEGER NOT NULL, axis TEXT NOT NULL,
            a_wins INTEGER NOT NULL DEFAULT 0, b_wins INTEGER NOT NULL DEFAULT 0,
            PRIMARY KEY (player_a, player_b, axis)
        );
        CREATE TABLE pending_prompts (voter_id INTEGER, player_a INTEGER, player_b INTEGER);
        CREATE TABLE answered_prompts (voter_id INTEGER, player_a INTEGER, player_b INTEGER);
        CREATE TABLE snoozes (voter_id INTEGER, axis TEXT, snoozed_until TIMESTAMP);
        """
    )
    legacy.execute("INSERT INTO players (telegram_id, display_name) VALUES (7, 'Legacy')")
    # attack→attack (in range), defense→block (clamped from 3.0→2.0),
    # setting→setting (clamped from -5.0→-2.0).
    legacy.executemany(
        "INSERT INTO player_ratings (telegram_id, axis, score) VALUES (?, ?, ?)",
        # 'libero' has no new-indicator mapping → must be skipped by the seeder.
        [(7, "attack", 1.5), (7, "defense", 3.0), (7, "setting", -5.0), (7, "libero", 0.9)],
    )
    legacy.commit()
    legacy.close()


def test_pairwise_to_scores_migration(db_path: Path) -> None:
    _build_legacy_db(db_path)
    migrated = get_connection(db_path)
    init_db(migrated)

    # player_ratings rebuilt on the indicator enum.
    cols = _columns(migrated, "player_ratings")
    assert "indicator" in cols and "axis" not in cols
    # New + late columns present.
    assert {"in_pool", "pool_paused_until", "is_ghost"}.issubset(_columns(migrated, "players"))
    assert {"scores", "score_skips"}.issubset(_tables(migrated))
    # Legacy pairwise tables dropped.
    assert {"vote_aggregates", "pending_prompts", "answered_prompts", "snoozes"}.isdisjoint(
        _tables(migrated)
    )
    # Seeded warm-start priors: overlapping indicators mapped + clamped.
    priors = {
        r["indicator"]: r["score"]
        for r in migrated.execute(
            "SELECT indicator, score FROM player_ratings WHERE telegram_id=7"
        ).fetchall()
    }
    assert abs(priors["attack"] - 1.5) < 1e-9
    assert abs(priors["block"] - 2.0) < 1e-9  # clamped from 3.0
    assert abs(priors["setting"] + 2.0) < 1e-9  # clamped from -5.0
    # Non-overlapping indicators start cold (no row); unmapped axis was skipped.
    assert "serve" not in priors and "receive" not in priors
    assert len(priors) == 3
    migrated.close()


def test_migration_is_idempotent_after_pairwise_migration(db_path: Path) -> None:
    _build_legacy_db(db_path)
    c = get_connection(db_path)
    init_db(c)
    init_db(c)  # second run sees indicator-based player_ratings → no-op
    assert "indicator" in _columns(c, "player_ratings")
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


def test_scores_reject_self_rating(conn: sqlite3.Connection) -> None:
    conn.execute("INSERT INTO players (telegram_id, display_name) VALUES (1, 'A')")
    conn.commit()
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(
            "INSERT INTO scores (voter_id, player_id, indicator, score) VALUES (1, 1, 'attack', 3)"
        )
        conn.commit()


def test_scores_reject_out_of_range(conn: sqlite3.Connection) -> None:
    conn.executescript("INSERT INTO players (telegram_id, display_name) VALUES (1, 'A'), (2, 'B');")
    conn.commit()
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(
            "INSERT INTO scores (voter_id, player_id, indicator, score) VALUES (1, 2, 'attack', 9)"
        )
        conn.commit()
