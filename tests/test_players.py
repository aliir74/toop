from __future__ import annotations

import sqlite3
from datetime import UTC, datetime, timedelta

from toop.players import (
    add_ghost_player,
    add_player,
    disable_player_pool,
    dont_know_stats,
    enable_player_pool,
    get_player_by_username,
    list_active_players,
    pause_player_pool,
    rename_player,
    soft_remove_player,
)


def test_add_player_creates_active_calibrating(conn: sqlite3.Connection) -> None:
    p = add_player(conn, telegram_id=1, display_name="Alice", username="@Alice")
    assert p.telegram_id == 1
    assert p.display_name == "Alice"
    assert p.username == "alice"
    assert p.active is True
    assert p.is_calibrating is True


def test_player_exposes_pool_and_ghost_defaults(conn: sqlite3.Connection) -> None:
    add_player(conn, telegram_id=1, display_name="Alice", username="alice")
    p = list_active_players(conn)[0]
    assert p.in_pool is True
    assert p.pool_paused_until is None
    assert p.is_ghost is False
    by_username = get_player_by_username(conn, "alice")
    assert by_username is not None
    assert by_username.in_pool is True
    assert by_username.is_ghost is False


def test_add_player_is_idempotent_and_revives(conn: sqlite3.Connection) -> None:
    add_player(conn, 1, "Alice", "alice")
    soft_remove_player(conn, 1)
    revived = add_player(conn, 1, "Alice Updated", "alice")
    assert revived.active is True
    assert revived.display_name == "Alice Updated"


def test_soft_remove_returns_false_when_not_active(conn: sqlite3.Connection) -> None:
    add_player(conn, 1, "Alice", "alice")
    assert soft_remove_player(conn, 1) is True
    assert soft_remove_player(conn, 1) is False
    assert soft_remove_player(conn, 999) is False


def test_list_active_excludes_removed_and_sorts_by_name(conn: sqlite3.Connection) -> None:
    add_player(conn, 1, "charlie", "charlie")
    add_player(conn, 2, "alice", "alice")
    add_player(conn, 3, "bob", "bob")
    soft_remove_player(conn, 3)
    names = [p.display_name for p in list_active_players(conn)]
    assert names == ["alice", "charlie"]


def test_get_by_username_normalizes(conn: sqlite3.Connection) -> None:
    add_player(conn, 1, "Alice", "Alice")
    found = get_player_by_username(conn, "@ALICE")
    assert found is not None
    assert found.telegram_id == 1


def test_rename_player_changes_only_display_name(conn: sqlite3.Connection) -> None:
    add_player(conn, 1, "H P", "hp")
    old = rename_player(conn, 1, "Hamed Pour")
    assert old == "H P"
    p = list_active_players(conn)[0]
    assert p.display_name == "Hamed Pour"
    assert p.username == "hp"  # username untouched
    assert p.telegram_id == 1


def test_rename_player_unknown_id_returns_none(conn: sqlite3.Connection) -> None:
    assert rename_player(conn, 999, "Nobody") is None


def test_rename_player_inactive_returns_none(conn: sqlite3.Connection) -> None:
    add_player(conn, 1, "Alice", "alice")
    soft_remove_player(conn, 1)
    assert rename_player(conn, 1, "Alice Renamed") is None


def _pool_row(conn: sqlite3.Connection, telegram_id: int) -> sqlite3.Row:
    return conn.execute(
        "SELECT in_pool, pool_paused_until FROM players WHERE telegram_id=?",
        (telegram_id,),
    ).fetchone()


def test_pause_player_pool_sets_timestamp(conn: sqlite3.Connection) -> None:
    add_player(conn, 1, "Alice", "alice")
    until = datetime.now(UTC) + timedelta(days=14)
    assert pause_player_pool(conn, 1, until) is True
    row = _pool_row(conn, 1)
    assert row["in_pool"] == 1  # pause is independent of the manual toggle
    assert row["pool_paused_until"] is not None
    assert pause_player_pool(conn, 999, until) is False


def test_disable_player_pool_clears_in_pool(conn: sqlite3.Connection) -> None:
    add_player(conn, 1, "Alice", "alice")
    assert disable_player_pool(conn, 1) is True
    assert _pool_row(conn, 1)["in_pool"] == 0
    assert disable_player_pool(conn, 999) is False


def test_enable_player_pool_clears_both(conn: sqlite3.Connection) -> None:
    add_player(conn, 1, "Alice", "alice")
    pause_player_pool(conn, 1, datetime.now(UTC) + timedelta(days=7))
    disable_player_pool(conn, 1)
    assert enable_player_pool(conn, 1) is True
    row = _pool_row(conn, 1)
    assert row["in_pool"] == 1
    assert row["pool_paused_until"] is None
    assert enable_player_pool(conn, 999) is False


def _agg(conn: sqlite3.Connection, a: int, b: int, axis: str, aw: int, bw: int, dk: int) -> None:
    conn.execute(
        "INSERT INTO vote_aggregates (player_a, player_b, axis, a_wins, b_wins, dont_know) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        (a, b, axis, aw, bw, dk),
    )
    conn.commit()


def test_dont_know_stats_aggregates_per_player(conn: sqlite3.Connection) -> None:
    for tid in (1, 2, 3):
        add_player(conn, tid, f"P{tid}", f"p{tid}")
    add_player(conn, 4, "P4", "p4")
    soft_remove_player(conn, 4)  # inactive — must be excluded from results
    _agg(conn, 1, 2, "attack", 2, 1, 3)
    _agg(conn, 1, 3, "attack", 0, 0, 1)
    _agg(conn, 2, 3, "defense", 1, 0, 0)

    stats = dont_know_stats(conn)
    by_id = {s.telegram_id: s for s in stats}
    assert set(by_id) == {1, 2, 3}  # player 4 excluded

    # Player 1: pairs (1,2)+(1,3) → dk 3+1=4, total 6+1=7.
    assert by_id[1].dk_count == 4
    assert by_id[1].total == 7
    assert by_id[1].dk_rate == 4 / 7
    # Player 3: pairs (1,3)+(2,3) → dk 1+0=1, total 1+1=2.
    assert by_id[3].dk_count == 1
    assert by_id[3].total == 2

    # Sorted by dk_rate descending.
    rates = [s.dk_rate for s in stats]
    assert rates == sorted(rates, reverse=True)


def test_dont_know_stats_zero_total_is_zero_rate(conn: sqlite3.Connection) -> None:
    add_player(conn, 1, "Lonely", "lonely")
    stats = dont_know_stats(conn)
    assert stats[0].dk_count == 0
    assert stats[0].total == 0
    assert stats[0].dk_rate == 0.0


def test_add_ghost_player_mints_negative_id(conn: sqlite3.Connection) -> None:
    add_player(conn, 1, "Real", "real")
    ghost = add_ghost_player(conn, "Late Joiner")
    assert ghost.telegram_id < 0
    assert ghost.username is None
    assert ghost.display_name == "Late Joiner"
    assert ghost.is_ghost is True
    assert ghost.in_pool is True
    assert ghost.is_calibrating is True
    assert ghost.active is True


def test_add_ghost_players_get_distinct_descending_ids(conn: sqlite3.Connection) -> None:
    first = add_ghost_player(conn, "Ghost A")
    second = add_ghost_player(conn, "Ghost B")
    assert second.telegram_id < first.telegram_id < 0


def test_ghost_appears_in_active_roster(conn: sqlite3.Connection) -> None:
    add_ghost_player(conn, "Ghost")
    roster = list_active_players(conn)
    assert any(p.is_ghost for p in roster)
