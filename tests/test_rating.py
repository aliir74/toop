from __future__ import annotations

import sqlite3

from toop.players import add_player
from toop.rating import INDICATORS, get_player_ratings, refresh_ratings


def _seed_players(conn: sqlite3.Connection, n: int) -> None:
    for i in range(1, n + 1):
        add_player(conn, i, f"P{i}", f"p{i}")


def _score(conn: sqlite3.Connection, voter: int, player: int, indicator: str, score: int) -> None:
    conn.execute(
        "INSERT INTO scores (voter_id, player_id, indicator, score) VALUES (?, ?, ?, ?)",
        (voter, player, indicator, score),
    )
    conn.commit()


def _player_indicator_score(conn: sqlite3.Connection, pid: int, indicator: str) -> float:
    row = conn.execute(
        "SELECT score FROM player_ratings WHERE telegram_id=? AND indicator=?",
        (pid, indicator),
    ).fetchone()
    return row["score"]


def test_normalization_cancels_rater_leniency(conn: sqlite3.Connection) -> None:
    """A player rated average by both a lenient and a harsh rater nets ~0 once
    each rater is z-scored — the raw mean would not cancel the bias."""
    _seed_players(conn, 5)
    # Lenient rater 1 (mean 4): target=3 is BELOW their bar.
    _score(conn, 1, 3, "attack", 3)
    _score(conn, 1, 4, "attack", 5)
    _score(conn, 1, 5, "attack", 4)
    # Harsh rater 2 (mean 2): target=3 is ABOVE their bar.
    _score(conn, 2, 3, "attack", 3)
    _score(conn, 2, 4, "attack", 1)
    _score(conn, 2, 5, "attack", 2)
    refresh_ratings(conn, calibration_threshold=1, norm_min_ratings=1, shrinkage_k=0.0)
    assert abs(_player_indicator_score(conn, 3, "attack")) < 1e-9


def test_normalize_off_uses_raw_mean(conn: sqlite3.Connection) -> None:
    _seed_players(conn, 5)
    _score(conn, 1, 3, "attack", 3)
    _score(conn, 1, 4, "attack", 5)
    _score(conn, 1, 5, "attack", 4)
    _score(conn, 2, 3, "attack", 3)
    _score(conn, 2, 4, "attack", 1)
    _score(conn, 2, 5, "attack", 2)
    refresh_ratings(conn, calibration_threshold=1, normalize=False, shrinkage_k=0.0)
    # Raw: target got 3 and 3 → mean 3.0 (no bias correction).
    assert abs(_player_indicator_score(conn, 3, "attack") - 3.0) < 1e-9


def test_zero_variance_rater_falls_back_to_leniency_shift(conn: sqlite3.Connection) -> None:
    """A rater who gives identical scores has sd=0; we shift by their mean only."""
    _seed_players(conn, 3)
    _score(conn, 1, 2, "attack", 3)
    _score(conn, 1, 3, "attack", 3)
    refresh_ratings(conn, calibration_threshold=1, norm_min_ratings=1, shrinkage_k=0.0)
    # contribution = score - rater_mean = 3 - 3 = 0
    assert abs(_player_indicator_score(conn, 2, "attack")) < 1e-9


def test_sub_threshold_rater_falls_back_to_global_shift(conn: sqlite3.Connection) -> None:
    """A rater with fewer than norm_min_ratings scores can't anchor on their own
    mean, so we shift by the global mean instead."""
    _seed_players(conn, 3)
    _score(conn, 1, 2, "attack", 5)
    _score(conn, 1, 3, "attack", 1)
    # global mean = 3; rater has 2 scores < norm_min_ratings=5 → global shift.
    refresh_ratings(conn, calibration_threshold=1, norm_min_ratings=5, shrinkage_k=0.0)
    assert abs(_player_indicator_score(conn, 2, "attack") - 2.0) < 1e-9  # 5 - 3
    assert abs(_player_indicator_score(conn, 3, "attack") + 2.0) < 1e-9  # 1 - 3


def test_no_scores_writes_nothing(conn: sqlite3.Connection) -> None:
    _seed_players(conn, 3)
    written = refresh_ratings(conn, calibration_threshold=1)
    assert written == 0
    rows = conn.execute("SELECT COUNT(*) AS n FROM player_ratings").fetchone()
    assert rows["n"] == 0


def test_inactive_target_is_skipped(conn: sqlite3.Connection) -> None:
    _seed_players(conn, 2)
    _score(conn, 1, 2, "attack", 4)
    conn.execute("UPDATE players SET active=0 WHERE telegram_id=2")
    conn.commit()
    refresh_ratings(conn, calibration_threshold=1)
    rows = conn.execute("SELECT COUNT(*) AS n FROM player_ratings WHERE telegram_id=2").fetchone()
    assert rows["n"] == 0


def test_get_player_ratings_returns_per_indicator(conn: sqlite3.Connection) -> None:
    _seed_players(conn, 2)
    for ind in INDICATORS:
        _score(conn, 1, 2, ind, 4)
    refresh_ratings(conn, calibration_threshold=1)
    r = get_player_ratings(conn, 2)
    assert set(r.keys()) == set(INDICATORS)
    score, count, calibrated = r["attack"]
    assert count == 1
    assert calibrated is True
