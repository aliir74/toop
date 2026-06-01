from __future__ import annotations

import sqlite3
from dataclasses import dataclass


@dataclass(frozen=True)
class Contact:
    telegram_id: int
    username: str | None
    display_name: str | None
    first_seen_at: str


def upsert_contact(
    conn: sqlite3.Connection,
    telegram_id: int,
    username: str | None = None,
    display_name: str | None = None,
) -> None:
    """Record (or refresh) someone who has DM'd the bot.

    Standalone presence log — NEVER joined to vote data. On conflict, bumps
    last_seen_at and refreshes username/display_name (handles renames).
    """
    normalized = username.lstrip("@").lower() if username else None
    conn.execute(
        """
        INSERT INTO contacts (telegram_id, username, display_name)
        VALUES (?, ?, ?)
        ON CONFLICT(telegram_id) DO UPDATE SET
            username=excluded.username,
            display_name=excluded.display_name,
            last_seen_at=CURRENT_TIMESTAMP
        """,
        (telegram_id, normalized, display_name),
    )
    conn.commit()


def list_contacts(conn: sqlite3.Connection) -> list[Contact]:
    """All known contacts, oldest first."""
    rows = conn.execute(
        "SELECT telegram_id, username, display_name, first_seen_at "
        "FROM contacts ORDER BY first_seen_at ASC, telegram_id ASC"
    ).fetchall()
    return [
        Contact(
            telegram_id=r["telegram_id"],
            username=r["username"],
            display_name=r["display_name"],
            first_seen_at=r["first_seen_at"],
        )
        for r in rows
    ]
