from __future__ import annotations

import sqlite3
from dataclasses import dataclass

FAIRNESS_WINDOW_DAYS = 56  # ~8 weeks


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
    2. remaining contested seats go to least-recently-attended (fairness queue).

    Ties broken by telegram_id for determinism.
    """
    rsvps = conn.execute(
        "SELECT telegram_id, locked_in FROM rsvps "
        "WHERE session_id=? AND status='yes' ORDER BY telegram_id",
        (session_id,),
    ).fetchall()
    yes_ids = [r["telegram_id"] for r in rsvps]
    locked = [r["telegram_id"] for r in rsvps if r["locked_in"]]
    contested = [r["telegram_id"] for r in rsvps if not r["locked_in"]]

    if len(yes_ids) <= max_attendees:
        return AttendeeSelection(selected=yes_ids, cut=[])

    counts = dict.fromkeys(contested, 0)
    rows = conn.execute(
        f"""
        SELECT a.telegram_id, COUNT(*) AS n
        FROM attendance a
        JOIN sessions s ON s.id = a.session_id
        WHERE a.was_attendee = 1
          AND s.session_date >= DATE('now', '-{FAIRNESS_WINDOW_DAYS} days')
        GROUP BY a.telegram_id
        """
    ).fetchall()
    for r in rows:
        if r["telegram_id"] in counts:
            counts[r["telegram_id"]] = r["n"]

    contested.sort(key=lambda pid: (counts[pid], pid))

    remaining_slots = max(max_attendees - len(locked), 0)
    selected = locked + contested[:remaining_slots]
    cut = contested[remaining_slots:]
    return AttendeeSelection(selected=selected, cut=cut)
