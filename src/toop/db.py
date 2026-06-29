from __future__ import annotations

import sqlite3
from pathlib import Path

# Overlapping-indicator map for the one-time pairwise→1-5 warm-start migration.
# Old axes were attack/defense/setting; only these map onto a new indicator.
# receive/serve/positioning have no old equivalent, so they start cold.
_AXIS_TO_INDICATOR: dict[str, str] = {
    "attack": "attack",
    "setting": "setting",
    "defense": "block",
}

# Old Bradley-Terry scores were mean-centered log-skills (~ -2..+2); the new
# normalized estimate lives on the same centered band, so a prior is just the
# clamped old score. Clamp guards against divergent all-win/all-loss outliers.
_PRIOR_CLAMP = 2.0

# Legacy pairwise tables retired by the 1-5 migration (Decision A = a+c).
_LEGACY_RATING_TABLES: tuple[str, ...] = (
    "vote_aggregates",
    "pending_prompts",
    "answered_prompts",
    "snoozes",
)


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
    ("players", "photo_file_id", "TEXT"),
    ("score_skips", "session_id", "INTEGER"),
)


def _column_names(conn: sqlite3.Connection, table: str) -> set[str]:
    rows = conn.execute(f"PRAGMA table_info({table})").fetchall()
    return {row[1] for row in rows}


def _table_exists(conn: sqlite3.Connection, table: str) -> bool:
    row = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)
    ).fetchone()
    return row is not None


def _clamp_prior(score: float) -> float:
    return max(-_PRIOR_CLAMP, min(_PRIOR_CLAMP, score))


def _seed_priors_from_bradley_terry(conn: sqlite3.Connection) -> list[tuple[int, str, float]]:
    """Read the legacy player_ratings (axis log-skills) and map them onto 1-5
    warm-start priors for the overlapping indicators (attack→attack,
    setting→setting, defense→block). Returns (telegram_id, indicator, score)
    rows to seed into the rebuilt player_ratings. receive/serve/positioning are
    left cold by design.
    """
    rows = conn.execute("SELECT telegram_id, axis, score FROM player_ratings").fetchall()
    priors: list[tuple[int, str, float]] = []
    for r in rows:
        indicator = _AXIS_TO_INDICATOR.get(r["axis"])
        if indicator is None:
            continue
        priors.append((r["telegram_id"], indicator, _clamp_prior(r["score"])))
    return priors


def _migrate_pairwise_to_scores(conn: sqlite3.Connection, schema_sql: str) -> None:
    """One-time migration off the retired pairwise model (Decision A = a+c).

    Order matters: read priors from the OLD player_ratings BEFORE rebuilding it,
    then drop the legacy pairwise tables. The synthetic prior is seeded directly
    into the player_ratings cache (NOT into `scores` — a synthetic rater would
    skew the per-rater normalization). refresh_ratings later overwrites a row
    once that player has real scores for the indicator, so priors wash out.
    """
    priors = _seed_priors_from_bradley_terry(conn)

    # Rebuild player_ratings on the new indicator enum. Drop + re-run schema.sql
    # (idempotent CREATE IF NOT EXISTS) recreates only the dropped table, reusing
    # the canonical DDL instead of duplicating it here.
    conn.execute("DROP TABLE player_ratings")
    conn.executescript(schema_sql)
    for telegram_id, indicator, score in priors:
        conn.execute(
            """
            INSERT OR IGNORE INTO player_ratings
                (telegram_id, indicator, score, vote_count, calibrated, computed_at)
            VALUES (?, ?, ?, 0, 0, CURRENT_TIMESTAMP)
            """,
            (telegram_id, indicator, score),
        )

    for table in _LEGACY_RATING_TABLES:
        conn.execute(f"DROP TABLE IF EXISTS {table}")


def _migrate(conn: sqlite3.Connection, schema_sql: str) -> None:
    """Add late-introduced columns, then run the one-time pairwise→1-5 migration
    on any DB still carrying the old axis-based player_ratings. Idempotent.
    """
    for table, column, definition in _MIGRATIONS:
        if column not in _column_names(conn, table):
            conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {definition}")

    # Legacy model is identified by the old 'axis' column on player_ratings.
    if _table_exists(conn, "player_ratings") and "axis" in _column_names(conn, "player_ratings"):
        _migrate_pairwise_to_scores(conn, schema_sql)


def init_db(conn: sqlite3.Connection) -> None:
    """Apply schema.sql idempotently, then run migrations."""
    schema_path = Path(__file__).parent / "schema.sql"
    if not schema_path.exists():
        return
    schema_sql = schema_path.read_text(encoding="utf-8")
    conn.executescript(schema_sql)
    _migrate(conn, schema_sql)
    conn.commit()
