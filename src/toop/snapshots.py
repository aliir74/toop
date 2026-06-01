from __future__ import annotations

import json
import sqlite3
from dataclasses import asdict, dataclass, replace
from datetime import datetime

from toop.balance import TeamMetrics


@dataclass(frozen=True)
class Snapshot:
    session_id: int
    team_a: list[int]
    team_b: list[int]
    cut: list[int]
    metrics: TeamMetrics
    created_at: datetime


def save_snapshot(
    conn: sqlite3.Connection,
    session_id: int,
    team_a: list[int],
    team_b: list[int],
    cut: list[int],
    metrics: TeamMetrics,
) -> None:
    conn.execute(
        """
        INSERT INTO snapshots
            (session_id, team_a_json, team_b_json, cut_json, metrics_json, created_at)
        VALUES (?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
        ON CONFLICT(session_id) DO UPDATE SET
            team_a_json=excluded.team_a_json,
            team_b_json=excluded.team_b_json,
            cut_json=excluded.cut_json,
            metrics_json=excluded.metrics_json,
            created_at=CURRENT_TIMESTAMP
        """,
        (
            session_id,
            json.dumps(team_a),
            json.dumps(team_b),
            json.dumps(cut),
            json.dumps(asdict(metrics)),
        ),
    )
    conn.commit()


def get_snapshot(conn: sqlite3.Connection, session_id: int) -> Snapshot | None:
    row = conn.execute(
        "SELECT session_id, team_a_json, team_b_json, cut_json, metrics_json, created_at "
        "FROM snapshots WHERE session_id=?",
        (session_id,),
    ).fetchone()
    if row is None:
        return None
    metrics_data = json.loads(row["metrics_json"])
    return Snapshot(
        session_id=row["session_id"],
        team_a=json.loads(row["team_a_json"]),
        team_b=json.loads(row["team_b_json"]),
        cut=json.loads(row["cut_json"]),
        metrics=TeamMetrics(**metrics_data),
        created_at=datetime.fromisoformat(row["created_at"]),
    )


def update_teams(
    conn: sqlite3.Connection,
    session_id: int,
    team_a: list[int],
    team_b: list[int],
    metrics: TeamMetrics,
) -> None:
    """Persist a team update (e.g. after admin swap), preserving the cut list."""
    existing = get_snapshot(conn, session_id)
    cut = existing.cut if existing else []
    save_snapshot(conn, session_id, team_a, team_b, cut, metrics)


def write_attendance(conn: sqlite3.Connection, session_id: int) -> int:
    """Materialize the snapshot's team_a + team_b as attendance rows. Returns rows written."""
    snap = get_snapshot(conn, session_id)
    if snap is None:
        return 0
    attendees = snap.team_a + snap.team_b
    for pid in attendees:
        conn.execute(
            """
            INSERT INTO attendance (session_id, telegram_id, was_attendee)
            VALUES (?, ?, 1)
            ON CONFLICT(session_id, telegram_id) DO UPDATE SET was_attendee=1
            """,
            (session_id, pid),
        )
    conn.commit()
    return len(attendees)


__all__ = [
    "Snapshot",
    "save_snapshot",
    "get_snapshot",
    "update_teams",
    "write_attendance",
    "replace",
]
