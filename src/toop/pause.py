from __future__ import annotations

import sqlite3
from datetime import datetime

# bot_state key holding the instant (ISO-8601, UTC) until which the weekly
# schedule is paused. While it's in the future, the attendance-poll job and the
# auto-snapshot job skip, so no session is created in that window.
_EVENTS_PAUSED_UNTIL = "events_paused_until"


def pause_events_until(conn: sqlite3.Connection, until: datetime) -> None:
    """Pause the weekly schedule until ``until`` (a tz-aware UTC datetime).

    Upserts so a second /pause_events just moves the end of the window.
    """
    conn.execute(
        """
        INSERT INTO bot_state (key, value, updated_at)
        VALUES (?, ?, CURRENT_TIMESTAMP)
        ON CONFLICT(key) DO UPDATE SET value=excluded.value, updated_at=CURRENT_TIMESTAMP
        """,
        (_EVENTS_PAUSED_UNTIL, until.isoformat()),
    )
    conn.commit()


def clear_events_pause(conn: sqlite3.Connection) -> None:
    """Lift any active schedule pause (no-op when none is set)."""
    conn.execute("DELETE FROM bot_state WHERE key=?", (_EVENTS_PAUSED_UNTIL,))
    conn.commit()


def events_paused_until(conn: sqlite3.Connection) -> datetime | None:
    """Return the stored pause-until instant, or None when nothing is stored.

    Returns the raw stored value even if it's already in the past; callers use
    :func:`events_are_paused` to decide whether the pause is still in effect.
    """
    row = conn.execute(
        "SELECT value FROM bot_state WHERE key=?", (_EVENTS_PAUSED_UNTIL,)
    ).fetchone()
    if row is None:
        return None
    return datetime.fromisoformat(row["value"])


def events_are_paused(conn: sqlite3.Connection, now: datetime) -> bool:
    """True when a pause is set and its end is still in the future at ``now``."""
    until = events_paused_until(conn)
    return until is not None and until > now
