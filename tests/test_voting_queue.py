from __future__ import annotations

import sqlite3
from datetime import UTC, datetime, timedelta

import pytest

from toop.players import add_player
from toop.rating import INDICATORS
from toop.voting_queue import (
    ScoreTarget,
    record_score,
    record_skip,
    select_next_score_target,
)


def _seed_players(conn: sqlite3.Connection, n: int) -> list[int]:
    ids = []
    for i in range(1, n + 1):
        add_player(conn, i, f"P{i}", f"p{i}")
        ids.append(i)
    return ids


def test_selector_returns_a_target(conn: sqlite3.Connection) -> None:
    _seed_players(conn, 3)
    target = select_next_score_target(conn, voter_id=1)
    assert isinstance(target, ScoreTarget)
    assert target.player_id in (2, 3)
    assert target.indicator in INDICATORS


def test_selector_never_targets_self(conn: sqlite3.Connection) -> None:
    _seed_players(conn, 3)
    seen: set[int] = set()
    # Score everything to walk the whole space; self must never appear.
    while (t := select_next_score_target(conn, voter_id=1)) is not None:
        seen.add(t.player_id)
        record_score(conn, 1, t.player_id, t.indicator, 3)
    assert 1 not in seen
    assert seen == {2, 3}


def test_selector_excludes_scored_and_skipped(conn: sqlite3.Connection) -> None:
    _seed_players(conn, 2)
    record_score(conn, 1, 2, "attack", 4)
    record_skip(conn, 1, 2, "receive")
    remaining = set()
    while (t := select_next_score_target(conn, voter_id=1)) is not None:
        remaining.add(t.indicator)
        record_score(conn, 1, 2, t.indicator, 3)
    assert "attack" not in remaining
    assert "receive" not in remaining


def test_selector_returns_none_when_exhausted(conn: sqlite3.Connection) -> None:
    _seed_players(conn, 2)
    for ind in INDICATORS:
        record_score(conn, 1, 2, ind, 3)
    assert select_next_score_target(conn, voter_id=1) is None


def test_exclude_player_surfaces_a_different_player(conn: sqlite3.Connection) -> None:
    _seed_players(conn, 3)
    # Default lowest-id-first → player 2. Excluding 2 surfaces player 3.
    assert select_next_score_target(conn, voter_id=1).player_id == 2
    assert select_next_score_target(conn, voter_id=1, exclude_player=2).player_id == 3


def test_undersampled_player_surfaces_first(conn: sqlite3.Connection) -> None:
    _seed_players(conn, 3)
    # Saturate player 2 on every indicator from other voters; player 3 stays at 0.
    for ind in INDICATORS:
        record_score(conn, 3, 2, ind, 4)
    target = select_next_score_target(conn, voter_id=1)
    assert target.player_id == 3


def test_selector_excludes_disabled_pool_player(conn: sqlite3.Connection) -> None:
    _seed_players(conn, 3)
    conn.execute("UPDATE players SET in_pool=0 WHERE telegram_id=2")
    conn.commit()
    seen = set()
    while (t := select_next_score_target(conn, voter_id=1)) is not None:
        seen.add(t.player_id)
        record_score(conn, 1, t.player_id, t.indicator, 3)
    assert 2 not in seen


def test_selector_excludes_future_paused_player(conn: sqlite3.Connection) -> None:
    _seed_players(conn, 3)
    future = (datetime.now(UTC) + timedelta(days=3)).strftime("%Y-%m-%d %H:%M:%S")
    conn.execute("UPDATE players SET pool_paused_until=? WHERE telegram_id=2", (future,))
    conn.commit()
    seen = set()
    while (t := select_next_score_target(conn, voter_id=1)) is not None:
        seen.add(t.player_id)
        record_score(conn, 1, t.player_id, t.indicator, 3)
    assert 2 not in seen


def test_selector_includes_expired_pause(conn: sqlite3.Connection) -> None:
    _seed_players(conn, 3)
    past = (datetime.now(UTC) - timedelta(days=1)).strftime("%Y-%m-%d %H:%M:%S")
    conn.execute("UPDATE players SET pool_paused_until=? WHERE telegram_id=2", (past,))
    conn.commit()
    seen = set()
    while (t := select_next_score_target(conn, voter_id=1)) is not None:
        seen.add(t.player_id)
        record_score(conn, 1, t.player_id, t.indicator, 3)
    assert 2 in seen


def test_record_score_inserts(conn: sqlite3.Connection) -> None:
    _seed_players(conn, 2)
    record_score(conn, 1, 2, "attack", 4)
    row = conn.execute(
        "SELECT score FROM scores WHERE voter_id=1 AND player_id=2 AND indicator='attack'"
    ).fetchone()
    assert row["score"] == 4


def test_voter_count_interleaves_filler_picks(conn: sqlite3.Connection) -> None:
    """When multiple players are tied on global total, the one this voter has
    rated fewer times surfaces next — so the voter sees fresh faces instead of
    cycling the same low-id player as the filler between exclude_player rounds."""
    _seed_players(conn, 4)  # voter=1, targets=2,3,4 (ids ascending)
    # Voter 1 scores player 2 and 3 each once (attack). Both now have voter_count=1.
    # Player 4 still has voter_count=0.
    record_score(conn, voter_id=1, player_id=2, indicator="attack", score=3)
    record_score(conn, voter_id=1, player_id=3, indicator="attack", score=3)
    # Excluding player 3: next should be player 4 (voter_count=0) not player 2 (voter_count=1).
    t = select_next_score_target(conn, voter_id=1, exclude_player=3)
    assert t is not None
    assert t.player_id == 4, (
        "voter_count tiebreaker should prefer player 4 (unvisited by this voter) "
        f"over player 2 (already scored once); got player_id={t.player_id}"
    )


def test_record_score_is_editable(conn: sqlite3.Connection) -> None:
    _seed_players(conn, 2)
    record_score(conn, 1, 2, "attack", 4)
    record_score(conn, 1, 2, "attack", 1)
    rows = conn.execute(
        "SELECT score FROM scores WHERE voter_id=1 AND player_id=2 AND indicator='attack'"
    ).fetchall()
    assert len(rows) == 1
    assert rows[0]["score"] == 1


def test_record_score_clears_prior_skip(conn: sqlite3.Connection) -> None:
    _seed_players(conn, 2)
    record_skip(conn, 1, 2, "attack")
    record_score(conn, 1, 2, "attack", 3)
    skip = conn.execute(
        "SELECT 1 FROM score_skips WHERE voter_id=1 AND player_id=2 AND indicator='attack'"
    ).fetchone()
    assert skip is None


def test_record_score_rejects_bad_indicator(conn: sqlite3.Connection) -> None:
    _seed_players(conn, 2)
    with pytest.raises(ValueError):
        record_score(conn, 1, 2, "bogus", 3)


def test_record_score_rejects_out_of_range(conn: sqlite3.Connection) -> None:
    _seed_players(conn, 2)
    with pytest.raises(ValueError):
        record_score(conn, 1, 2, "attack", 6)


def test_record_score_rejects_self(conn: sqlite3.Connection) -> None:
    _seed_players(conn, 2)
    with pytest.raises(ValueError):
        record_score(conn, 1, 1, "attack", 3)


def test_record_skip_inserts_dedupe(conn: sqlite3.Connection) -> None:
    _seed_players(conn, 2)
    record_skip(conn, 1, 2, "attack")
    row = conn.execute(
        "SELECT 1 FROM score_skips WHERE voter_id=1 AND player_id=2 AND indicator='attack'"
    ).fetchone()
    assert row is not None


def test_record_skip_rejects_bad_indicator(conn: sqlite3.Connection) -> None:
    _seed_players(conn, 2)
    with pytest.raises(ValueError):
        record_skip(conn, 1, 2, "bogus")


def test_record_skip_rejects_self(conn: sqlite3.Connection) -> None:
    _seed_players(conn, 2)
    with pytest.raises(ValueError):
        record_skip(conn, 1, 1, "attack")
