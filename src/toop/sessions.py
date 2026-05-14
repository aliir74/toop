from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import date, datetime, timedelta

WEEKDAY_INDEX = {
    "monday": 0,
    "tuesday": 1,
    "wednesday": 2,
    "thursday": 3,
    "friday": 4,
    "saturday": 5,
    "sunday": 6,
}


class SessionStateError(Exception):
    """Raised when a session lifecycle transition is invalid."""


@dataclass(frozen=True)
class Session:
    id: int
    session_date: date
    snapshot_at: datetime | None
    status: str


def next_weekday(target_weekday: str, today: date | None = None) -> date:
    """Return the next date matching target_weekday. If today is that weekday, return today + 7."""
    today = today or date.today()
    target = WEEKDAY_INDEX[target_weekday.lower()]
    days_ahead = (target - today.weekday()) % 7
    if days_ahead == 0:
        days_ahead = 7
    return today + timedelta(days=days_ahead)


def open_session(
    conn: sqlite3.Connection,
    session_date: date,
) -> Session:
    """Open a new session. Raises SessionStateError if one is already open."""
    existing = conn.execute(
        "SELECT id FROM sessions WHERE status IN ('open', 'snapshotted', 'published') LIMIT 1"
    ).fetchone()
    if existing is not None:
        raise SessionStateError(
            f"Session #{existing['id']} is still active. Close it first with /close_session."
        )
    cur = conn.execute(
        "INSERT INTO sessions (session_date, status) VALUES (?, 'open')",
        (session_date.isoformat(),),
    )
    conn.commit()
    return _fetch_session(conn, cur.lastrowid)


def close_session(conn: sqlite3.Connection) -> Session:
    """Mark the active session done."""
    row = conn.execute(
        "SELECT id FROM sessions WHERE status IN ('open', 'snapshotted', 'published') "
        "ORDER BY id DESC LIMIT 1"
    ).fetchone()
    if row is None:
        raise SessionStateError("No active session to close.")
    conn.execute("UPDATE sessions SET status='done' WHERE id=?", (row["id"],))
    conn.commit()
    return _fetch_session(conn, row["id"])


def set_session_status(
    conn: sqlite3.Connection, session_id: int, status: str, snapshot_at: bool = False
) -> Session:
    """Update a session's status. Optionally stamps snapshot_at = now."""
    if status not in ("open", "snapshotted", "published", "done"):
        raise ValueError(f"invalid status {status!r}")
    if snapshot_at:
        conn.execute(
            "UPDATE sessions SET status=?, snapshot_at=CURRENT_TIMESTAMP WHERE id=?",
            (status, session_id),
        )
    else:
        conn.execute("UPDATE sessions SET status=? WHERE id=?", (status, session_id))
    conn.commit()
    return _fetch_session(conn, session_id)


def list_recent_sessions(conn: sqlite3.Connection, limit: int = 10) -> list[Session]:
    rows = conn.execute(
        "SELECT id, session_date, snapshot_at, status FROM sessions "
        "ORDER BY id DESC LIMIT ?",
        (limit,),
    ).fetchall()
    return [_row_to_session(r) for r in rows]


def get_active_session(conn: sqlite3.Connection) -> Session | None:
    row = conn.execute(
        "SELECT id, session_date, snapshot_at, status FROM sessions "
        "WHERE status IN ('open', 'snapshotted', 'published') "
        "ORDER BY id DESC LIMIT 1"
    ).fetchone()
    return _row_to_session(row) if row else None


def _fetch_session(conn: sqlite3.Connection, session_id: int) -> Session:
    row = conn.execute(
        "SELECT id, session_date, snapshot_at, status FROM sessions WHERE id=?",
        (session_id,),
    ).fetchone()
    return _row_to_session(row)


def _row_to_session(row: sqlite3.Row) -> Session:
    return Session(
        id=row["id"],
        session_date=date.fromisoformat(row["session_date"]),
        snapshot_at=datetime.fromisoformat(row["snapshot_at"]) if row["snapshot_at"] else None,
        status=row["status"],
    )
