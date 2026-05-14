from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

AXES = ("attack", "defense", "setting")
UNDERSAMPLED_THRESHOLD = 5
SNOOZE_DURATION = timedelta(days=7)


@dataclass(frozen=True)
class Prompt:
    voter_id: int
    player_a: int
    player_b: int
    axis: str
    info_gain: float


def _compute_info_gain(total_votes: int) -> float:
    """Higher = more informative. Under-sampled pairs get a large bonus."""
    bonus = max(0, UNDERSAMPLED_THRESHOLD - total_votes) * 1000
    return float(bonus - total_votes)


CANDIDATE_SQL = """
WITH active AS (
    SELECT telegram_id FROM players WHERE active=1 AND telegram_id != :voter
),
pairs AS (
    SELECT a.telegram_id AS pa, b.telegram_id AS pb
    FROM active a JOIN active b ON a.telegram_id < b.telegram_id
),
axes(axis) AS (VALUES ('attack'), ('defense'), ('setting')),
snoozed AS (
    SELECT axis FROM snoozes
    WHERE voter_id = :voter AND snoozed_until > CURRENT_TIMESTAMP
)
SELECT
    p.pa AS player_a,
    p.pb AS player_b,
    x.axis AS axis,
    COALESCE(va.a_wins + va.b_wins, 0) AS total_votes
FROM pairs p
CROSS JOIN axes x
LEFT JOIN vote_aggregates va
    ON va.player_a = p.pa AND va.player_b = p.pb AND va.axis = x.axis
WHERE x.axis NOT IN (SELECT axis FROM snoozed)
  AND NOT EXISTS (
      SELECT 1 FROM answered_prompts ap
      WHERE ap.voter_id = :voter
        AND ap.player_a = p.pa
        AND ap.player_b = p.pb
        AND ap.axis = x.axis
  )
  AND NOT EXISTS (
      SELECT 1 FROM pending_prompts pp
      WHERE pp.voter_id = :voter
        AND pp.player_a = p.pa
        AND pp.player_b = p.pb
        AND pp.axis = x.axis
  )
ORDER BY
    CASE WHEN COALESCE(va.a_wins + va.b_wins, 0) < :threshold THEN 0 ELSE 1 END,
    COALESCE(va.a_wins + va.b_wins, 0) ASC,
    p.pa, p.pb, x.axis
LIMIT :slots
"""


def refill_queue(conn: sqlite3.Connection, voter_id: int, queue_depth: int) -> int:
    """Top up the voter's pending_prompts to queue_depth rows. Returns count inserted."""
    existing = conn.execute(
        "SELECT COUNT(*) AS n FROM pending_prompts WHERE voter_id=?",
        (voter_id,),
    ).fetchone()["n"]
    slots = queue_depth - existing
    if slots <= 0:
        return 0
    rows = conn.execute(
        CANDIDATE_SQL,
        {"voter": voter_id, "threshold": UNDERSAMPLED_THRESHOLD, "slots": slots},
    ).fetchall()
    for row in rows:
        info_gain = _compute_info_gain(row["total_votes"])
        conn.execute(
            "INSERT INTO pending_prompts (voter_id, player_a, player_b, axis, info_gain) "
            "VALUES (?, ?, ?, ?, ?)",
            (voter_id, row["player_a"], row["player_b"], row["axis"], info_gain),
        )
    conn.commit()
    return len(rows)


def peek_next_prompt(conn: sqlite3.Connection, voter_id: int) -> Prompt | None:
    """Return the highest-info-gain prompt without removing it."""
    row = conn.execute(
        "SELECT voter_id, player_a, player_b, axis, info_gain "
        "FROM pending_prompts WHERE voter_id=? "
        "ORDER BY info_gain DESC, player_a, player_b, axis "
        "LIMIT 1",
        (voter_id,),
    ).fetchone()
    if row is None:
        return None
    return Prompt(
        voter_id=row["voter_id"],
        player_a=row["player_a"],
        player_b=row["player_b"],
        axis=row["axis"],
        info_gain=row["info_gain"],
    )


def remove_prompt(
    conn: sqlite3.Connection, voter_id: int, player_a: int, player_b: int, axis: str
) -> None:
    conn.execute(
        "DELETE FROM pending_prompts WHERE voter_id=? AND player_a=? AND player_b=? AND axis=?",
        (voter_id, player_a, player_b, axis),
    )
    conn.commit()


def add_snooze(conn: sqlite3.Connection, voter_id: int, axis: str) -> datetime:
    """Snooze an axis for SNOOZE_DURATION. Replaces any existing snooze for that axis."""
    until = datetime.now(UTC) + SNOOZE_DURATION
    until_text = until.strftime("%Y-%m-%d %H:%M:%S")
    conn.execute(
        """
        INSERT INTO snoozes (voter_id, axis, snoozed_until)
        VALUES (?, ?, ?)
        ON CONFLICT(voter_id, axis) DO UPDATE SET snoozed_until=excluded.snoozed_until
        """,
        (voter_id, axis, until_text),
    )
    # Drop any pending prompts in the snoozed axis so the voter doesn't see them.
    conn.execute(
        "DELETE FROM pending_prompts WHERE voter_id=? AND axis=?",
        (voter_id, axis),
    )
    conn.commit()
    return until


def record_vote(
    conn: sqlite3.Connection,
    voter_id: int,
    player_a: int,
    player_b: int,
    axis: str,
    winner: str,
) -> None:
    """Record an aggregate increment + voter-side dedupe.

    Privacy invariant: vote_aggregates row carries no voter_id; answered_prompts
    carries voter_id but never the outcome. These two writes happen in the same
    transaction but the tables are never joined downstream.
    """
    if winner not in ("a", "b"):
        raise ValueError(f"winner must be 'a' or 'b', got {winner!r}")
    a_inc = 1 if winner == "a" else 0
    b_inc = 1 if winner == "b" else 0
    conn.execute(
        """
        INSERT INTO vote_aggregates (player_a, player_b, axis, a_wins, b_wins, updated_at)
        VALUES (?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
        ON CONFLICT(player_a, player_b, axis) DO UPDATE SET
            a_wins = a_wins + excluded.a_wins,
            b_wins = b_wins + excluded.b_wins,
            updated_at = CURRENT_TIMESTAMP
        """,
        (player_a, player_b, axis, a_inc, b_inc),
    )
    conn.execute(
        """
        INSERT OR IGNORE INTO answered_prompts (voter_id, player_a, player_b, axis)
        VALUES (?, ?, ?, ?)
        """,
        (voter_id, player_a, player_b, axis),
    )
    conn.execute(
        "DELETE FROM pending_prompts WHERE voter_id=? AND player_a=? AND player_b=? AND axis=?",
        (voter_id, player_a, player_b, axis),
    )
    conn.commit()


def mark_dont_know(
    conn: sqlite3.Connection,
    voter_id: int,
    player_a: int,
    player_b: int,
    axis: str,
) -> None:
    """Voter declined to compare. Dedupe only — no aggregate increment."""
    conn.execute(
        """
        INSERT OR IGNORE INTO answered_prompts (voter_id, player_a, player_b, axis)
        VALUES (?, ?, ?, ?)
        """,
        (voter_id, player_a, player_b, axis),
    )
    conn.execute(
        "DELETE FROM pending_prompts WHERE voter_id=? AND player_a=? AND player_b=? AND axis=?",
        (voter_id, player_a, player_b, axis),
    )
    conn.commit()


def insert_priority_prompt(
    conn: sqlite3.Connection,
    voter_id: int,
    player_a: int,
    player_b: int,
    axis: str,
    info_gain: float = 1_000_000.0,
) -> None:
    """Inject a high-priority prompt (used for calibration bootstrap on new player)."""
    if player_a == player_b:
        return
    a, b = (player_a, player_b) if player_a < player_b else (player_b, player_a)
    if voter_id in (a, b):
        return
    conn.execute(
        """
        INSERT OR IGNORE INTO pending_prompts
            (voter_id, player_a, player_b, axis, info_gain)
        VALUES (?, ?, ?, ?, ?)
        """,
        (voter_id, a, b, axis, info_gain),
    )
    conn.commit()
