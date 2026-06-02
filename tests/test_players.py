from __future__ import annotations

import sqlite3

from toop.players import (
    add_player,
    get_player_by_username,
    list_active_players,
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
