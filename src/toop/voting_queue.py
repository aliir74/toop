from __future__ import annotations

import sqlite3
from dataclasses import dataclass

from toop.rating import INDICATORS

_VALID_INDICATORS = frozenset(INDICATORS)


@dataclass(frozen=True)
class ScoreTarget:
    """One thing to rate: a player on a single indicator."""

    player_id: int
    indicator: str


# Pick the next unscored (player, indicator) for a voter. Under-sampled players
# (fewest existing scores on that indicator) surface first so coverage evens out.
# A player is rateable iff active=1 AND in_pool=1 AND not currently paused, and
# is never the voter themselves. Already-scored and already-skipped targets are
# excluded. :exclude_player (the player just rated) sorts last so a DIFFERENT
# player surfaces next when one exists, instead of cycling one name across all
# six indicators back-to-back.
_NEXT_TARGET_SQL = """
WITH rateable AS (
    SELECT telegram_id FROM players
    WHERE active=1 AND in_pool=1
      AND (pool_paused_until IS NULL OR pool_paused_until <= CURRENT_TIMESTAMP)
      AND telegram_id != :voter
),
indicators(indicator) AS (
    VALUES ('attack'), ('receive'), ('block'), ('setting'), ('serve'), ('positioning')
)
SELECT
    r.telegram_id AS player_id,
    i.indicator AS indicator,
    (SELECT COUNT(*) FROM scores s
        WHERE s.player_id = r.telegram_id AND s.indicator = i.indicator) AS total
FROM rateable r
CROSS JOIN indicators i
WHERE NOT EXISTS (
        SELECT 1 FROM scores sc
        WHERE sc.voter_id = :voter AND sc.player_id = r.telegram_id AND sc.indicator = i.indicator
    )
  AND NOT EXISTS (
        SELECT 1 FROM score_skips sk
        WHERE sk.voter_id = :voter AND sk.player_id = r.telegram_id AND sk.indicator = i.indicator
    )
ORDER BY
    (r.telegram_id = :exclude_player),
    total ASC,
    r.telegram_id, i.indicator
LIMIT 1
"""


def select_next_score_target(
    conn: sqlite3.Connection,
    voter_id: int,
    exclude_player: int | None = None,
) -> ScoreTarget | None:
    """Return the next (player, indicator) the voter should rate, or None when
    they've covered everyone."""
    row = conn.execute(
        _NEXT_TARGET_SQL, {"voter": voter_id, "exclude_player": exclude_player}
    ).fetchone()
    if row is None:
        return None
    return ScoreTarget(player_id=row["player_id"], indicator=row["indicator"])


def record_score(
    conn: sqlite3.Connection,
    voter_id: int,
    player_id: int,
    indicator: str,
    score: int,
) -> None:
    """Record (or update) a voter's 1-5 score for a player on one indicator.

    UPSERT on the (voter, player, indicator) key makes re-tapping edit the prior
    score. Any earlier skip of the same target is cleared.
    """
    if indicator not in _VALID_INDICATORS:
        raise ValueError(f"unknown indicator {indicator!r}")
    if not 1 <= score <= 5:
        raise ValueError(f"score must be 1..5, got {score!r}")
    if voter_id == player_id:
        raise ValueError("a voter cannot score themselves")
    conn.execute(
        """
        INSERT INTO scores (voter_id, player_id, indicator, score, updated_at)
        VALUES (?, ?, ?, ?, CURRENT_TIMESTAMP)
        ON CONFLICT(voter_id, player_id, indicator) DO UPDATE SET
            score = excluded.score,
            updated_at = CURRENT_TIMESTAMP
        """,
        (voter_id, player_id, indicator, score),
    )
    conn.execute(
        "DELETE FROM score_skips WHERE voter_id=? AND player_id=? AND indicator=?",
        (voter_id, player_id, indicator),
    )
    conn.commit()


def record_skip(
    conn: sqlite3.Connection,
    voter_id: int,
    player_id: int,
    indicator: str,
) -> None:
    """Voter declined to rate this target (🤷 ندیدمش). Records a dedupe row so it
    isn't re-asked; stores no score."""
    if indicator not in _VALID_INDICATORS:
        raise ValueError(f"unknown indicator {indicator!r}")
    if voter_id == player_id:
        raise ValueError("a voter cannot skip themselves")
    conn.execute(
        """
        INSERT OR IGNORE INTO score_skips (voter_id, player_id, indicator)
        VALUES (?, ?, ?)
        """,
        (voter_id, player_id, indicator),
    )
    conn.commit()
