from __future__ import annotations

import sqlite3

from toop.players import add_player
from toop.rating import INDICATORS, composite_score

EQUAL = dict.fromkeys(INDICATORS, 1.0 / 6.0)


def _set(
    conn: sqlite3.Connection, pid: int, indicator: str, score: float, votes: int, calibrated: int
) -> None:
    conn.execute(
        """
        INSERT INTO player_ratings (telegram_id, indicator, score, vote_count, calibrated)
        VALUES (?, ?, ?, ?, ?)
        ON CONFLICT(telegram_id, indicator) DO UPDATE SET
            score=excluded.score, vote_count=excluded.vote_count, calibrated=excluded.calibrated
        """,
        (pid, indicator, score, votes, calibrated),
    )
    conn.commit()


def test_status_calibrated_when_all_indicators(conn: sqlite3.Connection) -> None:
    add_player(conn, 1, "Alice", "alice")
    for ind in INDICATORS:
        _set(conn, 1, ind, 1.0, 20, 1)
    score, status = composite_score(conn, 1, EQUAL)
    assert status == "calibrated"
    assert abs(score - 1.0) < 1e-9  # all scores 1.0, weights sum to 1.0


def test_status_partial_when_some_indicators(conn: sqlite3.Connection) -> None:
    add_player(conn, 1, "Alice", "alice")
    _set(conn, 1, "attack", 1.0, 20, 1)
    for ind in INDICATORS[1:]:
        _set(conn, 1, ind, 0.5, 5, 0)
    _, status = composite_score(conn, 1, EQUAL)
    assert status == "partial"


def test_status_calibrating_when_none(conn: sqlite3.Connection) -> None:
    add_player(conn, 1, "Alice", "alice")
    for ind in INDICATORS:
        _set(conn, 1, ind, 0.0, 0, 0)
    _, status = composite_score(conn, 1, EQUAL)
    assert status == "calibrating"


def test_player_with_no_ratings_row_is_calibrating(conn: sqlite3.Connection) -> None:
    add_player(conn, 1, "Alice", "alice")
    score, status = composite_score(conn, 1, EQUAL)
    assert status == "calibrating"
    assert score == 0.0


def test_weights_affect_score(conn: sqlite3.Connection) -> None:
    add_player(conn, 1, "Alice", "alice")
    _set(conn, 1, "attack", 2.0, 20, 1)
    for ind in INDICATORS[1:]:
        _set(conn, 1, ind, 0.0, 20, 1)
    heavy_attack = {**dict.fromkeys(INDICATORS, 0.0), "attack": 1.0}
    light_attack = {**dict.fromkeys(INDICATORS, 0.2), "attack": 0.0}
    high, _ = composite_score(conn, 1, heavy_attack)
    low, _ = composite_score(conn, 1, light_attack)
    assert high > low
