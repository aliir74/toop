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


def get_contact(conn: sqlite3.Connection, telegram_id: int) -> Contact | None:
    """Fetch a single contact by telegram_id, or None if they haven't DM'd."""
    row = conn.execute(
        "SELECT telegram_id, username, display_name, first_seen_at "
        "FROM contacts WHERE telegram_id=?",
        (telegram_id,),
    ).fetchone()
    if row is None:
        return None
    return Contact(
        telegram_id=row["telegram_id"],
        username=row["username"],
        display_name=row["display_name"],
        first_seen_at=row["first_seen_at"],
    )


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


def list_addable_contacts(conn: sqlite3.Connection) -> list[Contact]:
    """Contacts who have DM'd the bot but aren't on the players roster yet.

    These are exactly the people the button-driven /add_player and /link_player
    flows can offer. Ghosts have negative ids and no contact row, so the
    NOT IN players subquery excludes them for free. Oldest first.
    """
    rows = conn.execute(
        "SELECT telegram_id, username, display_name, first_seen_at FROM contacts "
        "WHERE telegram_id NOT IN (SELECT telegram_id FROM players) "
        "ORDER BY first_seen_at ASC, telegram_id ASC"
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
