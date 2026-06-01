from __future__ import annotations

import sqlite3
from dataclasses import dataclass

RSVP_STATUSES = ("yes", "no", "maybe")


@dataclass(frozen=True)
class RsvpCounts:
    yes: int
    no: int
    maybe: int

    @property
    def total(self) -> int:
        return self.yes + self.no + self.maybe


def upsert_rsvp(
    conn: sqlite3.Connection,
    session_id: int,
    telegram_id: int,
    status: str,
) -> None:
    """Insert or update the player's RSVP. Idempotent on (session_id, telegram_id)."""
    if status not in RSVP_STATUSES:
        raise ValueError(f"status must be one of {RSVP_STATUSES}, got {status!r}")
    conn.execute(
        """
        INSERT INTO rsvps (session_id, telegram_id, status)
        VALUES (?, ?, ?)
        ON CONFLICT(session_id, telegram_id) DO UPDATE SET
            status=excluded.status,
            created_at=CURRENT_TIMESTAMP
        """,
        (session_id, telegram_id, status),
    )
    conn.commit()


def count_rsvps(conn: sqlite3.Connection, session_id: int) -> RsvpCounts:
    rows = conn.execute(
        "SELECT status, COUNT(*) AS n FROM rsvps WHERE session_id=? GROUP BY status",
        (session_id,),
    ).fetchall()
    counts = {r["status"]: r["n"] for r in rows}
    return RsvpCounts(
        yes=counts.get("yes", 0),
        no=counts.get("no", 0),
        maybe=counts.get("maybe", 0),
    )


def lock_in_player(conn: sqlite3.Connection, session_id: int, telegram_id: int) -> bool:
    """Force a yes-RSVP with locked_in=1. Returns False if the player isn't in the roster."""
    player = conn.execute(
        "SELECT 1 FROM players WHERE telegram_id=? AND active=1",
        (telegram_id,),
    ).fetchone()
    if player is None:
        return False
    conn.execute(
        """
        INSERT INTO rsvps (session_id, telegram_id, status, locked_in)
        VALUES (?, ?, 'yes', 1)
        ON CONFLICT(session_id, telegram_id) DO UPDATE SET
            status='yes',
            locked_in=1,
            created_at=CURRENT_TIMESTAMP
        """,
        (session_id, telegram_id),
    )
    conn.commit()
    return True


def is_player_on_roster(conn: sqlite3.Connection, telegram_id: int) -> bool:
    row = conn.execute(
        "SELECT 1 FROM players WHERE telegram_id=? AND active=1",
        (telegram_id,),
    ).fetchone()
    return row is not None


def format_rsvp_message(session_date: str, counts: RsvpCounts) -> str:
    return (
        f"📅 {session_date}\n\n✅ {counts.yes} · ❌ {counts.no} · 🤔 {counts.maybe}\n\nTap to RSVP:"
    )
