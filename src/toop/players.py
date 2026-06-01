from __future__ import annotations

import sqlite3
from dataclasses import dataclass


@dataclass(frozen=True)
class Player:
    telegram_id: int
    username: str | None
    display_name: str
    is_calibrating: bool
    active: bool


def add_player(
    conn: sqlite3.Connection,
    telegram_id: int,
    display_name: str,
    username: str | None = None,
) -> Player:
    """Insert or revive a player. Idempotent on telegram_id."""
    normalized = username.lstrip("@").lower() if username else None
    conn.execute(
        """
        INSERT INTO players (telegram_id, username, display_name, active, is_calibrating)
        VALUES (?, ?, ?, 1, 1)
        ON CONFLICT(telegram_id) DO UPDATE SET
            active=1,
            display_name=excluded.display_name,
            username=excluded.username
        """,
        (telegram_id, normalized, display_name),
    )
    conn.commit()
    return _row_to_player(_fetch_one(conn, telegram_id))


def soft_remove_player(conn: sqlite3.Connection, telegram_id: int) -> bool:
    """Set active=0. Returns True if a player row was changed."""
    cur = conn.execute(
        "UPDATE players SET active=0 WHERE telegram_id=? AND active=1",
        (telegram_id,),
    )
    conn.commit()
    return cur.rowcount > 0


def list_active_players(conn: sqlite3.Connection) -> list[Player]:
    rows = conn.execute(
        "SELECT telegram_id, username, display_name, is_calibrating, active "
        "FROM players WHERE active=1 ORDER BY display_name COLLATE NOCASE"
    ).fetchall()
    return [_row_to_player(r) for r in rows]


def get_player_by_username(conn: sqlite3.Connection, username: str) -> Player | None:
    row = conn.execute(
        "SELECT telegram_id, username, display_name, is_calibrating, active "
        "FROM players WHERE username=? AND active=1",
        (username.lstrip("@").lower(),),
    ).fetchone()
    return _row_to_player(row) if row else None


def _fetch_one(conn: sqlite3.Connection, telegram_id: int) -> sqlite3.Row:
    row = conn.execute(
        "SELECT telegram_id, username, display_name, is_calibrating, active "
        "FROM players WHERE telegram_id=?",
        (telegram_id,),
    ).fetchone()
    if row is None:
        raise LookupError(f"Player {telegram_id} not found after insert")
    return row


def _row_to_player(row: sqlite3.Row) -> Player:
    return Player(
        telegram_id=row["telegram_id"],
        username=row["username"],
        display_name=row["display_name"],
        is_calibrating=bool(row["is_calibrating"]),
        active=bool(row["active"]),
    )
