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


def init_db(conn: sqlite3.Connection) -> None:
    """Apply schema.sql idempotently."""
    schema_path = Path(__file__).parent / "schema.sql"
    if not schema_path.exists():
        return
    conn.executescript(schema_path.read_text(encoding="utf-8"))
    conn.commit()
