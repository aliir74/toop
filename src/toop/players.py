from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import datetime


@dataclass(frozen=True)
class LinkResult:
    """Counts of what link_ghost_player migrated onto the real account."""

    score_rows: int
    ratings: int
    rsvps: int
    attendance: int


@dataclass(frozen=True)
class DontKnowStat:
    telegram_id: int
    display_name: str
    dk_count: int
    total: int
    dk_rate: float


@dataclass(frozen=True)
class Player:
    telegram_id: int
    username: str | None
    display_name: str
    is_calibrating: bool
    active: bool
    in_pool: bool = True
    pool_paused_until: str | None = None
    is_ghost: bool = False
    photo_file_id: str | None = None


# Shared column list so every Player query stays in sync with the dataclass.
_PLAYER_COLS = (
    "telegram_id, username, display_name, is_calibrating, active, "
    "in_pool, pool_paused_until, is_ghost, photo_file_id"
)


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


def add_ghost_player(conn: sqlite3.Connection, display_name: str) -> Player:
    """Add an accountless "ghost" player others can vote on before they join.

    Real Telegram ids are positive, so a ghost gets the next free NEGATIVE id —
    a collision-free namespace that still satisfies the INTEGER PK and every FK.
    A ghost is rateable (is_ghost=1, in_pool=1) but never receives prompts and is
    never selected as a voter. Link it to a real account later via link_ghost_player.
    """
    lowest = conn.execute("SELECT COALESCE(MIN(telegram_id), 0) AS m FROM players").fetchone()["m"]
    ghost_id = min(lowest, 0) - 1
    conn.execute(
        """
        INSERT INTO players (telegram_id, username, display_name, active, is_calibrating, is_ghost)
        VALUES (?, NULL, ?, 1, 1, 1)
        """,
        (ghost_id, display_name),
    )
    conn.commit()
    return _row_to_player(_fetch_one(conn, ghost_id))


def _remap_endpoint(ghost_id: int, real_id: int, value: int) -> int:
    return real_id if value == ghost_id else value


def link_ghost_player(
    conn: sqlite3.Connection,
    ghost_id: int,
    real_id: int,
    username: str | None,
    display_name: str,
) -> LinkResult:
    """Merge a ghost player into a real Telegram account.

    Every score and skip that referenced the ghost (as voter or as scored player)
    is remapped onto real_id. Rows that would make the real account score itself
    are dropped. On a (voter, player, indicator) collision the most recent score
    wins (by updated_at). RSVPs, attendance, and ratings remap by telegram_id.
    The ghost player row is deleted last so ON DELETE CASCADE clears leftovers.
    """
    if conn.execute("SELECT 1 FROM players WHERE telegram_id=?", (real_id,)).fetchone() is None:
        add_player(conn, real_id, display_name, username)

    score_rows = 0
    for row in conn.execute(
        "SELECT voter_id, player_id, indicator, score, updated_at FROM scores "
        "WHERE voter_id=? OR player_id=?",
        (ghost_id, ghost_id),
    ).fetchall():
        new_voter = _remap_endpoint(ghost_id, real_id, row["voter_id"])
        new_player = _remap_endpoint(ghost_id, real_id, row["player_id"])
        if new_voter == new_player:
            continue
        conn.execute(
            """
            INSERT INTO scores (voter_id, player_id, indicator, score, updated_at)
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(voter_id, player_id, indicator) DO UPDATE SET
                score = excluded.score,
                updated_at = excluded.updated_at
            WHERE excluded.updated_at > scores.updated_at
            """,
            (new_voter, new_player, row["indicator"], row["score"], row["updated_at"]),
        )
        score_rows += 1

    for row in conn.execute(
        "SELECT voter_id, player_id, indicator FROM score_skips WHERE voter_id=? OR player_id=?",
        (ghost_id, ghost_id),
    ).fetchall():
        new_voter = _remap_endpoint(ghost_id, real_id, row["voter_id"])
        new_player = _remap_endpoint(ghost_id, real_id, row["player_id"])
        if new_voter == new_player:
            continue
        conn.execute(
            "INSERT OR IGNORE INTO score_skips (voter_id, player_id, indicator) VALUES (?, ?, ?)",
            (new_voter, new_player, row["indicator"]),
        )

    def _count(table: str) -> int:
        return conn.execute(
            f"SELECT COUNT(*) AS n FROM {table} WHERE telegram_id=?", (ghost_id,)
        ).fetchone()["n"]

    ratings, rsvps, attendance = _count("player_ratings"), _count("rsvps"), _count("attendance")
    conn.execute(
        "INSERT OR IGNORE INTO player_ratings "
        "(telegram_id, indicator, score, vote_count, calibrated, computed_at) "
        "SELECT ?, indicator, score, vote_count, calibrated, computed_at FROM player_ratings "
        "WHERE telegram_id=?",
        (real_id, ghost_id),
    )
    conn.execute(
        "INSERT OR IGNORE INTO rsvps (session_id, telegram_id, status, locked_in, created_at) "
        "SELECT session_id, ?, status, locked_in, created_at FROM rsvps WHERE telegram_id=?",
        (real_id, ghost_id),
    )
    conn.execute(
        "INSERT OR IGNORE INTO attendance (session_id, telegram_id, was_attendee) "
        "SELECT session_id, ?, was_attendee FROM attendance WHERE telegram_id=?",
        (real_id, ghost_id),
    )

    # Delete the ghost player last: ON DELETE CASCADE clears its remaining child rows.
    conn.execute("DELETE FROM players WHERE telegram_id=?", (ghost_id,))
    conn.execute("UPDATE players SET is_ghost=0, active=1 WHERE telegram_id=?", (real_id,))
    conn.commit()
    return LinkResult(score_rows=score_rows, ratings=ratings, rsvps=rsvps, attendance=attendance)


def soft_remove_player(conn: sqlite3.Connection, telegram_id: int) -> bool:
    """Set active=0. Returns True if a player row was changed."""
    cur = conn.execute(
        "UPDATE players SET active=0 WHERE telegram_id=? AND active=1",
        (telegram_id,),
    )
    conn.commit()
    return cur.rowcount > 0


def pause_player_pool(conn: sqlite3.Connection, telegram_id: int, until: datetime) -> bool:
    """Temporarily pull a player from the rating pool until `until`. Others stop
    being asked to rate them; the player can still vote. Returns True if changed.
    """
    until_text = until.strftime("%Y-%m-%d %H:%M:%S")
    cur = conn.execute(
        "UPDATE players SET pool_paused_until=? WHERE telegram_id=? AND active=1",
        (until_text, telegram_id),
    )
    conn.commit()
    return cur.rowcount > 0


def disable_player_pool(conn: sqlite3.Connection, telegram_id: int) -> bool:
    """Manually pull a player from the rating pool indefinitely (in_pool=0)."""
    cur = conn.execute(
        "UPDATE players SET in_pool=0 WHERE telegram_id=? AND active=1",
        (telegram_id,),
    )
    conn.commit()
    return cur.rowcount > 0


def enable_player_pool(conn: sqlite3.Connection, telegram_id: int) -> bool:
    """Restore a player to the rating pool, clearing any manual disable AND any
    timed pause."""
    cur = conn.execute(
        "UPDATE players SET in_pool=1, pool_paused_until=NULL WHERE telegram_id=? AND active=1",
        (telegram_id,),
    )
    conn.commit()
    return cur.rowcount > 0


def rename_player(conn: sqlite3.Connection, telegram_id: int, new_display_name: str) -> str | None:
    """Update an active player's display_name. Returns the old name, or None.

    None means no active player with that telegram_id exists (nothing changed).
    Touches display_name only — never username, votes, ratings, or telegram_id.
    """
    row = conn.execute(
        "SELECT display_name FROM players WHERE telegram_id=? AND active=1",
        (telegram_id,),
    ).fetchone()
    if row is None:
        return None
    old_name = row["display_name"]
    conn.execute(
        "UPDATE players SET display_name=? WHERE telegram_id=? AND active=1",
        (new_display_name, telegram_id),
    )
    conn.commit()
    return old_name


def set_player_photo(
    conn: sqlite3.Connection, telegram_id: int, photo_file_id: str | None
) -> str | None:
    """Set (or clear, when photo_file_id is None) an active player's photo_file_id.

    Returns the player's display_name on success, or None when no active player
    has that telegram_id (nothing changed). active=1 gating mirrors rename_player
    and still covers ghosts (they are active=1). Touches photo_file_id only.
    """
    row = conn.execute(
        "SELECT display_name FROM players WHERE telegram_id=? AND active=1",
        (telegram_id,),
    ).fetchone()
    if row is None:
        return None
    conn.execute(
        "UPDATE players SET photo_file_id=? WHERE telegram_id=? AND active=1",
        (photo_file_id, telegram_id),
    )
    conn.commit()
    return row["display_name"]


def list_active_players(conn: sqlite3.Connection) -> list[Player]:
    rows = conn.execute(
        f"SELECT {_PLAYER_COLS} FROM players WHERE active=1 ORDER BY display_name COLLATE NOCASE"
    ).fetchall()
    return [_row_to_player(r) for r in rows]


def get_player_by_username(conn: sqlite3.Connection, username: str) -> Player | None:
    row = conn.execute(
        f"SELECT {_PLAYER_COLS} FROM players WHERE username=? AND active=1",
        (username.lstrip("@").lower(),),
    ).fetchone()
    return _row_to_player(row) if row else None


def dont_know_stats(conn: sqlite3.Connection) -> list[DontKnowStat]:
    """Per-active-player "don't know" signal: how often voters SKIPPED rating this
    player (score_skips on player_id) vs all attempts (skips + scores received).
    dk_rate = dk_count / total (0.0 when none). Sorted by dk_rate descending,
    then name — the head of the list is the player the group can least rate.
    """
    rows = conn.execute(
        """
        SELECT p.telegram_id, p.display_name,
               (SELECT COUNT(*) FROM score_skips sk WHERE sk.player_id = p.telegram_id)
                   AS dk_count,
               (SELECT COUNT(*) FROM score_skips sk WHERE sk.player_id = p.telegram_id)
                 + (SELECT COUNT(*) FROM scores s WHERE s.player_id = p.telegram_id)
                   AS total
        FROM players p
        WHERE p.active = 1
        """
    ).fetchall()
    stats = [
        DontKnowStat(
            telegram_id=r["telegram_id"],
            display_name=r["display_name"],
            dk_count=r["dk_count"],
            total=r["total"],
            dk_rate=(r["dk_count"] / r["total"]) if r["total"] else 0.0,
        )
        for r in rows
    ]
    stats.sort(key=lambda s: (-s.dk_rate, s.display_name.lower()))
    return stats


def _fetch_one(conn: sqlite3.Connection, telegram_id: int) -> sqlite3.Row:
    row = conn.execute(
        f"SELECT {_PLAYER_COLS} FROM players WHERE telegram_id=?",
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
        in_pool=bool(row["in_pool"]),
        pool_paused_until=row["pool_paused_until"],
        is_ghost=bool(row["is_ghost"]),
        photo_file_id=row["photo_file_id"],
    )
