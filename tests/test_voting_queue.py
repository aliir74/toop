from __future__ import annotations

import sqlite3
from datetime import UTC, datetime, timedelta

from toop.players import add_player
from toop.voting_queue import (
    add_snooze,
    insert_priority_prompt,
    peek_next_prompt,
    refill_queue,
    remove_prompt,
)


def _seed_players(conn: sqlite3.Connection, n: int) -> list[int]:
    ids = []
    for i in range(1, n + 1):
        add_player(conn, i, f"P{i}", f"p{i}")
        ids.append(i)
    return ids


def test_refill_for_voter_with_no_history_returns_queue_depth(conn: sqlite3.Connection) -> None:
    _seed_players(conn, 5)
    inserted = refill_queue(conn, voter_id=1, queue_depth=5)
    assert inserted == 5
    rows = conn.execute("SELECT COUNT(*) AS n FROM pending_prompts WHERE voter_id=1").fetchone()
    assert rows["n"] == 5


def test_refill_idempotent_when_full(conn: sqlite3.Connection) -> None:
    _seed_players(conn, 5)
    refill_queue(conn, voter_id=1, queue_depth=5)
    inserted = refill_queue(conn, voter_id=1, queue_depth=5)
    assert inserted == 0


def test_refill_excludes_self_pairs(conn: sqlite3.Connection) -> None:
    _seed_players(conn, 4)
    refill_queue(conn, voter_id=1, queue_depth=20)
    rows = conn.execute(
        "SELECT player_a, player_b FROM pending_prompts WHERE voter_id=1"
    ).fetchall()
    for r in rows:
        assert 1 not in (r["player_a"], r["player_b"])


def test_refill_excludes_already_answered(conn: sqlite3.Connection) -> None:
    _seed_players(conn, 3)
    conn.execute(
        "INSERT INTO answered_prompts (voter_id, player_a, player_b, axis) "
        "VALUES (1, 2, 3, 'attack')"
    )
    conn.commit()
    refill_queue(conn, voter_id=1, queue_depth=20)
    rows = conn.execute(
        "SELECT 1 FROM pending_prompts WHERE voter_id=1 "
        "AND player_a=2 AND player_b=3 AND axis='attack'"
    ).fetchall()
    assert rows == []


def test_refill_filters_snoozed_axes(conn: sqlite3.Connection) -> None:
    _seed_players(conn, 4)
    until = (datetime.now(UTC) + timedelta(days=3)).strftime("%Y-%m-%d %H:%M:%S")
    conn.execute(
        "INSERT INTO snoozes (voter_id, axis, snoozed_until) VALUES (1, 'setting', ?)",
        (until,),
    )
    conn.commit()
    refill_queue(conn, voter_id=1, queue_depth=20)
    axes = {
        r["axis"]
        for r in conn.execute(
            "SELECT DISTINCT axis FROM pending_prompts WHERE voter_id=1"
        ).fetchall()
    }
    assert "setting" not in axes
    assert axes == {"attack", "defense"}


def test_refill_voter_with_all_answered_returns_zero(conn: sqlite3.Connection) -> None:
    _seed_players(conn, 3)
    pairs = [(2, 3)]
    for pa, pb in pairs:
        for axis in ("attack", "defense", "setting"):
            conn.execute(
                "INSERT INTO answered_prompts (voter_id, player_a, player_b, axis) "
                "VALUES (?, ?, ?, ?)",
                (1, pa, pb, axis),
            )
    conn.commit()
    inserted = refill_queue(conn, voter_id=1, queue_depth=5)
    assert inserted == 0


def test_undersampled_pairs_prioritized(conn: sqlite3.Connection) -> None:
    """Pairs with 0 votes should sort before pairs with many votes."""
    _seed_players(conn, 4)
    # Saturate (2,3) attack with 10 votes; leave others at 0
    conn.execute(
        "INSERT INTO vote_aggregates (player_a, player_b, axis, a_wins, b_wins) "
        "VALUES (2, 3, 'attack', 5, 5)"
    )
    conn.commit()
    refill_queue(conn, voter_id=1, queue_depth=2)
    rows = conn.execute(
        "SELECT player_a, player_b, axis FROM pending_prompts WHERE voter_id=1 "
        "ORDER BY info_gain DESC"
    ).fetchall()
    for r in rows:
        assert not (r["player_a"] == 2 and r["player_b"] == 3 and r["axis"] == "attack")


def test_peek_returns_highest_info_gain(conn: sqlite3.Connection) -> None:
    _seed_players(conn, 3)
    conn.execute(
        "INSERT INTO pending_prompts (voter_id, player_a, player_b, axis, info_gain) "
        "VALUES (1, 2, 3, 'attack', 100), (1, 2, 3, 'defense', 5000), (1, 2, 3, 'setting', 10)"
    )
    conn.commit()
    top = peek_next_prompt(conn, voter_id=1)
    assert top is not None
    assert top.axis == "defense"


def test_remove_prompt(conn: sqlite3.Connection) -> None:
    _seed_players(conn, 3)
    refill_queue(conn, voter_id=1, queue_depth=5)
    before = conn.execute("SELECT COUNT(*) AS n FROM pending_prompts WHERE voter_id=1").fetchone()[
        "n"
    ]
    p = peek_next_prompt(conn, voter_id=1)
    assert p is not None
    remove_prompt(conn, 1, p.player_a, p.player_b, p.axis)
    after = conn.execute("SELECT COUNT(*) AS n FROM pending_prompts WHERE voter_id=1").fetchone()[
        "n"
    ]
    assert after == before - 1


def test_add_snooze_clears_pending_for_axis(conn: sqlite3.Connection) -> None:
    _seed_players(conn, 4)
    refill_queue(conn, voter_id=1, queue_depth=20)
    add_snooze(conn, voter_id=1, axis="setting")
    rows = conn.execute(
        "SELECT axis FROM pending_prompts WHERE voter_id=1 AND axis='setting'"
    ).fetchall()
    assert rows == []


def test_priority_prompt_normalizes_pair_order(conn: sqlite3.Connection) -> None:
    _seed_players(conn, 3)
    insert_priority_prompt(conn, voter_id=1, player_a=3, player_b=2, axis="attack")
    row = conn.execute("SELECT player_a, player_b FROM pending_prompts WHERE voter_id=1").fetchone()
    assert row["player_a"] == 2
    assert row["player_b"] == 3


def test_priority_prompt_skips_voter_in_pair(conn: sqlite3.Connection) -> None:
    _seed_players(conn, 3)
    insert_priority_prompt(conn, voter_id=1, player_a=1, player_b=2, axis="attack")
    rows = conn.execute("SELECT COUNT(*) AS n FROM pending_prompts WHERE voter_id=1").fetchone()
    assert rows["n"] == 0
