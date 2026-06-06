from __future__ import annotations

import sqlite3
from statistics import mean, pstdev

INDICATORS = ("attack", "receive", "block", "setting", "serve", "positioning")

EPSILON = 1e-9

# Defaults mirror config.Settings; callers pass the configured values.
DEFAULT_NORM_MIN_RATINGS = 8
DEFAULT_SHRINKAGE_K = 3.0


def _rater_stats(
    scores: list[sqlite3.Row],
) -> tuple[dict[int, tuple[float, float, int]], float]:
    """Return ({voter_id: (mean, pstdev, count)}, global_mean).

    Per-rater stats span ALL of that rater's scores (across players + indicators)
    to capture their general leniency/severity. global_mean is the fallback
    anchor for raters with too few scores to trust their own mean.
    """
    by_rater: dict[int, list[int]] = {}
    all_scores: list[int] = []
    for row in scores:
        by_rater.setdefault(row["voter_id"], []).append(row["score"])
        all_scores.append(row["score"])
    stats: dict[int, tuple[float, float, int]] = {}
    for voter_id, vals in by_rater.items():
        stats[voter_id] = (mean(vals), pstdev(vals), len(vals))
    global_mean = mean(all_scores) if all_scores else 0.0
    return stats, global_mean


def _normalized_contribution(
    score: int,
    rater_mean: float,
    rater_sd: float,
    rater_count: int,
    global_mean: float,
    normalize: bool,
    norm_min_ratings: int,
) -> float:
    """Map a raw 1-5 score to a rater-bias-corrected contribution.

    - normalization off → raw score.
    - rater below the min-count threshold → global-mean shift only (their own
      mean isn't trustworthy yet).
    - rater gave near-identical scores (sd≈0) → leniency shift only (can't divide).
    - otherwise → full z-score (score − rater_mean) / rater_sd.
    """
    if not normalize:
        return float(score)
    if rater_count < norm_min_ratings:
        return float(score) - global_mean
    if rater_sd < EPSILON:
        return float(score) - rater_mean
    return (float(score) - rater_mean) / rater_sd


def refresh_ratings(
    conn: sqlite3.Connection,
    calibration_threshold: int,
    *,
    normalize: bool = True,
    norm_min_ratings: int = DEFAULT_NORM_MIN_RATINGS,
    shrinkage_k: float = DEFAULT_SHRINKAGE_K,
) -> int:
    """Recompute player_ratings from the `scores` table for all active players.

    Each raw score is rater-normalized (z-scored to cancel leniency/severity),
    then per (player, indicator) the contributions are averaged with shrinkage
    toward the global mean: estimate = Σ n / (count + shrinkage_k). A
    (player, indicator) with no real scores is left untouched so warm-start
    priors survive until real votes replace them.

    Returns the count of player_ratings rows written.
    """
    active_ids = {
        r["telegram_id"]
        for r in conn.execute("SELECT telegram_id FROM players WHERE active=1").fetchall()
    }
    if not active_ids:
        return 0

    score_rows = conn.execute("SELECT voter_id, player_id, indicator, score FROM scores").fetchall()
    rater_stats, global_mean = _rater_stats(score_rows)

    # Accumulate normalized contributions per (player, indicator).
    sums: dict[tuple[int, str], float] = {}
    counts: dict[tuple[int, str], int] = {}
    for row in score_rows:
        pid = row["player_id"]
        if pid not in active_ids:
            continue
        r_mean, r_sd, r_count = rater_stats[row["voter_id"]]
        n = _normalized_contribution(
            row["score"], r_mean, r_sd, r_count, global_mean, normalize, norm_min_ratings
        )
        key = (pid, row["indicator"])
        sums[key] = sums.get(key, 0.0) + n
        counts[key] = counts.get(key, 0) + 1

    rows_written = 0
    for (pid, indicator), count in counts.items():
        estimate = sums[(pid, indicator)] / (count + shrinkage_k)
        calibrated = 1 if count >= calibration_threshold else 0
        conn.execute(
            """
            INSERT INTO player_ratings
                (telegram_id, indicator, score, vote_count, calibrated, computed_at)
            VALUES (?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
            ON CONFLICT(telegram_id, indicator) DO UPDATE SET
                score = excluded.score,
                vote_count = excluded.vote_count,
                calibrated = excluded.calibrated,
                computed_at = CURRENT_TIMESTAMP
            """,
            (pid, indicator, estimate, count, calibrated),
        )
        rows_written += 1

    # Promote any player whose all 6 indicators are calibrated.
    conn.execute(
        """
        UPDATE players SET is_calibrating = 0
        WHERE telegram_id IN (
            SELECT telegram_id FROM player_ratings
            GROUP BY telegram_id
            HAVING SUM(calibrated) = 6
        )
        """
    )
    conn.commit()
    return rows_written


def get_player_ratings(
    conn: sqlite3.Connection, telegram_id: int
) -> dict[str, tuple[float, int, bool]]:
    """Return {indicator: (score, vote_count, calibrated)} for one player."""
    rows = conn.execute(
        "SELECT indicator, score, vote_count, calibrated FROM player_ratings WHERE telegram_id=?",
        (telegram_id,),
    ).fetchall()
    return {r["indicator"]: (r["score"], r["vote_count"], bool(r["calibrated"])) for r in rows}


def composite_score(
    conn: sqlite3.Connection,
    telegram_id: int,
    weights: dict[str, float],
) -> tuple[float, str]:
    """Return (weighted_score, calibration_status).

    weights maps indicator → weight; it does NOT need to sum to 1.0 (scaled
    appropriately). Status: 'calibrated' (all 6), 'partial' (1-5), 'calibrating' (0).
    """
    ratings = get_player_ratings(conn, telegram_id)
    score = sum(
        ratings.get(indicator, (0.0, 0, False))[0] * weights.get(indicator, 0.0)
        for indicator in INDICATORS
    )
    calibrated_count = sum(
        1 for indicator in INDICATORS if ratings.get(indicator, (0.0, 0, False))[2]
    )
    status = (
        "calibrated"
        if calibrated_count == len(INDICATORS)
        else "partial"
        if calibrated_count > 0
        else "calibrating"
    )
    return score, status
