from __future__ import annotations

import numpy as np

EPSILON = 1e-12


def fit_bradley_terry(
    aggregates: dict[tuple[int, int], tuple[int, int]],
    player_ids: list[int] | None = None,
    max_iter: int = 50,
    tol: float = 1e-6,
    prior_weight: float = 0.5,
) -> dict[int, float]:
    """Fit Bradley-Terry skills via the MM iteration (Hunter 2004).

    Args:
        aggregates: mapping {(i, j): (w_ij, w_ji)} where i < j and w_ij is the
            number of times i was preferred over j. Outcomes must be ints >= 0.
        player_ids: optional explicit player set. Players in this list with no
            comparisons are assigned the median log-skill (the "isolated" case).
            If None, derives the set from `aggregates`.
        max_iter: maximum MM iterations.
        tol: max-relative-change convergence threshold.
        prior_weight: virtual-game weight against a unit-skill anchor; stabilizes
            divergence on all-wins/all-losses players.

    Returns:
        mapping {telegram_id: log-skill}, mean-centered at 0.
    """
    appearing: set[int] = set()
    for (i, j), _outcomes in aggregates.items():
        appearing.add(i)
        appearing.add(j)

    if player_ids is None:
        player_ids = sorted(appearing)

    if not player_ids:
        return {}

    connected = [pid for pid in player_ids if pid in appearing]
    isolated = [pid for pid in player_ids if pid not in appearing]

    if not connected:
        return dict.fromkeys(player_ids, 0.0)

    idx = {pid: k for k, pid in enumerate(connected)}
    n = len(connected)
    wins = np.zeros(n)
    games = np.zeros((n, n))
    for (i, j), (w_ij, w_ji) in aggregates.items():
        if i not in idx or j not in idx:
            continue
        ki, kj = idx[i], idx[j]
        wins[ki] += w_ij
        wins[kj] += w_ji
        total = w_ij + w_ji
        games[ki, kj] += total
        games[kj, ki] += total

    wins_with_prior = wins + prior_weight
    pi = np.ones(n)

    for _ in range(max_iter):
        with np.errstate(divide="ignore", invalid="ignore"):
            pair_sum = pi[:, None] + pi[None, :]
            np.fill_diagonal(pair_sum, 1.0)
            denom_pairs = np.where(games > 0, games / pair_sum, 0.0).sum(axis=1)
        virtual = 2 * prior_weight / (pi + 1.0)
        denom = denom_pairs + virtual
        new_pi = wins_with_prior / np.maximum(denom, EPSILON)

        # Geometric-mean normalize each step to keep scale stable.
        log_pi = np.log(np.maximum(new_pi, EPSILON))
        log_pi -= log_pi.mean()
        new_pi = np.exp(log_pi)

        if np.max(np.abs(new_pi - pi) / np.maximum(pi, EPSILON)) < tol:
            pi = new_pi
            break
        pi = new_pi

    log_skills = np.log(np.maximum(pi, EPSILON))
    log_skills -= log_skills.mean()

    result: dict[int, float] = {
        pid: float(log_skills[idx[pid]]) for pid in connected
    }
    if isolated:
        median = float(np.median(list(result.values())))
        for pid in isolated:
            result[pid] = median
    return result
