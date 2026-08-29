from __future__ import annotations

import sqlite3
from dataclasses import dataclass

from toop.config import settings
from toop.rating import INDICATORS

_VALID_INDICATORS = frozenset(INDICATORS)


@dataclass(frozen=True)
class ScoreTarget:
    """One thing to rate: a player on a single indicator."""

    player_id: int
    indicator: str


@dataclass(frozen=True)
class PendingCounts:
    """What one voter still owes, split by why.

    `unscored` is coverage they have never given; `stale` is coverage that has
    aged past REVOTE_AFTER_DAYS and is due a refresh. Keeping them apart is what
    stops a voter who finished months ago from looking, in /health, exactly like
    one who has never voted.
    """

    unscored: int
    stale: int

    @property
    def total(self) -> int:
        return self.unscored + self.stale


# Rateable players: active, in the pool, and not currently paused.
#
# The self-exclusion (nobody rates themselves) deliberately does NOT live here.
# A CTE cannot correlate to an outer FROM alias and SQLite has no LATERAL, so
# keeping `telegram_id != :voter` inside this fragment would make it unusable by
# the whole-roster query in pending_counts_by_voter. Each caller applies its own
# form instead: `r.telegram_id != :voter` here, a join predicate there.
_RATEABLE_CTE = """
    SELECT telegram_id FROM players
    WHERE active=1 AND in_pool=1
      AND (pool_paused_until IS NULL OR pool_paused_until <= CURRENT_TIMESTAMP)
"""

_INDICATORS_CTE = """
    VALUES ('attack'), ('receive'), ('block'), ('setting'), ('serve'), ('positioning')
"""

# A ندیدمش skip hides the WHOLE player from that voter, on every indicator,
# until the row ages past SKIP_COOLDOWN_DAYS. Two placeholders, not one: the
# callers bind the voter differently (a parameter vs a column) AND alias the
# rateable table differently, so both expressions have to be injected.
_SKIP_FILTER = """
    NOT EXISTS (
        SELECT 1 FROM score_skips sk
        WHERE sk.voter_id = {voter} AND sk.player_id = {player}
          AND sk.skipped_at > datetime('now', '-' || :cooldown_days || ' days')
    )
"""

# Pick the next (player, indicator) for a voter. Under-sampled players (fewest
# existing scores on that indicator) surface first so coverage evens out.
#
# A target qualifies when the voter has never scored it, OR when their score has
# aged past REVOTE_AFTER_DAYS: people rate better once they have actually played
# together, so an opinion formed months ago is worth asking again. Re-scoring
# resets updated_at, which is what keeps this from looping.
#
# Ordering, in precedence order:
#   1. :exclude_player (the player just rated) sorts last, so a DIFFERENT player
#      surfaces next instead of cycling one name across all six indicators. This
#      deliberately outranks freshness — a stale target elsewhere beats an
#      unscored one on the player still on screen.
#   2. is_revote: among everyone else, never-scored beats needs-refreshing.
#   3. updated_at: oldest stale target first. NULL on every unscored row, so it
#      ties there and total/voter_count still govern coverage.
_NEXT_TARGET_SQL = f"""
WITH rateable AS ({_RATEABLE_CTE}),
indicators(indicator) AS ({_INDICATORS_CTE})
SELECT
    r.telegram_id AS player_id,
    i.indicator AS indicator,
    (sc.voter_id IS NOT NULL) AS is_revote,
    (SELECT COUNT(*) FROM scores s
        WHERE s.player_id = r.telegram_id AND s.indicator = i.indicator) AS total,
    (SELECT COUNT(*) FROM scores s
        WHERE s.voter_id = :voter AND s.player_id = r.telegram_id) AS voter_count
FROM rateable r
CROSS JOIN indicators i
LEFT JOIN scores sc
    ON sc.voter_id = :voter
   AND sc.player_id = r.telegram_id
   AND sc.indicator = i.indicator
WHERE r.telegram_id != :voter
  AND (
        sc.voter_id IS NULL
        OR sc.updated_at <= datetime('now', '-' || :revote_days || ' days')
      )
  AND {_SKIP_FILTER.format(voter=":voter", player="r.telegram_id")}
ORDER BY
    (r.telegram_id = :exclude_player),
    is_revote ASC,
    sc.updated_at ASC,
    total ASC,
    voter_count ASC,
    r.telegram_id, i.indicator
LIMIT 1
"""


def select_next_score_target(
    conn: sqlite3.Connection,
    voter_id: int,
    exclude_player: int | None = None,
) -> ScoreTarget | None:
    """Return the next (player, indicator) the voter should rate, or None when
    they've covered everyone and nothing has gone stale yet.

    A ندیدمش skip hides the WHOLE player from this voter — every indicator, not
    just the one that was on screen — until the row ages past
    SKIP_COOLDOWN_DAYS. A score the voter already gave comes back once it ages
    past REVOTE_AFTER_DAYS, after every never-scored target on another player.

    Both windows are bound as integers and concatenated into the date modifier;
    binding a pre-formatted string risks a NULL modifier, which would make the
    comparison silently false and ignore the window entirely.
    """
    row = conn.execute(
        _NEXT_TARGET_SQL,
        {
            "voter": voter_id,
            "exclude_player": exclude_player,
            "cooldown_days": settings.SKIP_COOLDOWN_DAYS,
            "revote_days": settings.REVOTE_AFTER_DAYS,
        },
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
    score. Every skip of that PLAYER is cleared, not just the matching
    indicator: a score proves the voter can rate them, which retracts the
    "I haven't seen them play" claim the skip stood for.
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
        "DELETE FROM score_skips WHERE voter_id=? AND player_id=?",
        (voter_id, player_id),
    )
    conn.commit()


def record_skip(
    conn: sqlite3.Connection,
    voter_id: int,
    player_id: int,
    indicator: str,
    session_id: int | None = None,
) -> None:
    """Voter declined to rate this target (🤷 ندیدمش): they haven't seen this
    player play. The row hides that player from this voter across ALL indicators
    until it ages past SKIP_COOLDOWN_DAYS; skipped_at is refreshed on conflict so
    a repeat skip restarts the window.

    session_id is still recorded, but only as an audit trail of which session the
    voter was in — it no longer gates what the queue offers.
    """
    if indicator not in _VALID_INDICATORS:
        raise ValueError(f"unknown indicator {indicator!r}")
    if voter_id == player_id:
        raise ValueError("a voter cannot skip themselves")
    conn.execute(
        """
        INSERT INTO score_skips (voter_id, player_id, indicator, session_id)
        VALUES (?, ?, ?, ?)
        ON CONFLICT(voter_id, player_id, indicator) DO UPDATE SET
            session_id = excluded.session_id,
            skipped_at = CURRENT_TIMESTAMP
        """,
        (voter_id, player_id, indicator, session_id),
    )
    conn.commit()


# Per-voter tally of everything select_next_score_target would be willing to
# offer, split into never-scored and needs-refreshing. Same predicates as that
# query, reusing the same two fragments, so the two can't drift.
#
# Three things here are load-bearing and were each got wrong first:
#   1. Every rp-referencing predicate (self-exclusion, skip filter) lives in the
#      LEFT JOIN's ON. In the WHERE it annihilates the outer join, so a voter
#      who has skipped everyone drops out of the result instead of appearing
#      with a zero count.
#   2. The SUMs test rp.telegram_id IS NOT NULL, not just the score. Otherwise
#      the six placeholder rows a voter with no rateable targets produces get
#      counted as unscored.
#   3. The staleness window lives INSIDE the stale SUM. In the join ON it would
#      make fresh-scored targets fall through and be counted as unscored.
_PENDING_COUNTS_SQL = f"""
WITH rateable AS ({_RATEABLE_CTE}),
indicators(indicator) AS ({_INDICATORS_CTE})
SELECT
    v.telegram_id AS voter_id,
    SUM(rp.telegram_id IS NOT NULL AND sc.voter_id IS NULL) AS unscored,
    SUM(rp.telegram_id IS NOT NULL AND sc.voter_id IS NOT NULL
        AND sc.updated_at <= datetime('now', '-' || :revote_days || ' days')) AS stale
FROM players v
CROSS JOIN indicators i
LEFT JOIN rateable rp
    ON rp.telegram_id != v.telegram_id
   AND {_SKIP_FILTER.format(voter="v.telegram_id", player="rp.telegram_id")}
LEFT JOIN scores sc
    ON sc.voter_id = v.telegram_id
   AND sc.player_id = rp.telegram_id
   AND sc.indicator = i.indicator
WHERE v.active = 1
GROUP BY v.telegram_id
"""


def pending_counts_by_voter(conn: sqlite3.Connection) -> dict[int, PendingCounts]:
    """Return {voter_id: PendingCounts} for every active player.

    Ghosts are included, matching what /health already renders — they are
    active players with a row in the table. The "can we actually DM them"
    question belongs to revote.nudge_targets, not to a counting function.

    Every active player gets a key, including those owing nothing.
    """
    rows = conn.execute(
        _PENDING_COUNTS_SQL,
        {
            "cooldown_days": settings.SKIP_COOLDOWN_DAYS,
            "revote_days": settings.REVOTE_AFTER_DAYS,
        },
    ).fetchall()
    return {
        row["voter_id"]: PendingCounts(unscored=row["unscored"], stale=row["stale"]) for row in rows
    }
