from __future__ import annotations

import sqlite3
from pathlib import Path


def get_connection(database_path: str | Path) -> sqlite3.Connection:
    """Open a SQLite connection with WAL + foreign keys enabled."""
    path = Path(database_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path))
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    conn.row_factory = sqlite3.Row
    return conn


# (table, column, column_definition) tuples added after the initial schema.
# CREATE TABLE IF NOT EXISTS won't add columns to a pre-existing table, so each
# is applied via ALTER TABLE ADD COLUMN only when PRAGMA shows it missing.
# Defaults must be constant (SQLite rejects non-constant ADD COLUMN defaults).
_MIGRATIONS: tuple[tuple[str, str, str], ...] = (
    ("players", "in_pool", "INTEGER NOT NULL DEFAULT 1"),
    ("players", "pool_paused_until", "TIMESTAMP"),
    ("players", "is_ghost", "INTEGER NOT NULL DEFAULT 0"),
    ("vote_aggregates", "dont_know", "INTEGER NOT NULL DEFAULT 0"),
)


def _column_names(conn: sqlite3.Connection, table: str) -> set[str]:
    rows = conn.execute(f"PRAGMA table_info({table})").fetchall()
    return {row[1] for row in rows}


def _migrate(conn: sqlite3.Connection) -> None:
    """Add late-introduced columns to existing tables. Idempotent."""
    for table, column, definition in _MIGRATIONS:
        if column not in _column_names(conn, table):
            conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {definition}")


def init_db(conn: sqlite3.Connection) -> None:
    """Apply schema.sql idempotently, then run column migrations."""
    schema_path = Path(__file__).parent / "schema.sql"
    if not schema_path.exists():
        return
    conn.executescript(schema_path.read_text(encoding="utf-8"))
    _migrate(conn)
    conn.commit()
