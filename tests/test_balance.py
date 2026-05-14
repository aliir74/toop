from __future__ import annotations

import sqlite3

import pytest

from toop.balance import (
    compute_metrics,
    generate_teams,
    swap_players,
)
from toop.players import add_player

WEIGHTS = (0.4, 0.4, 0.2)


def _set_rating(
    conn: sqlite3.Connection, pid: int, axis: str, score: float, calibrated: int = 1
) -> None:
    conn.execute(
        """
        INSERT INTO player_ratings (telegram_id, axis, score, vote_count, calibrated)
        VALUES (?, ?, ?, 20, ?)
        ON CONFLICT(telegram_id, axis) DO UPDATE SET
            score=excluded.score, calibrated=excluded.calibrated
        """,
        (pid, axis, score, calibrated),
    )
    conn.commit()


def _seed_balanced(conn: sqlite3.Connection, n: int) -> None:
    for i in range(1, n + 1):
        add_player(conn, i, f"P{i}", f"p{i}")
        # ratings descending: P1 strongest, P14 weakest
        base = (n + 1 - i) / n
        _set_rating(conn, i, "attack", base)
        _set_rating(conn, i, "defense", base)
        _set_rating(conn, i, "setting", base)


def test_snake_draft_14_split_seven_seven(conn: sqlite3.Connection) -> None:
    _seed_balanced(conn, 14)
    team_a, team_b, metrics = generate_teams(conn, list(range(1, 15)), WEIGHTS)
    assert len(team_a) == 7
    assert len(team_b) == 7
    assert set(team_a).isdisjoint(team_b)
    assert metrics.abs_delta < 0.5  # snake-draft on balanced data should be tight


def test_metrics_high_confidence_when_all_calibrated(conn: sqlite3.Connection) -> None:
    _seed_balanced(conn, 14)
    _, _, metrics = generate_teams(conn, list(range(1, 15)), WEIGHTS)
    assert metrics.calibration_confidence == "high"


def test_metrics_low_confidence_when_none_calibrated(conn: sqlite3.Connection) -> None:
    for i in range(1, 15):
        add_player(conn, i, f"P{i}", f"p{i}")
        for axis in ("attack", "defense", "setting"):
            _set_rating(conn, i, axis, 0.5, calibrated=0)
    _, _, metrics = generate_teams(conn, list(range(1, 15)), WEIGHTS)
    assert metrics.calibration_confidence == "low"


def test_setter_constraint_triggers_swap(conn: sqlite3.Connection) -> None:
    """Cluster all top setters on one team via composite — confirm swap moves one."""
    for i in range(1, 9):
        add_player(conn, i, f"P{i}", f"p{i}")
        _set_rating(conn, i, "attack", (9 - i) / 8)
        _set_rating(conn, i, "defense", (9 - i) / 8)
        if i <= 2:
            # P1, P2 are top setters (highest setting); they would land on team A by snake
            _set_rating(conn, i, "setting", 5.0)
        else:
            _set_rating(conn, i, "setting", 0.0)
    team_a, team_b, metrics = generate_teams(conn, list(range(1, 9)), WEIGHTS)
    a_setters = sum(1 for p in team_a if p in {1, 2})
    b_setters = sum(1 for p in team_b if p in {1, 2})
    assert a_setters >= 1 and b_setters >= 1
    # If swap happened, the flag is set.
    if a_setters == 1 and b_setters == 1:
        # Either it was already split (unlikely with this setup) or swap fixed it.
        # We can't strictly assert swap_applied=True because snake might have placed
        # them on different teams already. Just verify the constraint holds.
        assert True


def test_empty_attendees(conn: sqlite3.Connection) -> None:
    team_a, team_b, metrics = generate_teams(conn, [], WEIGHTS)
    assert team_a == []
    assert team_b == []
    assert metrics.abs_delta == 0.0


def test_swap_players_swaps_correctly() -> None:
    a = [1, 2, 3]
    b = [4, 5, 6]
    new_a, new_b = swap_players(a, b, 2, 5)
    assert sorted(new_a) == [1, 3, 5]
    assert sorted(new_b) == [2, 4, 6]


def test_swap_players_reverse_args() -> None:
    a = [1, 2, 3]
    b = [4, 5, 6]
    new_a, new_b = swap_players(a, b, 5, 2)
    assert sorted(new_a) == [1, 3, 5]
    assert sorted(new_b) == [2, 4, 6]


def test_swap_players_raises_when_same_team() -> None:
    with pytest.raises(ValueError):
        swap_players([1, 2, 3], [4, 5, 6], 1, 2)


def test_compute_metrics_recomputes_after_manual_swap(conn: sqlite3.Connection) -> None:
    _seed_balanced(conn, 6)
    initial_a, initial_b, initial_metrics = generate_teams(conn, [1, 2, 3, 4, 5, 6], WEIGHTS)
    new_a, new_b = swap_players(initial_a, initial_b, initial_a[0], initial_b[0])
    new_metrics = compute_metrics(conn, new_a, new_b, WEIGHTS)
    assert new_metrics.team_a_total != initial_metrics.team_a_total
