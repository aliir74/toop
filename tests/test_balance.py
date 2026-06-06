from __future__ import annotations

import sqlite3

import pytest

from toop.balance import (
    compute_metrics,
    generate_teams,
    swap_players,
)
from toop.players import add_player
from toop.rating import INDICATORS

WEIGHTS = dict.fromkeys(INDICATORS, 1.0 / 6.0)
NON_SETTING = [ind for ind in INDICATORS if ind != "setting"]


def _set_rating(
    conn: sqlite3.Connection, pid: int, indicator: str, score: float, calibrated: int = 1
) -> None:
    conn.execute(
        """
        INSERT INTO player_ratings (telegram_id, indicator, score, vote_count, calibrated)
        VALUES (?, ?, ?, 20, ?)
        ON CONFLICT(telegram_id, indicator) DO UPDATE SET
            score=excluded.score, calibrated=excluded.calibrated
        """,
        (pid, indicator, score, calibrated),
    )
    conn.commit()


def _seed_balanced(conn: sqlite3.Connection, n: int) -> None:
    for i in range(1, n + 1):
        add_player(conn, i, f"P{i}", f"p{i}")
        base = (n + 1 - i) / n  # P1 strongest, Pn weakest
        for ind in INDICATORS:
            _set_rating(conn, i, ind, base)


def test_snake_draft_14_split_seven_seven(conn: sqlite3.Connection) -> None:
    _seed_balanced(conn, 14)
    team_a, team_b, metrics = generate_teams(conn, list(range(1, 15)), WEIGHTS)
    assert len(team_a) == 7
    assert len(team_b) == 7
    assert set(team_a).isdisjoint(team_b)
    assert metrics.abs_delta < 0.5


def test_metrics_high_confidence_when_all_calibrated(conn: sqlite3.Connection) -> None:
    _seed_balanced(conn, 14)
    _, _, metrics = generate_teams(conn, list(range(1, 15)), WEIGHTS)
    assert metrics.calibration_confidence == "high"


def test_metrics_low_confidence_when_none_calibrated(conn: sqlite3.Connection) -> None:
    for i in range(1, 15):
        add_player(conn, i, f"P{i}", f"p{i}")
        for ind in INDICATORS:
            _set_rating(conn, i, ind, 0.5, calibrated=0)
    _, _, metrics = generate_teams(conn, list(range(1, 15)), WEIGHTS)
    assert metrics.calibration_confidence == "low"


def test_setter_constraint_holds_after_generate(conn: sqlite3.Connection) -> None:
    """Top setters must not all land on one team."""
    for i in range(1, 9):
        add_player(conn, i, f"P{i}", f"p{i}")
        for ind in NON_SETTING:
            _set_rating(conn, i, ind, (9 - i) / 8)
        _set_rating(conn, i, "setting", 5.0 if i <= 2 else 0.0)
    team_a, team_b, _ = generate_teams(conn, list(range(1, 9)), WEIGHTS)
    a_setters = sum(1 for p in team_a if p in {1, 2})
    b_setters = sum(1 for p in team_b if p in {1, 2})
    assert a_setters >= 1 and b_setters >= 1


def test_per_indicator_metrics_have_six_keys(conn: sqlite3.Connection) -> None:
    _seed_balanced(conn, 4)
    _, _, metrics = generate_teams(conn, [1, 2, 3, 4], WEIGHTS)
    assert set(metrics.per_indicator_a.keys()) == set(INDICATORS)
    assert set(metrics.per_indicator_b.keys()) == set(INDICATORS)


def test_empty_attendees(conn: sqlite3.Connection) -> None:
    team_a, team_b, metrics = generate_teams(conn, [], WEIGHTS)
    assert team_a == []
    assert team_b == []
    assert metrics.abs_delta == 0.0


def test_swap_players_swaps_correctly() -> None:
    new_a, new_b = swap_players([1, 2, 3], [4, 5, 6], 2, 5)
    assert sorted(new_a) == [1, 3, 5]
    assert sorted(new_b) == [2, 4, 6]


def test_swap_players_reverse_args() -> None:
    new_a, new_b = swap_players([1, 2, 3], [4, 5, 6], 5, 2)
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
