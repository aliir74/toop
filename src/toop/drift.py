from __future__ import annotations

import sqlite3


def current_yes_set(conn: sqlite3.Connection, session_id: int) -> set[int]:
    """Telegram ids currently RSVP'd 'yes' for the session."""
    rows = conn.execute(
        "SELECT telegram_id FROM rsvps WHERE session_id=? AND status='yes'",
        (session_id,),
    ).fetchall()
    return {r["telegram_id"] for r in rows}


def compute_drift(snapshot_ids: set[int], current_yes: set[int]) -> tuple[list[int], list[int]]:
    """(added, removed) of the current yes-set versus the snapshot's attendees.

    Added = newly-yes since the snapshot; removed = dropped since. Both sorted
    for a deterministic signature.
    """
    added = sorted(current_yes - snapshot_ids)
    removed = sorted(snapshot_ids - current_yes)
    return added, removed


def drift_signature(added: list[int], removed: list[int]) -> str:
    """Stable key for an (added, removed) drift so identical states dedupe."""
    return f"+{added}|-{removed}"


def display_names(conn: sqlite3.Connection, ids: list[int]) -> list[str]:
    names: list[str] = []
    for pid in ids:
        row = conn.execute(
            "SELECT display_name FROM players WHERE telegram_id=?", (pid,)
        ).fetchone()
        names.append(row["display_name"] if row is not None else f"#{pid}")
    return names


def get_last_drift_signature(conn: sqlite3.Connection, session_id: int) -> str | None:
    row = conn.execute(
        "SELECT last_signature FROM drift_notices WHERE session_id=?",
        (session_id,),
    ).fetchone()
    return row["last_signature"] if row is not None else None


def set_drift_signature(conn: sqlite3.Connection, session_id: int, signature: str) -> None:
    conn.execute(
        """
        INSERT INTO drift_notices (session_id, last_signature)
        VALUES (?, ?)
        ON CONFLICT(session_id) DO UPDATE SET last_signature=excluded.last_signature
        """,
        (session_id, signature),
    )
    conn.commit()
