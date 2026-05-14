from __future__ import annotations

import numpy as np
import pytest

from toop.rating import fit_bradley_terry


def test_empty_returns_empty() -> None:
    assert fit_bradley_terry({}) == {}


def test_player_ids_only_no_aggregates_returns_zeros() -> None:
    res = fit_bradley_terry({}, player_ids=[1, 2, 3])
    assert set(res.keys()) == {1, 2, 3}
    for v in res.values():
        assert v == 0.0


def test_two_player_dominance() -> None:
    # Player 1 beats player 2 every time
    res = fit_bradley_terry({(1, 2): (10, 0)})
    assert res[1] > res[2]
    assert abs(res[1] + res[2]) < 1e-9  # mean-centered


def test_ranking_matches_synthetic_truth() -> None:
    """Generate matches from known skills, fitter should recover the order."""
    rng = np.random.default_rng(seed=42)
    true_log_skills = {1: 2.0, 2: 1.0, 3: 0.0, 4: -1.0, 5: -2.0}
    true_pi = {p: np.exp(s) for p, s in true_log_skills.items()}
    aggregates: dict[tuple[int, int], list[int]] = {}
    n_games = 100
    for i in range(1, 6):
        for j in range(i + 1, 6):
            p_i_wins = true_pi[i] / (true_pi[i] + true_pi[j])
            wins_i = int(rng.binomial(n_games, p_i_wins))
            aggregates[(i, j)] = [wins_i, n_games - wins_i]
    agg_tuples = {k: (v[0], v[1]) for k, v in aggregates.items()}
    res = fit_bradley_terry(agg_tuples)
    ranking = sorted(res, key=res.get, reverse=True)
    assert ranking == [1, 2, 3, 4, 5]


def test_isolated_player_gets_median() -> None:
    aggs = {(1, 2): (5, 5), (1, 3): (5, 5), (2, 3): (5, 5)}
    res = fit_bradley_terry(aggs, player_ids=[1, 2, 3, 99])
    median = float(np.median([res[1], res[2], res[3]]))
    assert abs(res[99] - median) < 1e-9


def test_all_wins_no_divergence() -> None:
    """Player 1 beats 2, 3, 4 every time; prior keeps log-skill finite."""
    aggs = {
        (1, 2): (10, 0),
        (1, 3): (10, 0),
        (1, 4): (10, 0),
        (2, 3): (5, 5),
        (2, 4): (5, 5),
        (3, 4): (5, 5),
    }
    res = fit_bradley_terry(aggs)
    assert np.isfinite(res[1])
    assert res[1] > 0
    for p in (2, 3, 4):
        assert res[p] < res[1]


def test_mean_centered() -> None:
    aggs = {(1, 2): (3, 2), (1, 3): (4, 1), (2, 3): (1, 4)}
    res = fit_bradley_terry(aggs)
    assert abs(sum(res.values())) < 1e-9


def test_idempotent_under_same_input() -> None:
    aggs = {(1, 2): (3, 2), (1, 3): (4, 1), (2, 3): (1, 4)}
    a = fit_bradley_terry(aggs)
    b = fit_bradley_terry(aggs)
    for k in a:
        assert pytest.approx(a[k]) == b[k]
