from __future__ import annotations

import sqlite3

from toop.players import add_player
from toop.rating import composite_score, refresh_ratings


def _set_axis(
    conn: sqlite3.Connection, pid: int, axis: str, score: float, votes: int, calibrated: int
) -> None:
    conn.execute(
        """
        INSERT INTO player_ratings (telegram_id, axis, score, vote_count, calibrated)
        VALUES (?, ?, ?, ?, ?)
        ON CONFLICT(telegram_id, axis) DO UPDATE SET
            score=excluded.score, vote_count=excluded.vote_count, calibrated=excluded.calibrated
        """,
        (pid, axis, score, votes, calibrated),
    )
    conn.commit()


def test_status_calibrated_when_all_axes(conn: sqlite3.Connection) -> None:
    add_player(conn, 1, "Alice", "alice")
    _set_axis(conn, 1, "attack", 1.0, 20, 1)
    _set_axis(conn, 1, "defense", 0.5, 20, 1)
    _set_axis(conn, 1, "setting", -0.5, 20, 1)
    score, status = composite_score(conn, 1, weights=(0.4, 0.4, 0.2))
    assert status == "calibrated"
    assert abs(score - (1.0 * 0.4 + 0.5 * 0.4 - 0.5 * 0.2)) < 1e-9


def test_status_partial_when_some_axes(conn: sqlite3.Connection) -> None:
    add_player(conn, 1, "Alice", "alice")
    _set_axis(conn, 1, "attack", 1.0, 20, 1)
    _set_axis(conn, 1, "defense", 0.5, 5, 0)
    _set_axis(conn, 1, "setting", -0.5, 5, 0)
    _, status = composite_score(conn, 1, weights=(0.4, 0.4, 0.2))
    assert status == "partial"


def test_status_calibrating_when_none(conn: sqlite3.Connection) -> None:
    add_player(conn, 1, "Alice", "alice")
    _set_axis(conn, 1, "attack", 0.0, 0, 0)
    _set_axis(conn, 1, "defense", 0.0, 0, 0)
    _set_axis(conn, 1, "setting", 0.0, 0, 0)
    _, status = composite_score(conn, 1, weights=(0.4, 0.4, 0.2))
    assert status == "calibrating"


def test_player_with_no_ratings_row_is_calibrating(conn: sqlite3.Connection) -> None:
    add_player(conn, 1, "Alice", "alice")
    score, status = composite_score(conn, 1, weights=(0.4, 0.4, 0.2))
    assert status == "calibrating"
    assert score == 0.0


def test_weights_affect_score(conn: sqlite3.Connection) -> None:
    add_player(conn, 1, "Alice", "alice")
    # Refresh on empty aggregates writes default scores; override manually
    refresh_ratings(conn, calibration_threshold=15)
    _set_axis(conn, 1, "attack", 2.0, 20, 1)
    _set_axis(conn, 1, "defense", 0.0, 20, 1)
    _set_axis(conn, 1, "setting", 0.0, 20, 1)
    high_attack, _ = composite_score(conn, 1, weights=(0.8, 0.1, 0.1))
    low_attack, _ = composite_score(conn, 1, weights=(0.1, 0.45, 0.45))
    assert high_attack > low_attack
