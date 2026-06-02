from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import datetime


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


# Shared column list so every Player query stays in sync with the dataclass.
_PLAYER_COLS = (
    "telegram_id, username, display_name, is_calibrating, active, "
    "in_pool, pool_paused_until, is_ghost"
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


def list_active_players(conn: sqlite3.Connection) -> list[Player]:
    rows = conn.execute(
        f"SELECT {_PLAYER_COLS} "
        "FROM players WHERE active=1 ORDER BY display_name COLLATE NOCASE"
    ).fetchall()
    return [_row_to_player(r) for r in rows]


def get_player_by_username(conn: sqlite3.Connection, username: str) -> Player | None:
    row = conn.execute(
        f"SELECT {_PLAYER_COLS} FROM players WHERE username=? AND active=1",
        (username.lstrip("@").lower(),),
    ).fetchone()
    return _row_to_player(row) if row else None


def dont_know_stats(conn: sqlite3.Connection) -> list[DontKnowStat]:
    """Per-active-player "don't know" signal, summed across every pair the player
    appears in. dk_rate = dk_count / total prompts answered on those pairs (0.0
    when none). Sorted by dk_rate descending, then name — the head of the list is
    the player the group can least confidently rate.
    """
    rows = conn.execute(
        """
        SELECT p.telegram_id, p.display_name,
               COALESCE(SUM(va.dont_know), 0) AS dk_count,
               COALESCE(SUM(va.a_wins + va.b_wins + va.dont_know), 0) AS total
        FROM players p
        LEFT JOIN vote_aggregates va
            ON va.player_a = p.telegram_id OR va.player_b = p.telegram_id
        WHERE p.active = 1
        GROUP BY p.telegram_id, p.display_name
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
    )
