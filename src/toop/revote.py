"""Who should be nudged to come back and vote, and when they last were.

The queue (``voting_queue``) decides WHAT a voter is asked. This module only
decides WHO gets a DM about it. Nothing here ever writes to ``scores`` or widens
REVOTE_AFTER_DAYS, so no code path can ask anyone to re-rate everything.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass

from toop.voting_queue import pending_counts_by_voter


@dataclass(frozen=True)
class NudgeTarget:
    """One player worth DMing, with the shape of what they owe."""

    telegram_id: int
    display_name: str
    unscored: int
    stale: int

    @property
    def total(self) -> int:
        return self.unscored + self.stale

    @property
    def first_name(self) -> str:
        """First word of the display name, for a friendlier DM opener.

        Mirrors handlers/voting._build_nudge_templates. display_name is NOT NULL
        in the schema, but a name of only whitespace would make split() empty, so
        fall back to the whole string.
        """
        parts = self.display_name.split()
        return parts[0] if parts else self.display_name


# Candidates for a nudge: active, non-ghost, plausibly reachable, and not DMed
# inside the cooldown.
#
# Reachability is a disjunction, deliberately not an inner JOIN on contacts.
# upsert_contact runs in exactly one place (handle_start's private-chat branch),
# so `contacts` records "typed /start in a DM", not "is reachable" — voting or
# tapping a rating button writes nothing. The table also postdates the bot's
# first weeks, so long-standing players who /start-ed before it existed have no
# row despite voting today. Having cast any score is itself proof of an open DM,
# because the whole vote flow is DM-only.
_CANDIDATES_SQL = """
SELECT p.telegram_id, p.display_name, n.last_nudged_at
FROM players p
LEFT JOIN revote_nudges n ON n.telegram_id = p.telegram_id
WHERE p.active = 1
  AND p.is_ghost = 0
  AND (
        EXISTS (SELECT 1 FROM contacts c WHERE c.telegram_id = p.telegram_id)
        OR EXISTS (SELECT 1 FROM scores s WHERE s.voter_id = p.telegram_id)
      )
ORDER BY p.display_name COLLATE NOCASE
"""


def nudge_targets(
    conn: sqlite3.Connection,
    *,
    cooldown_days: int,
    min_pending: int,
    ignore_cooldown: bool = False,
) -> list[NudgeTarget]:
    """Players who owe at least ``min_pending`` targets and are due a nudge.

    ``ignore_cooldown`` waives only the per-voter DM cooldown (what
    ``/revote_ping force`` does). It does NOT widen the staleness window, so the
    set of targets a nudged voter is then asked about is completely unchanged.
    """
    cutoff = None if ignore_cooldown else f"-{int(cooldown_days)} days"
    counts = pending_counts_by_voter(conn)
    targets: list[NudgeTarget] = []
    for row in conn.execute(_CANDIDATES_SQL).fetchall():
        pending = counts.get(row["telegram_id"])
        if pending is None or pending.total < min_pending:
            continue
        if cutoff is not None and row["last_nudged_at"] is not None:
            still_cooling = conn.execute(
                "SELECT ? > datetime('now', ?) AS hot", (row["last_nudged_at"], cutoff)
            ).fetchone()["hot"]
            if still_cooling:
                continue
        targets.append(
            NudgeTarget(
                telegram_id=row["telegram_id"],
                display_name=row["display_name"],
                unscored=pending.unscored,
                stale=pending.stale,
            )
        )
    return targets


def record_nudge(conn: sqlite3.Connection, telegram_id: int) -> None:
    """Start this player's cooldown. Call only after a DM actually landed."""
    conn.execute(
        """
        INSERT INTO revote_nudges (telegram_id, last_nudged_at)
        VALUES (?, CURRENT_TIMESTAMP)
        ON CONFLICT(telegram_id) DO UPDATE SET last_nudged_at = CURRENT_TIMESTAMP
        """,
        (telegram_id,),
    )
    conn.commit()
