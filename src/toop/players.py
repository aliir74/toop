from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import datetime


@dataclass(frozen=True)
class LinkResult:
    """Counts of what link_ghost_player migrated onto the real account."""

    vote_rows: int
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


def _relink_pair(
    ghost_id: int, real_id: int, player_a: int, player_b: int
) -> tuple[int, int, int] | None:
    """Remap a pair (one side is the ghost) onto the real account.

    Returns (other, new_a, new_b) with new_a < new_b, or None when the only other
    endpoint IS the real account (comparing the ghost to itself → void row).
    """
    other = player_b if player_a == ghost_id else player_a
    if other == real_id:
        return None
    new_a, new_b = (real_id, other) if real_id < other else (other, real_id)
    return other, new_a, new_b


def link_ghost_player(
    conn: sqlite3.Connection,
    ghost_id: int,
    real_id: int,
    username: str | None,
    display_name: str,
) -> LinkResult:
    """Merge a ghost player into a real Telegram account.

    Every row that referenced the ghost (votes, queued/answered prompts, ratings,
    RSVPs, attendance) is remapped onto real_id, re-normalizing the player_a<player_b
    pair ordering and merging counts on collision. Rows that would compare the real
    account to itself, or queue a prompt to a voter now inside the pair, are dropped.
    The ghost player row is deleted last so ON DELETE CASCADE clears any leftovers.
    """
    if conn.execute("SELECT 1 FROM players WHERE telegram_id=?", (real_id,)).fetchone() is None:
        add_player(conn, real_id, display_name, username)

    vote_rows = 0
    for row in conn.execute(
        "SELECT player_a, player_b, axis, a_wins, b_wins, dont_know FROM vote_aggregates "
        "WHERE player_a=? OR player_b=?",
        (ghost_id, ghost_id),
    ).fetchall():
        remap = _relink_pair(ghost_id, real_id, row["player_a"], row["player_b"])
        if remap is None:
            continue
        _other, new_a, new_b = remap
        ghost_wins = row["a_wins"] if row["player_a"] == ghost_id else row["b_wins"]
        other_wins = row["b_wins"] if row["player_a"] == ghost_id else row["a_wins"]
        # In the remapped pair, real_id carries the ghost's wins.
        if new_a == real_id:
            inc_a, inc_b = ghost_wins, other_wins
        else:
            inc_a, inc_b = other_wins, ghost_wins
        conn.execute(
            """
            INSERT INTO vote_aggregates (player_a, player_b, axis, a_wins, b_wins, dont_know)
            VALUES (?, ?, ?, ?, ?, ?)
            ON CONFLICT(player_a, player_b, axis) DO UPDATE SET
                a_wins = a_wins + excluded.a_wins,
                b_wins = b_wins + excluded.b_wins,
                dont_know = dont_know + excluded.dont_know,
                updated_at = CURRENT_TIMESTAMP
            """,
            (new_a, new_b, row["axis"], inc_a, inc_b, row["dont_know"]),
        )
        vote_rows += 1

    for row in conn.execute(
        "SELECT voter_id, player_a, player_b, axis, info_gain FROM pending_prompts "
        "WHERE player_a=? OR player_b=?",
        (ghost_id, ghost_id),
    ).fetchall():
        remap = _relink_pair(ghost_id, real_id, row["player_a"], row["player_b"])
        if remap is None or row["voter_id"] in (remap[1], remap[2]):
            continue
        conn.execute(
            "INSERT OR IGNORE INTO pending_prompts (voter_id, player_a, player_b, axis, info_gain) "
            "VALUES (?, ?, ?, ?, ?)",
            (row["voter_id"], remap[1], remap[2], row["axis"], row["info_gain"]),
        )

    for row in conn.execute(
        "SELECT voter_id, player_a, player_b, axis FROM answered_prompts "
        "WHERE player_a=? OR player_b=?",
        (ghost_id, ghost_id),
    ).fetchall():
        remap = _relink_pair(ghost_id, real_id, row["player_a"], row["player_b"])
        if remap is None or row["voter_id"] in (remap[1], remap[2]):
            continue
        conn.execute(
            "INSERT OR IGNORE INTO answered_prompts (voter_id, player_a, player_b, axis) "
            "VALUES (?, ?, ?, ?)",
            (row["voter_id"], remap[1], remap[2], row["axis"]),
        )

    def _count(table: str) -> int:
        return conn.execute(
            f"SELECT COUNT(*) AS n FROM {table} WHERE telegram_id=?", (ghost_id,)
        ).fetchone()["n"]

    ratings, rsvps, attendance = _count("player_ratings"), _count("rsvps"), _count("attendance")
    conn.execute(
        "INSERT OR IGNORE INTO player_ratings "
        "(telegram_id, axis, score, vote_count, calibrated, computed_at) "
        "SELECT ?, axis, score, vote_count, calibrated, computed_at FROM player_ratings "
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
    return LinkResult(vote_rows=vote_rows, ratings=ratings, rsvps=rsvps, attendance=attendance)


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
