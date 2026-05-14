from __future__ import annotations

import sqlite3

from toop.players import add_player
from toop.rating import get_player_ratings, refresh_ratings


def _seed_players(conn: sqlite3.Connection, n: int) -> None:
    for i in range(1, n + 1):
        add_player(conn, i, f"P{i}", f"p{i}")


def _seed_aggregate(
    conn: sqlite3.Connection, a: int, b: int, axis: str, w_a: int, w_b: int
) -> None:
    pa, pb = (a, b) if a < b else (b, a)
    if pa != a:
        w_a, w_b = w_b, w_a
    conn.execute(
        "INSERT INTO vote_aggregates (player_a, player_b, axis, a_wins, b_wins) "
        "VALUES (?, ?, ?, ?, ?)",
        (pa, pb, axis, w_a, w_b),
    )
    conn.commit()


def test_refresh_writes_three_axes_per_player(conn: sqlite3.Connection) -> None:
    _seed_players(conn, 3)
    written = refresh_ratings(conn, calibration_threshold=10)
    assert written == 9  # 3 players * 3 axes
    rows = conn.execute("SELECT axis FROM player_ratings WHERE telegram_id=1").fetchall()
    assert {r["axis"] for r in rows} == {"attack", "defense", "setting"}


def test_scores_monotonic_with_wins(conn: sqlite3.Connection) -> None:
    _seed_players(conn, 4)
    # Player 1 dominates 2, 3, 4 in attack; others draw
    for opp in (2, 3, 4):
        _seed_aggregate(conn, 1, opp, "attack", 10, 0)
    _seed_aggregate(conn, 2, 3, "attack", 5, 5)
    _seed_aggregate(conn, 2, 4, "attack", 5, 5)
    _seed_aggregate(conn, 3, 4, "attack", 5, 5)
    refresh_ratings(conn, calibration_threshold=15)
    ratings = {
        r["telegram_id"]: r["score"]
        for r in conn.execute(
            "SELECT telegram_id, score FROM player_ratings WHERE axis='attack'"
        ).fetchall()
    }
    assert ratings[1] > ratings[2]
    assert ratings[1] > ratings[3]
    assert ratings[1] > ratings[4]


def test_calibration_flag_set_when_threshold_met(conn: sqlite3.Connection) -> None:
    _seed_players(conn, 2)
    # 20 total votes touching player 1 in attack
    _seed_aggregate(conn, 1, 2, "attack", 10, 10)
    refresh_ratings(conn, calibration_threshold=15)
    row = conn.execute(
        "SELECT calibrated, vote_count FROM player_ratings WHERE telegram_id=1 AND axis='attack'"
    ).fetchone()
    assert row["vote_count"] == 20
    assert row["calibrated"] == 1


def test_uncalibrated_below_threshold(conn: sqlite3.Connection) -> None:
    _seed_players(conn, 2)
    _seed_aggregate(conn, 1, 2, "attack", 2, 1)
    refresh_ratings(conn, calibration_threshold=15)
    row = conn.execute(
        "SELECT calibrated FROM player_ratings WHERE telegram_id=1 AND axis='attack'"
    ).fetchone()
    assert row["calibrated"] == 0


def test_player_promoted_when_all_three_axes_calibrated(conn: sqlite3.Connection) -> None:
    _seed_players(conn, 2)
    for axis in ("attack", "defense", "setting"):
        _seed_aggregate(conn, 1, 2, axis, 10, 10)
    refresh_ratings(conn, calibration_threshold=15)
    row = conn.execute("SELECT is_calibrating FROM players WHERE telegram_id=1").fetchone()
    assert row["is_calibrating"] == 0


def test_get_player_ratings_returns_per_axis(conn: sqlite3.Connection) -> None:
    _seed_players(conn, 2)
    _seed_aggregate(conn, 1, 2, "attack", 5, 5)
    refresh_ratings(conn, calibration_threshold=5)
    r = get_player_ratings(conn, 1)
    assert set(r.keys()) == {"attack", "defense", "setting"}
    score, count, calibrated = r["attack"]
    assert count == 10
    assert calibrated is True


def test_idempotent_refresh(conn: sqlite3.Connection) -> None:
    _seed_players(conn, 2)
    _seed_aggregate(conn, 1, 2, "attack", 7, 3)
    refresh_ratings(conn, calibration_threshold=5)
    first = conn.execute(
        "SELECT score FROM player_ratings WHERE telegram_id=1 AND axis='attack'"
    ).fetchone()["score"]
    refresh_ratings(conn, calibration_threshold=5)
    second = conn.execute(
        "SELECT score FROM player_ratings WHERE telegram_id=1 AND axis='attack'"
    ).fetchone()["score"]
    assert abs(first - second) < 1e-9
