"""Targeted tests for the last single-line/branch gaps across core-logic modules."""

from __future__ import annotations

import sqlite3
from unittest.mock import MagicMock

import pytest

from toop.balance import _confidence_from_ratio, _try_setter_swap, compute_metrics
from toop.db import init_db
from toop.players import _fetch_one, add_player
from toop.rating import fit_bradley_terry, refresh_ratings
from toop.sessions import set_session_status
from toop.snapshots import write_attendance
from toop.voting_queue import insert_priority_prompt, peek_next_prompt, record_vote

WEIGHTS = (0.4, 0.4, 0.2)


# --- balance.py ---


def test_confidence_from_ratio_medium() -> None:
    assert _confidence_from_ratio(0.6) == "medium"


def test_try_setter_swap_donor_is_team_b() -> None:
    # team_a has no top setter, team_b has one → donor=team_b path, swap applied.
    new_a, new_b, applied = _try_setter_swap([1], [2], {2}, {1: 0.0, 2: 1.0})
    assert applied is True
    assert new_a == [2] and new_b == [1]


def test_try_setter_swap_no_recipients() -> None:
    # Recipient team empty → nothing to swap into.
    new_a, new_b, applied = _try_setter_swap([], [2], {2}, {2: 1.0})
    assert applied is False
    assert new_a == [] and new_b == [2]


def test_compute_metrics_empty_teams(conn: sqlite3.Connection) -> None:
    metrics = compute_metrics(conn, [], [], WEIGHTS)
    assert metrics.team_a_total == 0.0
    assert metrics.team_b_total == 0.0
    assert metrics.abs_delta == 0.0


# --- db.py ---


def test_init_db_skips_when_schema_missing(
    conn: sqlite3.Connection, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake_path = MagicMock()
    fake_path.return_value.parent.__truediv__.return_value.exists.return_value = False
    monkeypatch.setattr("toop.db.Path", fake_path)
    assert init_db(conn) is None


# --- players.py ---


def test_fetch_one_raises_when_missing(conn: sqlite3.Connection) -> None:
    with pytest.raises(LookupError, match="not found"):
        _fetch_one(conn, 999)


# --- rating.py ---


def test_fit_bradley_terry_skips_unknown_ids() -> None:
    # Aggregate references id 99 which isn't in player_ids → skipped, no crash.
    scores = fit_bradley_terry({(1, 99): (1, 0)}, player_ids=[1])
    assert set(scores) == {1}


def test_refresh_ratings_empty_roster(conn: sqlite3.Connection) -> None:
    assert refresh_ratings(conn, 15) == 0


# --- sessions.py ---


def test_set_session_status_rejects_invalid(conn: sqlite3.Connection) -> None:
    with pytest.raises(ValueError, match="invalid status"):
        set_session_status(conn, 1, "bogus")


# --- snapshots.py ---


def test_write_attendance_no_snapshot_returns_zero(conn: sqlite3.Connection) -> None:
    assert write_attendance(conn, 999) == 0


# --- voting_queue.py ---


def test_peek_next_prompt_empty_queue(conn: sqlite3.Connection) -> None:
    assert peek_next_prompt(conn, 1) is None


def test_record_vote_rejects_bad_winner(conn: sqlite3.Connection) -> None:
    add_player(conn, 1, "Alice", "alice")
    add_player(conn, 2, "Bob", "bob")
    with pytest.raises(ValueError, match="winner must be"):
        record_vote(conn, 99, 1, 2, "attack", "x")


def test_insert_priority_prompt_skips_self_pair(conn: sqlite3.Connection) -> None:
    add_player(conn, 1, "Alice", "alice")
    add_player(conn, 2, "Bob", "bob")
    # voter 1 is one of the pair → must not insert a prompt.
    insert_priority_prompt(conn, 1, 1, 2, "attack")
    # degenerate pair (player_a == player_b) → must not insert either.
    insert_priority_prompt(conn, 99, 1, 1, "attack")
    count = conn.execute("SELECT COUNT(*) AS n FROM pending_prompts").fetchone()["n"]
    assert count == 0
