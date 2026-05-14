from __future__ import annotations

import sqlite3

import numpy as np

AXES = ("attack", "defense", "setting")

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


def _load_aggregates_for_axis(
    conn: sqlite3.Connection, axis: str
) -> dict[tuple[int, int], tuple[int, int]]:
    rows = conn.execute(
        "SELECT player_a, player_b, a_wins, b_wins FROM vote_aggregates WHERE axis=?",
        (axis,),
    ).fetchall()
    return {(r["player_a"], r["player_b"]): (r["a_wins"], r["b_wins"]) for r in rows}


def _vote_count_per_player(
    conn: sqlite3.Connection, axis: str
) -> dict[int, int]:
    """Total votes touching each player in a given axis."""
    rows = conn.execute(
        """
        SELECT player_a AS pid, SUM(a_wins + b_wins) AS n
        FROM vote_aggregates WHERE axis=? GROUP BY player_a
        UNION ALL
        SELECT player_b AS pid, SUM(a_wins + b_wins) AS n
        FROM vote_aggregates WHERE axis=? GROUP BY player_b
        """,
        (axis, axis),
    ).fetchall()
    counts: dict[int, int] = {}
    for r in rows:
        counts[r["pid"]] = counts.get(r["pid"], 0) + (r["n"] or 0)
    return counts


def refresh_ratings(
    conn: sqlite3.Connection,
    calibration_threshold: int,
) -> int:
    """Refit BT per axis for all active players; write to player_ratings.

    Returns the count of rows written.
    """
    active_ids = [
        r["telegram_id"]
        for r in conn.execute("SELECT telegram_id FROM players WHERE active=1").fetchall()
    ]
    if not active_ids:
        return 0

    rows_written = 0
    for axis in AXES:
        aggregates = _load_aggregates_for_axis(conn, axis)
        scores = fit_bradley_terry(aggregates, player_ids=active_ids)
        vote_counts = _vote_count_per_player(conn, axis)
        for pid in active_ids:
            score = scores.get(pid, 0.0)
            count = vote_counts.get(pid, 0)
            calibrated = 1 if count >= calibration_threshold else 0
            conn.execute(
                """
                INSERT INTO player_ratings
                    (telegram_id, axis, score, vote_count, calibrated, computed_at)
                VALUES (?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
                ON CONFLICT(telegram_id, axis) DO UPDATE SET
                    score = excluded.score,
                    vote_count = excluded.vote_count,
                    calibrated = excluded.calibrated,
                    computed_at = CURRENT_TIMESTAMP
                """,
                (pid, axis, score, count, calibrated),
            )
            rows_written += 1
    # Promote any player whose all 3 axes are calibrated.
    conn.execute(
        """
        UPDATE players SET is_calibrating = 0
        WHERE telegram_id IN (
            SELECT telegram_id FROM player_ratings
            GROUP BY telegram_id
            HAVING SUM(calibrated) = 3
        )
        """
    )
    conn.commit()
    return rows_written


def get_player_ratings(
    conn: sqlite3.Connection, telegram_id: int
) -> dict[str, tuple[float, int, bool]]:
    """Return {axis: (score, vote_count, calibrated)} for one player."""
    rows = conn.execute(
        "SELECT axis, score, vote_count, calibrated FROM player_ratings WHERE telegram_id=?",
        (telegram_id,),
    ).fetchall()
    return {
        r["axis"]: (r["score"], r["vote_count"], bool(r["calibrated"]))
        for r in rows
    }


def composite_score(
    conn: sqlite3.Connection,
    telegram_id: int,
    weights: tuple[float, float, float],
) -> tuple[float, str]:
    """Return (weighted_score, calibration_status).

    weights is (attack, defense, setting) — does NOT need to sum to 1.0;
    scaled appropriately. Status: 'calibrated' (all 3), 'partial' (1-2),
    'calibrating' (0).
    """
    ratings = get_player_ratings(conn, telegram_id)
    w_attack, w_defense, w_setting = weights
    axis_weights = {"attack": w_attack, "defense": w_defense, "setting": w_setting}
    score = sum(
        ratings.get(axis, (0.0, 0, False))[0] * w
        for axis, w in axis_weights.items()
    )
    calibrated_count = sum(
        1 for axis in AXES if ratings.get(axis, (0.0, 0, False))[2]
    )
    status = (
        "calibrated" if calibrated_count == 3
        else "partial" if calibrated_count > 0
        else "calibrating"
    )
    return score, status
