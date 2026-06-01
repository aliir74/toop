from __future__ import annotations

import sqlite3
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, MagicMock

import pytest

from toop.handlers.health import (
    build_coverage,
    build_health_rows,
    format_health,
    handle_coverage,
    handle_health,
)
from toop.players import add_player


@pytest.fixture(autouse=True)
def patch_admin(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("toop.admin.settings", MagicMock(ADMIN_TELEGRAM_ID=42))


def _admin_update() -> MagicMock:
    u = MagicMock()
    u.effective_user = MagicMock(id=42)
    msg = MagicMock()
    msg.reply_text = AsyncMock()
    u.effective_message = msg
    return u


def _ctx(conn: sqlite3.Connection) -> MagicMock:
    ctx = MagicMock()
    ctx.bot_data = {"conn": conn}
    return ctx


def _answered(
    conn: sqlite3.Connection, voter_id: int, pa: int, pb: int, axis: str, days_ago: int = 0
) -> None:
    ts = datetime.now(UTC) - timedelta(days=days_ago)
    conn.execute(
        "INSERT INTO answered_prompts (voter_id, player_a, player_b, axis, answered_at) "
        "VALUES (?, ?, ?, ?, ?)",
        (voter_id, pa, pb, axis, ts.strftime("%Y-%m-%d %H:%M:%S")),
    )
    conn.commit()


def test_health_orders_never_voted_first(conn: sqlite3.Connection) -> None:
    add_player(conn, 1, "Alice", "alice")
    add_player(conn, 2, "Bob", "bob")
    add_player(conn, 3, "Carol", "carol")
    _answered(conn, 1, 2, 3, "attack", days_ago=2)
    _answered(conn, 3, 1, 2, "attack", days_ago=14)
    rows = build_health_rows(conn)
    # Bob: never voted, should be first
    assert rows[0]["display_name"] == "Bob"
    # Carol: 14 days ago, before Alice (2 days ago)
    assert rows[1]["display_name"] == "Carol"
    assert rows[2]["display_name"] == "Alice"


def test_health_lifetime_and_30d_counts(conn: sqlite3.Connection) -> None:
    add_player(conn, 1, "Alice", "alice")
    add_player(conn, 2, "Bob", "bob")
    add_player(conn, 3, "Carol", "carol")
    # Alice has 1 recent + 1 old, Bob has 0
    _answered(conn, 1, 2, 3, "attack", days_ago=5)
    _answered(conn, 1, 2, 3, "defense", days_ago=45)
    rows = build_health_rows(conn)
    alice = next(r for r in rows if r["display_name"] == "Alice")
    assert alice["lifetime"] == 2
    assert alice["last_30d"] == 1


def test_health_no_vote_outcomes_exposed(conn: sqlite3.Connection) -> None:
    """The health query touches answered_prompts (counts only) — never vote_aggregates."""
    add_player(conn, 1, "Alice", "alice")
    add_player(conn, 2, "Bob", "bob")
    conn.execute(
        "INSERT INTO vote_aggregates (player_a, player_b, axis, a_wins, b_wins) "
        "VALUES (1, 2, 'attack', 999, 0)"
    )
    conn.commit()
    rows = build_health_rows(conn)
    serialized = format_health(rows)
    assert "999" not in serialized  # no win count leakage


async def test_health_handler_replies(conn: sqlite3.Connection) -> None:
    add_player(conn, 1, "Alice", "alice")
    update = _admin_update()
    await handle_health(update, _ctx(conn))
    reply = update.effective_message.reply_text.await_args.args[0]
    assert "Alice" in reply


def test_coverage_orders_undersampled_first(conn: sqlite3.Connection) -> None:
    add_player(conn, 1, "Alice", "alice")
    add_player(conn, 2, "Bob", "bob")
    add_player(conn, 3, "Carol", "carol")
    # (1,2) saturated; (1,3) some; (2,3) empty
    for axis, n in (("attack", 10), ("defense", 10), ("setting", 10)):
        conn.execute(
            "INSERT INTO vote_aggregates (player_a, player_b, axis, a_wins, b_wins) "
            "VALUES (?, ?, ?, ?, ?)",
            (1, 2, axis, n // 2, n - n // 2),
        )
    conn.execute(
        "INSERT INTO vote_aggregates (player_a, player_b, axis, a_wins, b_wins) "
        "VALUES (1, 3, 'attack', 1, 1)"
    )
    conn.commit()
    text = build_coverage(conn, limit=10)
    lines = text.split("\n")
    # First gap line should be the empty pair (Bob vs Carol = 2-3)
    assert "Bob vs Carol" in lines[1] or "Carol vs Bob" in lines[1]


async def test_coverage_handler_replies(conn: sqlite3.Connection) -> None:
    add_player(conn, 1, "Alice", "alice")
    add_player(conn, 2, "Bob", "bob")
    update = _admin_update()
    await handle_coverage(update, _ctx(conn))
    reply = update.effective_message.reply_text.await_args.args[0]
    assert "Coverage" in reply or "Not enough" in reply


# ----- branch coverage additions -----

from toop.handlers.health import _calibration_marker, _conn, _humanize_age  # noqa: E402


def test_conn_raises_when_missing() -> None:
    ctx = MagicMock()
    ctx.bot_data = {}
    with pytest.raises(RuntimeError, match="DB connection missing"):
        _conn(ctx)


def test_humanize_age_variants() -> None:
    now = datetime.now(UTC)
    assert _humanize_age(None) == "never"
    assert _humanize_age("") == "never"
    assert _humanize_age("garbage") == "?"
    assert _humanize_age((now - timedelta(days=3)).strftime("%Y-%m-%d %H:%M:%S")) == "3d ago"
    assert _humanize_age((now - timedelta(hours=2)).strftime("%Y-%m-%d %H:%M:%S")) == "2h ago"
    assert _humanize_age((now - timedelta(minutes=1)).strftime("%Y-%m-%d %H:%M:%S")) == "today"


def test_calibration_marker_variants() -> None:
    assert _calibration_marker(False, 0) == "✓"
    assert _calibration_marker(True, 5) == "⚠"
    assert _calibration_marker(True, 0) == "✗"


def test_format_health_empty() -> None:
    assert format_health([]) == "Roster is empty."


def test_build_health_rows_handles_malformed_timestamp(conn: sqlite3.Connection) -> None:
    add_player(conn, 1, "Alice", "alice")
    add_player(conn, 2, "Bob", "bob")
    add_player(conn, 3, "Carol", "carol")
    conn.execute(
        "INSERT INTO answered_prompts (voter_id, player_a, player_b, axis, answered_at) "
        "VALUES (1, 2, 3, 'attack', 'not-a-date')"
    )
    conn.commit()
    rows = build_health_rows(conn)
    alice = next(r for r in rows if r["display_name"] == "Alice")
    assert alice["last_voted_human"] == "?"


async def test_health_returns_without_message(conn: sqlite3.Connection) -> None:
    u = MagicMock()
    u.effective_user = MagicMock(id=42)
    u.effective_message = None
    await handle_health(u, _ctx(conn))


def test_build_coverage_too_few_players(conn: sqlite3.Connection) -> None:
    add_player(conn, 1, "Alice", "alice")
    assert "Not enough players" in build_coverage(conn, limit=10)


async def test_coverage_returns_without_message(conn: sqlite3.Connection) -> None:
    u = MagicMock()
    u.effective_user = MagicMock(id=42)
    u.effective_message = None
    await handle_coverage(u, _ctx(conn))
