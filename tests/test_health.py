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
from toop.rating import INDICATORS


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


def _score(
    conn: sqlite3.Connection, voter: int, player: int, indicator: str, days_ago: int = 0
) -> None:
    ts = datetime.now(UTC) - timedelta(days=days_ago)
    conn.execute(
        "INSERT INTO scores (voter_id, player_id, indicator, score, updated_at) "
        "VALUES (?, ?, ?, 3, ?)",
        (voter, player, indicator, ts.strftime("%Y-%m-%d %H:%M:%S")),
    )
    conn.commit()


def test_health_orders_never_voted_first(conn: sqlite3.Connection) -> None:
    add_player(conn, 1, "Alice", "alice")
    add_player(conn, 2, "Bob", "bob")
    add_player(conn, 3, "Carol", "carol")
    _score(conn, 1, 2, "attack", days_ago=2)  # Alice voted 2d ago
    _score(conn, 3, 1, "attack", days_ago=14)  # Carol voted 14d ago
    rows = build_health_rows(conn)
    assert rows[0]["display_name"] == "Bob"  # never voted
    assert rows[1]["display_name"] == "Carol"
    assert rows[2]["display_name"] == "Alice"


def test_health_lifetime_and_30d_counts(conn: sqlite3.Connection) -> None:
    add_player(conn, 1, "Alice", "alice")
    add_player(conn, 2, "Bob", "bob")
    _score(conn, 1, 2, "attack", days_ago=5)
    _score(conn, 1, 2, "receive", days_ago=45)
    rows = build_health_rows(conn)
    alice = next(r for r in rows if r["display_name"] == "Alice")
    assert alice["lifetime"] == 2
    assert alice["last_30d"] == 1


def test_health_pending_counts_remaining_targets(conn: sqlite3.Connection) -> None:
    add_player(conn, 1, "Alice", "alice")
    add_player(conn, 2, "Bob", "bob")
    # Alice can rate Bob on 6 indicators; she's done 1 → 5 remaining.
    _score(conn, 1, 2, "attack")
    rows = build_health_rows(conn)
    alice = next(r for r in rows if r["display_name"] == "Alice")
    assert alice["pending"] == len(INDICATORS) - 1


async def test_health_handler_replies(conn: sqlite3.Connection) -> None:
    add_player(conn, 1, "Alice", "alice")
    update = _admin_update()
    await handle_health(update, _ctx(conn))
    reply = update.effective_message.reply_text.await_args.args[0]
    assert "Alice" in reply


def test_coverage_orders_least_rated_first(conn: sqlite3.Connection) -> None:
    add_player(conn, 1, "Alice", "alice")
    add_player(conn, 2, "Bob", "bob")
    add_player(conn, 3, "Carol", "carol")
    # Alice & Bob get rated; Carol gets nothing → Carol is the top gap.
    for ind in INDICATORS:
        _score(conn, 2, 1, ind)  # Alice rated
        _score(conn, 1, 2, ind)  # Bob rated
    text = build_coverage(conn, limit=10)
    lines = text.split("\n")
    assert "Carol" in lines[1]


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
    conn.execute(
        "INSERT INTO scores (voter_id, player_id, indicator, score, updated_at) "
        "VALUES (1, 2, 'attack', 3, 'not-a-date')"
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


def test_build_coverage_empty_roster(conn: sqlite3.Connection) -> None:
    assert "Not enough players" in build_coverage(conn, limit=10)


async def test_coverage_returns_without_message(conn: sqlite3.Connection) -> None:
    u = MagicMock()
    u.effective_user = MagicMock(id=42)
    u.effective_message = None
    await handle_coverage(u, _ctx(conn))
