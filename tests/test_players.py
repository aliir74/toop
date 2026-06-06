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
    link_ghost_player,
    list_active_players,
    pause_player_pool,
    rename_player,
    set_player_photo,
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
    assert p.username == "hp"
    assert p.telegram_id == 1


def test_rename_player_unknown_id_returns_none(conn: sqlite3.Connection) -> None:
    assert rename_player(conn, 999, "Nobody") is None


def test_rename_player_inactive_returns_none(conn: sqlite3.Connection) -> None:
    add_player(conn, 1, "Alice", "alice")
    soft_remove_player(conn, 1)
    assert rename_player(conn, 1, "Alice Renamed") is None


def test_set_player_photo_round_trips_and_clears(conn: sqlite3.Connection) -> None:
    add_player(conn, 1, "Alice", "alice")
    assert list_active_players(conn)[0].photo_file_id is None
    assert set_player_photo(conn, 1, "FILEID123") == "Alice"
    assert list_active_players(conn)[0].photo_file_id == "FILEID123"
    # None clears it back to the text-prompt fallback.
    assert set_player_photo(conn, 1, None) == "Alice"
    assert list_active_players(conn)[0].photo_file_id is None


def test_set_player_photo_works_for_ghost(conn: sqlite3.Connection) -> None:
    ghost = add_ghost_player(conn, "Late Joiner")
    assert set_player_photo(conn, ghost.telegram_id, "GHOSTFILE") == "Late Joiner"
    stored = next(p for p in list_active_players(conn) if p.telegram_id == ghost.telegram_id)
    assert stored.photo_file_id == "GHOSTFILE"


def test_set_player_photo_unknown_or_inactive_returns_none(conn: sqlite3.Connection) -> None:
    assert set_player_photo(conn, 999, "X") is None
    add_player(conn, 1, "Alice", "alice")
    soft_remove_player(conn, 1)
    assert set_player_photo(conn, 1, "X") is None


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
    assert row["in_pool"] == 1
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


# ----- dont_know_stats (now off score_skips) -----


def _skip(conn: sqlite3.Connection, voter: int, player: int, indicator: str) -> None:
    conn.execute(
        "INSERT INTO score_skips (voter_id, player_id, indicator) VALUES (?, ?, ?)",
        (voter, player, indicator),
    )
    conn.commit()


def _score(conn: sqlite3.Connection, voter: int, player: int, indicator: str, s: int = 3) -> None:
    conn.execute(
        "INSERT INTO scores (voter_id, player_id, indicator, score) VALUES (?, ?, ?, ?)",
        (voter, player, indicator, s),
    )
    conn.commit()


def test_dont_know_stats_per_player(conn: sqlite3.Connection) -> None:
    for tid in (1, 2, 3):
        add_player(conn, tid, f"P{tid}", f"p{tid}")
    add_player(conn, 4, "P4", "p4")
    soft_remove_player(conn, 4)  # inactive — excluded
    # Player 1: 3 skips + 4 scores received → dk 3, total 7.
    for ind in ("attack", "receive", "block"):
        _skip(conn, 2, 1, ind)
    for ind in ("attack", "receive", "block", "setting"):
        _score(conn, 3, 1, ind)
    # Player 3: 1 skip + 1 score → dk 1, total 2.
    _skip(conn, 1, 3, "attack")
    _score(conn, 2, 3, "attack")
    # Player 2: 1 score, no skips → dk 0.
    _score(conn, 1, 2, "attack")

    stats = dont_know_stats(conn)
    by_id = {s.telegram_id: s for s in stats}
    assert set(by_id) == {1, 2, 3}
    assert by_id[1].dk_count == 3
    assert by_id[1].total == 7
    assert by_id[1].dk_rate == 3 / 7
    assert by_id[3].dk_count == 1
    assert by_id[3].total == 2
    rates = [s.dk_rate for s in stats]
    assert rates == sorted(rates, reverse=True)


def test_dont_know_stats_zero_total_is_zero_rate(conn: sqlite3.Connection) -> None:
    add_player(conn, 1, "Lonely", "lonely")
    stats = dont_know_stats(conn)
    assert stats[0].dk_count == 0
    assert stats[0].total == 0
    assert stats[0].dk_rate == 0.0


# ----- ghost players -----


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


# ----- link_ghost_player (scores model) -----


def _score_row(conn: sqlite3.Connection, voter: int, player: int, indicator: str) -> sqlite3.Row:
    return conn.execute(
        "SELECT score FROM scores WHERE voter_id=? AND player_id=? AND indicator=?",
        (voter, player, indicator),
    ).fetchone()


def test_link_ghost_remaps_ghost_as_player_and_as_voter(conn: sqlite3.Connection) -> None:
    add_player(conn, 5, "Five", "five")
    ghost = add_ghost_player(conn, "Ghost")
    g = ghost.telegram_id
    _score(conn, 5, g, "attack", 4)  # ghost scored ON by voter 5
    _score(conn, g, 5, "block", 2)  # ghost scored player 5 (ghost as voter)
    result = link_ghost_player(conn, ghost_id=g, real_id=10, username="ten", display_name="Ten")
    assert _score_row(conn, 5, 10, "attack")["score"] == 4
    assert _score_row(conn, 10, 5, "block")["score"] == 2
    assert result.score_rows == 2
    assert conn.execute("SELECT 1 FROM players WHERE telegram_id=?", (g,)).fetchone() is None
    real = conn.execute("SELECT is_ghost, active FROM players WHERE telegram_id=10").fetchone()
    assert real["is_ghost"] == 0 and real["active"] == 1


def test_link_ghost_merge_keeps_newest_score(conn: sqlite3.Connection) -> None:
    add_player(conn, 5, "Five", "five")
    add_player(conn, 10, "Ten", "ten")
    ghost = add_ghost_player(conn, "Ghost")
    g = ghost.telegram_id
    # attack: existing real score is OLDER → ghost's newer score wins.
    conn.execute(
        "INSERT INTO scores (voter_id, player_id, indicator, score, updated_at) "
        "VALUES (5, 10, 'attack', 2, '2020-01-01 00:00:00')"
    )
    conn.execute(
        "INSERT INTO scores (voter_id, player_id, indicator, score, updated_at) "
        "VALUES (5, ?, 'attack', 4, '2030-01-01 00:00:00')",
        (g,),
    )
    # receive: existing real score is NEWER → ghost's older score must NOT overwrite.
    conn.execute(
        "INSERT INTO scores (voter_id, player_id, indicator, score, updated_at) "
        "VALUES (5, 10, 'receive', 3, '2030-01-01 00:00:00')"
    )
    conn.execute(
        "INSERT INTO scores (voter_id, player_id, indicator, score, updated_at) "
        "VALUES (5, ?, 'receive', 1, '2020-01-01 00:00:00')",
        (g,),
    )
    conn.commit()
    link_ghost_player(conn, ghost_id=g, real_id=10, username=None, display_name="Ten")
    assert _score_row(conn, 5, 10, "attack")["score"] == 4  # newer ghost score won
    assert _score_row(conn, 5, 10, "receive")["score"] == 3  # newer existing kept


def test_link_ghost_drops_self_score(conn: sqlite3.Connection) -> None:
    add_player(conn, 10, "Ten", "ten")
    ghost = add_ghost_player(conn, "Ghost")
    g = ghost.telegram_id
    # The real account (10) scored the ghost; after merge that becomes 10→10 → drop.
    _score(conn, 10, g, "attack", 3)
    link_ghost_player(conn, ghost_id=g, real_id=10, username=None, display_name="Ten")
    assert conn.execute("SELECT COUNT(*) AS n FROM scores").fetchone()["n"] == 0


def test_link_ghost_remaps_skips_and_drops_self(conn: sqlite3.Connection) -> None:
    add_player(conn, 5, "Five", "five")
    add_player(conn, 10, "Ten", "ten")
    ghost = add_ghost_player(conn, "Ghost")
    g = ghost.telegram_id
    _skip(conn, 5, g, "attack")  # remaps to (5, 10)
    _skip(conn, 10, g, "block")  # becomes self (10,10) → drop
    link_ghost_player(conn, ghost_id=g, real_id=10, username=None, display_name="Ten")
    assert (
        conn.execute(
            "SELECT 1 FROM score_skips WHERE voter_id=5 AND player_id=10 AND indicator='attack'"
        ).fetchone()
        is not None
    )
    assert conn.execute("SELECT COUNT(*) AS n FROM score_skips").fetchone()["n"] == 1


def test_link_ghost_migrates_ratings_rsvps_attendance(conn: sqlite3.Connection) -> None:
    ghost = add_ghost_player(conn, "Ghost")
    g = ghost.telegram_id
    conn.execute(
        "INSERT INTO player_ratings (telegram_id, indicator, score, vote_count, calibrated) "
        "VALUES (?, 'attack', 1.5, 5, 1)",
        (g,),
    )
    conn.execute("INSERT INTO sessions (id, session_date) VALUES (1, '2026-06-08')")
    conn.execute(
        "INSERT INTO rsvps (session_id, telegram_id, status, locked_in) VALUES (1, ?, 'yes', 1)",
        (g,),
    )
    conn.execute(
        "INSERT INTO attendance (session_id, telegram_id, was_attendee) VALUES (1, ?, 1)", (g,)
    )
    conn.commit()
    result = link_ghost_player(conn, ghost_id=g, real_id=10, username="ten", display_name="Ten")
    assert (
        conn.execute(
            "SELECT 1 FROM player_ratings WHERE telegram_id=10 AND indicator='attack'"
        ).fetchone()
        is not None
    )
    assert conn.execute("SELECT 1 FROM rsvps WHERE session_id=1 AND telegram_id=10").fetchone()
    assert (
        conn.execute("SELECT 1 FROM attendance WHERE session_id=1 AND telegram_id=10").fetchone()
        is not None
    )
    assert (
        conn.execute(
            "SELECT COUNT(*) AS n FROM player_ratings WHERE telegram_id=?", (g,)
        ).fetchone()["n"]
        == 0
    )
    assert result.ratings == 1
    assert result.rsvps == 1
    assert result.attendance == 1


def test_link_ghost_creates_real_player_if_missing(conn: sqlite3.Connection) -> None:
    add_player(conn, 5, "Five", "five")
    ghost = add_ghost_player(conn, "Ghost")
    g = ghost.telegram_id
    _score(conn, 5, g, "attack", 4)
    # real_id 10 does not exist yet → link must create it.
    link_ghost_player(conn, ghost_id=g, real_id=10, username="ten", display_name="Ten")
    assert conn.execute("SELECT 1 FROM players WHERE telegram_id=10").fetchone() is not None
