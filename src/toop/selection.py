from __future__ import annotations

import sqlite3
from dataclasses import dataclass


@dataclass(frozen=True)
class AttendeeSelection:
    selected: list[int]
    cut: list[int]


def select_attendees(
    conn: sqlite3.Connection,
    session_id: int,
    max_attendees: int,
) -> AttendeeSelection:
    """Pick up to `max_attendees` players from this session's yes-RSVPs.

    Order:
    1. locked_in players (always selected, even past cap — admin override).
    2. remaining seats go first-come by vote time; everyone past the cap is the
       waitlist (`cut`), in the order they'll be promoted. A yes that's changed
       and re-cast moves to the back, since upsert_rsvp re-stamps created_at.

    Ties (same-second votes) broken by telegram_id for determinism.
    """
    rsvps = conn.execute(
        "SELECT telegram_id, locked_in FROM rsvps "
        "WHERE session_id=? AND status='yes' ORDER BY created_at, telegram_id",
        (session_id,),
    ).fetchall()
    locked = [r["telegram_id"] for r in rsvps if r["locked_in"]]
    contested = [r["telegram_id"] for r in rsvps if not r["locked_in"]]
    remaining_slots = max(max_attendees - len(locked), 0)
    return AttendeeSelection(
        selected=locked + contested[:remaining_slots],
        cut=contested[remaining_slots:],
    )
