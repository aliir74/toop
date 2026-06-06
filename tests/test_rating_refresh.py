from __future__ import annotations

import sqlite3

from toop.players import add_player
from toop.rating import INDICATORS, refresh_ratings


def _seed_players(conn: sqlite3.Connection, n: int) -> None:
    for i in range(1, n + 1):
        add_player(conn, i, f"P{i}", f"p{i}")


def _score(conn: sqlite3.Connection, voter: int, player: int, indicator: str, score: int) -> None:
    conn.execute(
        "INSERT INTO scores (voter_id, player_id, indicator, score) VALUES (?, ?, ?, ?)",
        (voter, player, indicator, score),
    )
    conn.commit()


def test_refresh_writes_one_row_per_scored_player_indicator(conn: sqlite3.Connection) -> None:
    _seed_players(conn, 2)
    for ind in INDICATORS:
        _score(conn, 1, 2, ind, 3)
    written = refresh_ratings(conn, calibration_threshold=1)
    assert written == 6  # player 2 across 6 indicators
    rows = conn.execute("SELECT indicator FROM player_ratings WHERE telegram_id=2").fetchall()
    assert {r["indicator"] for r in rows} == set(INDICATORS)


def test_vote_count_is_number_of_raters(conn: sqlite3.Connection) -> None:
    _seed_players(conn, 3)
    _score(conn, 1, 3, "attack", 4)
    _score(conn, 2, 3, "attack", 2)
    refresh_ratings(conn, calibration_threshold=10)
    row = conn.execute(
        "SELECT vote_count, calibrated FROM player_ratings "
        "WHERE telegram_id=3 AND indicator='attack'"
    ).fetchone()
    assert row["vote_count"] == 2
    assert row["calibrated"] == 0


def test_calibration_flag_set_when_threshold_met(conn: sqlite3.Connection) -> None:
    _seed_players(conn, 3)
    _score(conn, 1, 3, "attack", 4)
    _score(conn, 2, 3, "attack", 5)
    refresh_ratings(conn, calibration_threshold=2)
    row = conn.execute(
        "SELECT calibrated FROM player_ratings WHERE telegram_id=3 AND indicator='attack'"
    ).fetchone()
    assert row["calibrated"] == 1


def test_player_promoted_when_all_six_indicators_calibrated(conn: sqlite3.Connection) -> None:
    _seed_players(conn, 2)
    for ind in INDICATORS:
        _score(conn, 1, 2, ind, 3)
    refresh_ratings(conn, calibration_threshold=1)
    row = conn.execute("SELECT is_calibrating FROM players WHERE telegram_id=2").fetchone()
    assert row["is_calibrating"] == 0


def test_partial_coverage_does_not_promote(conn: sqlite3.Connection) -> None:
    _seed_players(conn, 2)
    # Only 5 of 6 indicators scored → not promoted.
    for ind in INDICATORS[:5]:
        _score(conn, 1, 2, ind, 3)
    refresh_ratings(conn, calibration_threshold=1)
    row = conn.execute("SELECT is_calibrating FROM players WHERE telegram_id=2").fetchone()
    assert row["is_calibrating"] == 1


def test_warm_start_prior_preserved_until_real_votes(conn: sqlite3.Connection) -> None:
    """A (player, indicator) with no real scores keeps its existing row (a seeded
    prior survives); once a real score arrives, it is overwritten."""
    _seed_players(conn, 2)
    conn.execute(
        "INSERT INTO player_ratings (telegram_id, indicator, score, vote_count, calibrated) "
        "VALUES (2, 'serve', 1.5, 0, 0)"
    )
    conn.commit()
    refresh_ratings(conn, calibration_threshold=1)
    kept = conn.execute(
        "SELECT score FROM player_ratings WHERE telegram_id=2 AND indicator='serve'"
    ).fetchone()
    assert abs(kept["score"] - 1.5) < 1e-9

    _score(conn, 1, 2, "serve", 5)
    refresh_ratings(conn, calibration_threshold=1, norm_min_ratings=1)
    replaced = conn.execute(
        "SELECT vote_count FROM player_ratings WHERE telegram_id=2 AND indicator='serve'"
    ).fetchone()
    assert replaced["vote_count"] == 1


def test_idempotent_refresh(conn: sqlite3.Connection) -> None:
    _seed_players(conn, 3)
    _score(conn, 1, 3, "attack", 4)
    _score(conn, 2, 3, "attack", 2)
    refresh_ratings(conn, calibration_threshold=1, norm_min_ratings=1)
    first = conn.execute(
        "SELECT score FROM player_ratings WHERE telegram_id=3 AND indicator='attack'"
    ).fetchone()["score"]
    refresh_ratings(conn, calibration_threshold=1, norm_min_ratings=1)
    second = conn.execute(
        "SELECT score FROM player_ratings WHERE telegram_id=3 AND indicator='attack'"
    ).fetchone()["score"]
    assert abs(first - second) < 1e-9


def test_no_active_players_returns_zero(conn: sqlite3.Connection) -> None:
    assert refresh_ratings(conn, calibration_threshold=1) == 0
